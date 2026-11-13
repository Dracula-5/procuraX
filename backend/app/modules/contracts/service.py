"""Contracts: policy facts for a purchase, and the daily expiry and reminder sweep."""

import logging
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import system_session, utcnow
from app.domain.contracts import (
    REMIND_DAYS_BEFORE,
    ContractAction,
    ContractState,
    ContractTerms,
    covering_contract,
    due_for_expiry,
    due_for_reminder,
    transition,
    vendor_contract_status,
)
from app.domain.money import format_money
from app.domain.notifications import EventType
from app.domain.policy.models import ContractFacts
from app.domain.workflow.purchase_request import PRStatus
from app.modules.audit import service as audit
from app.modules.contracts.models import Contract
from app.modules.finance import fx
from app.modules.identity.models import Organization
from app.modules.notifications import outbox
from app.modules.procurement.models import PurchaseRequest
from app.modules.vendors.models import Vendor

logger = logging.getLogger("procurax.contracts")

# Requests that use up a contract's cap: in approval or approved.
CAP_STATUSES = (PRStatus.PENDING_APPROVAL.value, PRStatus.APPROVED.value)


def terms(c: Contract) -> ContractTerms:
    return ContractTerms(
        id=str(c.id),
        number=c.number,
        vendor_id=str(c.vendor_id),
        state=ContractState(c.status),
        starts_on=c.starts_on,
        ends_on=c.ends_on,
        categories=tuple(c.categories or ()),
        value_cap=c.value_cap,
        currency=c.currency,
    )


def local_today(timezone: str, now: datetime | None = None) -> date:
    try:
        tz = ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        tz = ZoneInfo("UTC")
    return (now or utcnow()).astimezone(tz).date()


@dataclass(frozen=True)
class VendorContractPosition:
    status: str | None  # active / expired / none; None = vendor has no contract records (legacy field)
    contract_id: uuid.UUID | None
    facts: ContractFacts | None


async def committed_under(
    session: AsyncSession, contract_id: uuid.UUID, *, exclude_request_id: uuid.UUID | None = None
) -> Decimal:
    query = select(func.coalesce(func.sum(PurchaseRequest.base_amount), 0)).where(
        PurchaseRequest.contract_id == contract_id, PurchaseRequest.status.in_(CAP_STATUSES)
    )
    if exclude_request_id is not None:
        query = query.where(PurchaseRequest.id != exclude_request_id)
    return Decimal(await session.scalar(query) or 0)


async def position(
    session: AsyncSession,
    org: Organization,
    vendor_id: uuid.UUID,
    category: str,
    on: date,
    *,
    exclude_request_id: uuid.UUID | None = None,
) -> VendorContractPosition:
    """The vendor's contract status for this purchase and, when a contract covers it, its cap facts."""
    rows = list(
        await session.scalars(
            select(Contract).where(Contract.org_id == org.id, Contract.vendor_id == vendor_id)
        )
    )
    all_terms = [terms(c) for c in rows]
    status = vendor_contract_status(all_terms, str(vendor_id), category, on)
    covering = covering_contract(all_terms, str(vendor_id), category, on)
    if covering is None:
        return VendorContractPosition(status, None, None)
    contract_id = uuid.UUID(covering.id)
    committed = await committed_under(session, contract_id, exclude_request_id=exclude_request_id)
    cap, convertible = covering.value_cap, True
    if cap is not None:
        cap, _ = await fx.convert(session, org.id, org.base_currency, cap, covering.currency, on)
        convertible = cap is not None
    facts = ContractFacts(
        number=covering.number,
        value_cap=cap,
        committed=committed,
        cap_currency=covering.currency,
        cap_convertible=convertible,
    )
    return VendorContractPosition(status, contract_id, facts)


# --- Sweep -------------------------------------------------------------------------------------


async def sweep(session: AsyncSession, *, now: datetime | None = None) -> dict[str, int]:
    """Expire active contracts past their end date and send one reminder 30 days before the end.

    Runs across tenants in a system session (explicit RLS bypass). Rows are claimed with
    FOR UPDATE SKIP LOCKED, so concurrent workers never double-process a contract; the reminder is
    recorded on the row (expiry_reminded_at) in the same transaction as its outbox event.
    """
    now = now or utcnow()
    horizon = now.date() + timedelta(days=REMIND_DAYS_BEFORE + 1)
    rows = (
        await session.execute(
            select(Contract, Organization.timezone, Vendor.name)
            .join(Organization, Organization.id == Contract.org_id)
            .join(Vendor, Vendor.id == Contract.vendor_id)
            .where(
                Contract.status == ContractState.ACTIVE.value,
                # Only contracts that can be due today in any timezone (± one day around UTC):
                # not yet reminded and inside the reminder window, or about to pass their end date.
                or_(
                    Contract.ends_on <= horizon - timedelta(days=REMIND_DAYS_BEFORE),
                    Contract.expiry_reminded_at.is_(None) & (Contract.ends_on <= horizon),
                ),
            )
            .order_by(Contract.ends_on, Contract.id)
            .with_for_update(of=Contract, skip_locked=True)
        )
    ).all()
    counts = {"contracts_expired": 0, "contracts_reminded": 0}
    for contract, timezone, vendor_name in rows:
        today = local_today(timezone, now)
        t = terms(contract)
        if due_for_expiry(t, today):
            contract.status = transition(ContractState.ACTIVE, ContractAction.EXPIRE).value
            await audit.record(
                session,
                org_id=contract.org_id,
                actor=None,
                actor_type="system",
                action="contract.expired",
                entity_type="contract",
                entity_id=contract.id,
                summary=f"Contract {contract.number} expired on {contract.ends_on.isoformat()}",
                changes={"status": {"before": ContractState.ACTIVE.value, "after": contract.status}},
            )
            counts["contracts_expired"] += 1
        elif due_for_reminder(t, today, reminded=contract.expiry_reminded_at is not None):
            contract.expiry_reminded_at = now
            cap = format_money(contract.value_cap, contract.currency) if contract.value_cap else "—"
            outbox.emit(
                session,
                org_id=contract.org_id,
                event_type=EventType.CONTRACT_EXPIRING,
                aggregate_type="contract",
                aggregate_id=contract.id,
                actor_user_id=None,
                recipient_user_ids=[contract.created_by_id],
                recipient_roles=["procurement_officer"],
                link="/app/contracts",
                contract_number=contract.number,
                title=contract.title,
                vendor=vendor_name,
                ends_on=contract.ends_on.isoformat(),
                days_left=(contract.ends_on - today).days,
                cap=cap,
            )
            counts["contracts_reminded"] += 1
    await session.commit()
    return counts


async def sweep_once() -> dict[str, int]:
    session = system_session()
    try:
        return await sweep(session)
    finally:
        await session.close()
