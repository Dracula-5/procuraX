"""European Central Bank euro foreign-exchange reference rates (daily XML, free, no key).

Behind PROCURAX_FX_ECB_ENABLED (off by default). The daily file lists "1 EUR = x CCY" for about 30
currencies; `app.domain.fx.cross_rates` turns those into rates against the tenant's base currency.
Rates are stored with source "ecb" and the ECB's own reference date; a manual rate for the same day
wins. The ECB states that the rates are for information purposes only — a tenant that needs its bank's
or its accounting policy's rates should enter them manually.
"""

from datetime import date
from decimal import Decimal, InvalidOperation

import httpx
from defusedxml import DefusedXmlException
from defusedxml.ElementTree import ParseError, fromstring

from app.core.config import Settings, get_settings

_ECB_NS = "{http://www.ecb.int/vocabulary/2002-08-01/eurofxref}"


class RatesUnavailableError(RuntimeError):
    pass


def parse_daily(xml: bytes) -> tuple[date, dict[str, Decimal]]:
    """(reference date, {currency: units per EUR}) from eurofxref-daily.xml. Pure; tested on a fixture."""
    try:
        root = fromstring(xml, forbid_dtd=True)
    except (ParseError, DefusedXmlException) as exc:
        raise RatesUnavailableError("The ECB response is not valid XML") from exc
    day = next((c for c in root.iter(f"{_ECB_NS}Cube") if c.get("time")), None)
    if day is None:
        raise RatesUnavailableError("The ECB response has no reference date")
    try:
        on = date.fromisoformat(str(day.get("time")))
    except ValueError as exc:
        raise RatesUnavailableError("The ECB reference date is not a date") from exc
    rates: dict[str, Decimal] = {}
    for cube in day.iter(f"{_ECB_NS}Cube"):
        currency, rate = cube.get("currency"), cube.get("rate")
        if not currency or not rate:
            continue
        try:
            value = Decimal(rate)
        except InvalidOperation:
            continue
        if len(currency) == 3 and currency.isalpha() and value > 0:
            rates[currency.upper()] = value
    if not rates:
        raise RatesUnavailableError("The ECB response lists no rates")
    return on, rates


async def fetch_daily(
    settings: Settings | None = None, client: httpx.AsyncClient | None = None
) -> tuple[date, dict[str, Decimal]]:
    settings = settings or get_settings()
    own_client = client is None
    client = client or httpx.AsyncClient(timeout=settings.fx_timeout_seconds)
    try:
        response = await client.get(settings.fx_ecb_url, headers={"Accept": "application/xml"})
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise RatesUnavailableError(f"ECB reference rates are unavailable: {exc.__class__.__name__}") from exc
    finally:
        if own_client:
            await client.aclose()
    return parse_daily(response.content)
