"""Vendor bank details, payer settings and Zengin transfer-file export of approved payments."""

import base64
import hashlib
import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.deps import DB, requires
from app.core.db import utcnow
from app.core.errors import BusinessRuleViolation, NotFoundError
from app.core.principal import Principal
from app.domain.rbac import Permission
from app.domain.zengin import (
    BankAccount,
    Transfer,
    ZenginError,
    build_transfer_file,
    to_zengin_kana,
    validate_account,
)
from app.modules.audit import service as audit
from app.modules.identity.models import Organization
from app.modules.procurement.records import Invoice, Payment
from app.modules.vendors.models import Vendor

router = APIRouter(tags=["payments"])
Finance = Annotated[Principal, Depends(requires(Permission.PAYMENT_APPROVE))]
VendorManager = Annotated[Principal, Depends(requires(Permission.VENDOR_MANAGE))]
OrgAdmin = Annotated[Principal, Depends(requires(Permission.ORG_MANAGE))]


class BankAccountIn(BaseModel):
    bank_code: str = Field(pattern=r"^\d{4}$")
    bank_name_kana: str = Field(min_length=1, max_length=40)
    branch_code: str = Field(pattern=r"^\d{3}$")
    branch_name_kana: str = Field(min_length=1, max_length=40)
    account_type: str = Field(pattern=r"^[124]$")
    account_number: str = Field(pattern=r"^\d{1,7}$")
    holder_name_kana: str = Field(min_length=1, max_length=60)


class PaymentSettingsIn(BankAccountIn):
    consignor_code: str = Field(pattern=r"^\d{10}$")
    consignor_name_kana: str = Field(min_length=1, max_length=80)


class TransferFileIn(BaseModel):
    transfer_date: date


def _normalise(data: BankAccountIn) -> dict:
    """Validate against Zengin field rules and store names in bank (half-width kana) form."""
    stored = data.model_dump()
    try:
        for key in ("bank_name_kana", "branch_name_kana", "holder_name_kana", "consignor_name_kana"):
            if key in stored:
                stored[key] = to_zengin_kana(stored[key])
        validate_account(_account(stored))
    except ZenginError as exc:
        raise BusinessRuleViolation(str(exc)) from exc
    return stored


def _account(stored: dict) -> BankAccount:
    return BankAccount(
        bank_code=stored["bank_code"],
        bank_name_kana=stored["bank_name_kana"],
        branch_code=stored["branch_code"],
        branch_name_kana=stored["branch_name_kana"],
        account_type=stored["account_type"],
        account_number=stored["account_number"],
        holder_name_kana=stored["holder_name_kana"],
    )


def _masked(stored: dict | None) -> dict | None:
    if stored is None:
        return None
    number = stored["account_number"]
    return {**stored, "account_number": "*" * max(len(number) - 3, 0) + number[-3:]}


@router.put("/vendors/{vendor_id}/bank-account")
async def set_vendor_bank_account(
    vendor_id: uuid.UUID, data: BankAccountIn, session: DB, principal: VendorManager
) -> dict:
    vendor = await session.scalar(
        select(Vendor).where(
            Vendor.id == vendor_id, Vendor.org_id == principal.org_id, Vendor.deleted_at.is_(None)
        )
    )
    if vendor is None:
        raise NotFoundError("Vendor not found")
    stored = _normalise(data)
    vendor.bank_account = stored
    await audit.record(
        session,
        org_id=principal.org_id,
        actor=principal,
        action="vendor.bank_account_set",
        entity_type="vendor",
        entity_id=vendor.id,
        summary=f"Bank account set for {vendor.name}",
        changes={"bank_account": _masked(stored)},
    )
    await session.commit()
    return {"vendor_id": vendor.id, "bank_account": _masked(stored)}


@router.get("/vendors/{vendor_id}/bank-account")
async def get_vendor_bank_account(vendor_id: uuid.UUID, session: DB, principal: VendorManager) -> dict:
    stored = await session.scalar(
        select(Vendor.bank_account).where(Vendor.id == vendor_id, Vendor.org_id == principal.org_id)
    )
    return {"vendor_id": vendor_id, "bank_account": _masked(stored)}


@router.put("/organizations/current/payment-settings")
async def set_payment_settings(data: PaymentSettingsIn, session: DB, principal: OrgAdmin) -> dict:
    org = await session.get(Organization, principal.org_id)
    if org is None:
        raise NotFoundError("Organization not found")
    stored = _normalise(data)
    org.payment_settings = stored
    await audit.record(
        session,
        org_id=principal.org_id,
        actor=principal,
        action="organization.payment_settings_set",
        entity_type="organization",
        entity_id=org.id,
        summary="Bank transfer payer settings updated",
        changes={"payment_settings": _masked(stored)},
    )
    await session.commit()
    return {"payment_settings": _masked(stored)}


@router.get("/organizations/current/payment-settings")
async def get_payment_settings(session: DB, principal: Finance) -> dict:
    stored = await session.scalar(
        select(Organization.payment_settings).where(Organization.id == principal.org_id)
    )
    return {"payment_settings": _masked(stored)}


@router.post("/payments/transfer-file")
async def export_transfer_file(data: TransferFileIn, session: DB, principal: Finance) -> dict:
    """Build a Zengin 総合振込 file from approved, not yet exported JPY payments.

    The included payments become `exported` in the same transaction, so a payment can never be
    placed in two files. The file is returned for a person to upload to the bank; nothing is sent.
    """
    if data.transfer_date < date.today():
        raise BusinessRuleViolation("The transfer date cannot be in the past")
    # The organisation row lock serialises exports per tenant (unique references, no overlap).
    org = await session.scalar(
        select(Organization).where(Organization.id == principal.org_id).with_for_update()
    )
    if org is None or not org.payment_settings:
        raise BusinessRuleViolation("Configure the payer bank account and consignor code first")
    rows = (
        await session.execute(
            select(Payment, Invoice.invoice_number, Vendor.name, Vendor.bank_account)
            .join(Invoice, Invoice.id == Payment.invoice_id)
            .join(Vendor, Vendor.id == Invoice.vendor_id)
            .where(Payment.org_id == principal.org_id, Payment.status == "approved")
            .order_by(Payment.created_at)
            .with_for_update(of=Payment)
        )
    ).all()
    included: list[tuple[Payment, str, Transfer]] = []
    skipped: list[dict] = []
    for payment, invoice_number, vendor_name, bank_account in rows:
        if payment.currency != "JPY":
            skipped.append({"payment_id": str(payment.id), "reason": "Zengin transfers are JPY only"})
        elif not bank_account:
            skipped.append({"payment_id": str(payment.id), "reason": f"No bank account for {vendor_name}"})
        else:
            included.append(
                (payment, invoice_number, Transfer(_account(bank_account), payment.amount, invoice_number))
            )
    if not included:
        raise BusinessRuleViolation("No approved payments are ready for export", details={"skipped": skipped})
    settings = org.payment_settings
    try:
        content, records = build_transfer_file(
            consignor_code=settings["consignor_code"],
            consignor_name_kana=settings["consignor_name_kana"],
            transfer_date=data.transfer_date,
            payer=_account(settings),
            transfers=[transfer for _, _, transfer in included],
        )
    except ZenginError as exc:
        raise BusinessRuleViolation(str(exc)) from exc

    sequence = (
        await session.scalar(
            select(func.count(func.distinct(Payment.export_reference))).where(
                Payment.org_id == principal.org_id, Payment.export_reference.is_not(None)
            )
        )
        or 0
    ) + 1
    reference = f"ZG-{data.transfer_date:%Y%m%d}-{sequence:04d}"
    now = utcnow()
    for (payment, _, _), record in zip(included, records, strict=True):
        payment.status = "exported"
        payment.exported_at = now
        payment.exported_by_id = principal.user_id
        payment.export_reference = reference
        payment.transfer_record = record
    total = sum(int(transfer.amount) for _, _, transfer in included)
    digest = hashlib.sha256(content).hexdigest()
    await audit.record(
        session,
        org_id=principal.org_id,
        actor=principal,
        action="payment.transfer_file_exported",
        entity_type="organization",
        entity_id=principal.org_id,
        summary=f"Zengin transfer file {reference}: {len(included)} payments, ¥{total:,}",
        changes={
            "reference": reference,
            "transfer_date": data.transfer_date,
            "payment_ids": [str(payment.id) for payment, _, _ in included],
            "total": total,
            "sha256": digest,
        },
    )
    await session.commit()
    return {
        "reference": reference,
        "filename": f"{reference}.txt",
        "encoding": "Shift_JIS, 120-byte records, CRLF",
        "transfer_date": data.transfer_date,
        "payments": len(included),
        "total": total,
        "sha256": digest,
        "skipped": skipped,
        "content_base64": base64.b64encode(content).decode("ascii"),
    }
