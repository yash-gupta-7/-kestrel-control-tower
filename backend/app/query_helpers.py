"""Shared query-building helpers used across the service/money/cold-chain
routers, so period filtering logic (fiscal quarter, calendar month, or all
data) is implemented exactly once."""
from typing import Optional

from fastapi import HTTPException

from . import date_utils

FULFILLED_STATUSES = ("DELIVERED", "PARTIAL")


def period_filter(fiscal_year: Optional[int], fiscal_quarter: Optional[int], month: Optional[str],
                   date_col: str) -> tuple[str, list, str]:
    """Returns (sql_fragment, params, period_label). Empty fragment = all data.
    sql_fragment is a standalone "AND ..." clause, safe to concatenate into
    a WHERE that already has at least one condition, or to strip the
    leading "AND " for a fresh WHERE clause."""
    if month:
        try:
            y, m = month.split("-")
            int(y), int(m)
        except ValueError:
            raise HTTPException(400, "month must be formatted YYYY-MM")
        return f"AND strftime({date_col}, '%Y-%m') = ?", [month], f"month {month}"
    if fiscal_year is not None and fiscal_quarter is not None:
        start, end = date_utils.fiscal_quarter_bounds(fiscal_year, fiscal_quarter)
        return f"AND {date_col} BETWEEN ? AND ?", [start, end], f"FY{fiscal_year} Q{fiscal_quarter}"
    if fiscal_year is not None or fiscal_quarter is not None:
        raise HTTPException(400, "fiscal_year and fiscal_quarter must be given together")
    return "", [], "all available data (Jan 2025 - Jun 2026)"
