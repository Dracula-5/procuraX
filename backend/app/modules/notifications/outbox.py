"""Writing events to the transactional outbox.

`emit` only adds a row to the caller's session: it commits (or rolls back) with the business change,
so a notification can never describe something that did not happen, and a committed change can never
lose its notification. Delivery happens later, in the worker.
"""

import uuid
from collections.abc import Iterable
from decimal import Decimal
from typing import TypedDict

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.money import format_money
from app.domain.notifications import EventType
from app.modules.notifications.models import OutboxEvent


class MoneyFacts(TypedDict):
    amount: str
    amount_value: str
    currency: str


def money_facts(amount: Decimal, currency: str) -> MoneyFacts:
    """Amount as a formatted string (for e-mail and webhooks) plus its parts (for the SPA's own formatting)."""
    return {"amount": format_money(amount, currency), "amount_value": str(amount), "currency": currency}


def emit(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    event_type: EventType,
    aggregate_type: str,
    aggregate_id: uuid.UUID,
    actor_user_id: uuid.UUID | None,
    recipient_user_ids: Iterable[uuid.UUID | None] = (),
    recipient_roles: Iterable[str] = (),
    link: str | None = None,
    **facts: str | int | None,
) -> OutboxEvent:
    """Queue an event in the current transaction.

    Recipients are named users, roles (every active holder in the tenant, resolved by the worker), or
    both. `facts` are the strings the message templates use (number, title, amount, ...).
    """
    event = OutboxEvent(
        org_id=org_id,
        event_type=event_type.value,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        actor_user_id=actor_user_id,
        payload={
            **{k: v for k, v in facts.items() if v is not None},
            "recipient_user_ids": sorted({str(u) for u in recipient_user_ids if u is not None}),
            "recipient_roles": sorted(set(recipient_roles)),
            "link": link,
        },
    )
    session.add(event)
    return event
