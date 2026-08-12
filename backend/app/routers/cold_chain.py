"""
Cold chain: temperature excursions per hundred chilled deliveries (by
month), returns attributable to cold-chain failure or near-expiry, and
near-expiry stock from the latest inventory snapshot.

Scoped deliberately light (10% of build priority per plan) but covers all
three things Divya's brief item 2 names: excursions, near-expiry stock,
and cold-chain-caused returns.
"""
from typing import Optional

from fastapi import APIRouter, Query

from ..db import get_connection, run_query
from ..schemas import MetricResponse, MetricRow
from ..query_helpers import period_filter

router = APIRouter(prefix="/cold-chain", tags=["cold-chain"])

COLD_CHAIN_REASON_CODES = ("RT01_NEAR_EXPIRY", "RT06_COLD_CHAIN_BREACH")


@router.get("/excursions", response_model=MetricResponse)
def excursions(fiscal_year: Optional[int] = None, fiscal_quarter: Optional[int] = None,
               month: Optional[str] = None):
    period_sql, params, period_label = period_filter(fiscal_year, fiscal_quarter, month, "d.dispatch_datetime")

    sql = f"""
        WITH chilled_orders AS (
            SELECT DISTINCT order_id FROM fact_order_lines WHERE is_chilled
        ),
        flagged AS (
            SELECT d.delivery_id,
                   strftime(d.dispatch_datetime, '%Y-%m') AS month,
                   d.temperature_excursion_flag
            FROM fact_deliveries d
            JOIN chilled_orders c ON c.order_id = d.order_id
            WHERE 1=1 {period_sql}
        )
        SELECT month AS dim_code, month AS dim_label,
               COUNT(*) AS chilled_deliveries,
               SUM(CASE WHEN temperature_excursion_flag = 1 THEN 1 ELSE 0 END) AS excursions,
               ROUND(100.0 * SUM(CASE WHEN temperature_excursion_flag = 1 THEN 1 ELSE 0 END) / COUNT(*), 2)
                   AS excursions_per_hundred
        FROM flagged
        GROUP BY 1, 2
        ORDER BY 1
    """
    with get_connection() as con:
        rows = run_query(con, sql, params)

    return MetricResponse(
        metric="temperature_excursions_per_hundred_chilled_deliveries",
        group_by="month",
        period_label=period_label,
        rows=[MetricRow(dimension=r["dim_code"], dimension_label=r["dim_label"],
                         metrics={k: v for k, v in r.items() if k not in ("dim_code", "dim_label")})
              for r in rows],
        caveats=[
            "A delivery counts as 'chilled' if any order line on its order is a chilled SKU (is_chilled=1) "
            "-- the deliveries table itself has no per-shipment chilled flag, only a whole-shipment "
            "temperature_excursion_flag, so mixed ambient+chilled shipments are included here.",
        ],
    )


@router.get("/returns", response_model=MetricResponse)
def cold_chain_returns(fiscal_year: Optional[int] = None, fiscal_quarter: Optional[int] = None,
                        month: Optional[str] = None, limit: int = 50):
    period_sql, params, period_label = period_filter(fiscal_year, fiscal_quarter, month, "return_date")
    reason_list = ", ".join(f"'{r}'" for r in COLD_CHAIN_REASON_CODES)

    sql = f"""
        SELECT category AS dim_code, category AS dim_label,
               return_reason_code,
               SUM(credit_note_value_inr) AS return_value_inr,
               COUNT(*) AS n_returns
        FROM fact_returns
        WHERE return_reason_code IN ({reason_list}) {period_sql}
        GROUP BY 1, 2, 3
        ORDER BY return_value_inr DESC
        LIMIT ?
    """
    with get_connection() as con:
        rows = run_query(con, sql, params + [limit])

    return MetricResponse(
        metric="cold_chain_returns",
        group_by="category_and_reason",
        period_label=period_label,
        filters_applied={"return_reason_code_in": list(COLD_CHAIN_REASON_CODES)},
        rows=[MetricRow(dimension=r["dim_code"], dimension_label=r["dim_label"],
                         metrics={k: v for k, v in r.items() if k not in ("dim_code", "dim_label")})
              for r in rows],
        caveats=[
            "RT01_NEAR_EXPIRY is included as a cold-chain-adjacent reason per the brief ('near-expiry "
            "stock' is grouped with cold chain), not because it is definitionally a cold-chain failure.",
        ],
    )
