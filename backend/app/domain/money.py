from decimal import ROUND_HALF_UP, Decimal

_SYMBOLS = {"JPY": "¥", "USD": "$", "EUR": "€", "GBP": "£"}
# ISO 4217 minor units for the currencies we format specially; default is 2.
_MINOR_UNITS = {"JPY": 0, "KRW": 0}


def minor_units(currency: str) -> int:
    return _MINOR_UNITS.get(currency, 2)


def quantize_money(amount: Decimal, currency: str) -> Decimal:
    exp = Decimal(1).scaleb(-minor_units(currency))
    return amount.quantize(exp, rounding=ROUND_HALF_UP)


def format_money(amount: Decimal, currency: str) -> str:
    places = minor_units(currency)
    body = f"{amount:,.{places}f}"
    symbol = _SYMBOLS.get(currency)
    return f"{symbol}{body}" if symbol else f"{currency} {body}"
