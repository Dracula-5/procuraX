from datetime import date


def fiscal_year_of(day: date, start_month: int) -> int:
    """Fiscal year labelled by the calendar year in which it starts.

    With an April start (common in Japan), 2026-04-01..2027-03-31 is FY2026.
    """
    if not 1 <= start_month <= 12:
        raise ValueError("start_month must be 1..12")
    return day.year if day.month >= start_month else day.year - 1
