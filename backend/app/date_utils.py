"""
Fiscal-year (April-March) and "last complete period" helpers.

This is a fixed historical dataset (Jan 2025 - 30 Jun 2026), not a live
feed, so "last month" / "last complete quarter" are computed relative to
the latest order_date actually present in the data (see db.data_max_order_date),
not relative to wall-clock today.
"""
from datetime import date


def fiscal_year(d: date) -> int:
    """Kestrel's FY runs April-March (per assignment brief). FY label is
    the calendar year the FY *ends* in -- the standard Indian corporate
    convention (e.g. "FY2027" is used for the year ending 31 Mar 2027), and
    the convention explicitly requested on review. So 1 Apr 2026 -
    30 Jun 2026 is "FY2027 Q1", not "FY2026 Q1". Corrected after an initial
    (wrong) implementation used the starting year instead -- see
    DECISIONS.md."""
    return d.year + 1 if d.month >= 4 else d.year


def fiscal_quarter(d: date) -> int:
    return {4: 1, 5: 1, 6: 1, 7: 2, 8: 2, 9: 2, 10: 3, 11: 3, 12: 3, 1: 4, 2: 4, 3: 4}[d.month]


def fiscal_quarter_bounds(fy: int, fq: int) -> tuple[date, date]:
    """fy is the year-ending label (see fiscal_year() docstring): Q1-Q3
    (Apr-Dec) fall in calendar year fy-1, Q4 (Jan-Mar) falls in calendar
    year fy itself."""
    start_month = {1: 4, 2: 7, 3: 10, 4: 1}[fq]
    start_year = fy - 1 if fq != 4 else fy
    start = date(start_year, start_month, 1)
    if start_month == 10:
        end = date(start_year, 12, 31)
    elif start_month == 1:
        end = date(start_year, 3, 31)
    elif start_month == 4:
        end = date(start_year, 6, 30)
    else:  # 7
        end = date(start_year, 9, 30)
    return start, end


def last_complete_fiscal_quarter(reference: date) -> tuple[int, int, date, date]:
    """The most recent fiscal quarter that has fully ended on or before
    `reference`. If `reference` itself lands exactly on a quarter end
    (true here: 30 Jun 2026 is the last day of FY2027 Q1), that quarter
    counts as complete."""
    fy, fq = fiscal_year(reference), fiscal_quarter(reference)
    start, end = fiscal_quarter_bounds(fy, fq)
    if reference >= end:
        return fy, fq, start, end
    # Step back one quarter.
    if fq == 1:
        fy, fq = fy - 1, 4
    else:
        fq -= 1
    start, end = fiscal_quarter_bounds(fy, fq)
    return fy, fq, start, end


def last_complete_calendar_month(reference: date) -> tuple[date, date]:
    """The most recent calendar month that has fully ended on or before
    `reference`."""
    first_of_this_month = reference.replace(day=1)
    if reference == _last_day_of_month(reference):
        start = first_of_this_month
        end = reference
        return start, end
    # Step back to previous month.
    prev_end = first_of_this_month - _one_day()
    prev_start = prev_end.replace(day=1)
    return prev_start, prev_end


def _one_day():
    from datetime import timedelta
    return timedelta(days=1)


def _last_day_of_month(d: date) -> date:
    from calendar import monthrange
    return d.replace(day=monthrange(d.year, d.month)[1])
