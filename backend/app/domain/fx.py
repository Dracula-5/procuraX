"""Exchange rates for requests in another currency. Pure functions, no I/O.

A rate is stored as "1 unit of the quote currency = `rate` units of the tenant's base currency". At
submission the latest rate on or before the submission date is snapshotted on the request with the
converted base-currency amount; the policy engine and budgets work in base currency from then on.
POs and invoices keep the transaction currency.
"""

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from app.domain.money import minor_units

MANUAL = "manual"
ECB = "ecb"
# ISO 4217 codes a tenant can buy in. Any of them can be the base currency.
SUPPORTED_CURRENCIES = (
    "JPY",
    "USD",
    "EUR",
    "GBP",
    "CNY",
    "KRW",
    "SGD",
    "AUD",
    "CAD",
    "CHF",
    "HKD",
    "TWD",
    "THB",
)


@dataclass(frozen=True)
class Rate:
    currency: str
    rate: Decimal
    valid_on: date
    source: str


def pick_rate(rates: list[Rate], currency: str, on: date) -> Rate | None:
    """The most recent rate for `currency` valid on or before `on`; on the same day a manual rate wins."""
    candidates = [r for r in rates if r.currency == currency and r.valid_on <= on]
    if not candidates:
        return None
    return max(candidates, key=lambda r: (r.valid_on, r.source == MANUAL, r.source))


def to_base(amount: Decimal, rate: Decimal, base_currency: str) -> Decimal:
    """Convert to the base currency, rounded half-up to its minor unit (0 decimals for JPY)."""
    step = Decimal(1).scaleb(-minor_units(base_currency))
    return (amount * rate).quantize(step, rounding=ROUND_HALF_UP)


def cross_rates(eur_rates: dict[str, Decimal], base_currency: str) -> dict[str, Decimal]:
    """Turn EUR-based reference rates (1 EUR = x CCY, as the ECB publishes) into base-currency rates.

    1 CCY = (EUR→base) / (EUR→CCY) units of base. Returns rates for every currency except the base.
    """
    table = {"EUR": Decimal(1), **eur_rates}
    if base_currency not in table:
        raise ValueError(f"No reference rate for the base currency {base_currency}")
    base_per_eur = table[base_currency]
    return {
        ccy: (base_per_eur / per_eur).quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)
        for ccy, per_eur in table.items()
        if ccy != base_currency and per_eur > 0
    }
