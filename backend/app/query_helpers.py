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


def region_filter(region_code: Optional[str], region_id_col: str) -> tuple[str, list]:
    """Returns (sql_fragment, params) for the "regional manager view" scope
    (see DECISIONS.md). This is a plain WHERE-clause filter, not
    authentication/authorization -- anyone can switch region in the UI, it
    just narrows what's shown, the same way a warehouse_code filter already
    does elsewhere in this API.

    region_code is looked up against dim_region by a scalar subquery rather
    than validated here: an unknown code resolves to NULL, region_id_col =
    NULL is false for every row, so the query returns an empty result set
    (same graceful-empty behaviour as an unmatched warehouse_code filter)
    instead of raising -- the frontend only ever offers codes it fetched
    from GET /meta/regions, so this path is a safety net, not the primary
    validation."""
    if not region_code:
        return "", []
    return (
        f"AND {region_id_col} = (SELECT region_id FROM dim_region WHERE region_code = ?)",
        [region_code],
    )
