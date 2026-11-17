"""Exchange-rate lookups for the tenant (manual rates and, when enabled, ECB reference rates)."""

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.fx import Rate, pick_rate, to_base
from app.modules.finance.models import ExchangeRate


async def find_rate(session: AsyncSession, org_id: uuid.UUID, currency: str, on: date) -> Rate | None:
    """The latest rate valid on or before `on`; on the same day a manual rate wins over a provider's."""
    rows = await session.scalars(
        select(ExchangeRate)
        .where(ExchangeRate.org_id == org_id, ExchangeRate.currency == currency, ExchangeRate.valid_on <= on)
        .order_by(ExchangeRate.valid_on.desc())
        .limit(10)
    )
    return pick_rate([Rate(r.currency, r.rate, r.valid_on, r.source) for r in rows], currency, on)


async def convert(
    session: AsyncSession, org_id: uuid.UUID, base_currency: str, amount: Decimal, currency: str, on: date
) -> tuple[Decimal | None, Rate | None]:
    """(base-currency amount, rate used). Same currency → the amount itself; no rate → (None, None)."""
    if currency == base_currency:
        return amount, None
    rate = await find_rate(session, org_id, currency, on)
    if rate is None:
        return None, None
    return to_base(amount, rate.rate, base_currency), rate
