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
def excursions(fiscal_year: Optional[int] = None, fiscal_quarter: Optional[int] = Query(None, ge=1, le=4),
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
def cold_chain_returns(fiscal_year: Optional[int] = None, fiscal_quarter: Optional[int] = Query(None, ge=1, le=4),
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


@router.get("/near-expiry", response_model=MetricResponse)
def near_expiry(
    group_by: str = "category",
    near_expiry_days: int = 30,
    warehouse_code: Optional[str] = None,
):
    """Stock expiring within `near_expiry_days` of the LATEST inventory
    snapshot (this is a point-in-time stock position, not a period metric --
    there is exactly one 'now' for on-hand stock, unlike orders/deliveries
    which accumulate over a date range)."""
    if group_by not in ("category", "warehouse"):
        group_by = "category"
    needs_warehouse_join = group_by == "warehouse" or warehouse_code is not None
    dim_select = (
        "s.category AS dim_code, s.category AS dim_label" if group_by == "category"
        else "w.warehouse_code AS dim_code, w.warehouse_name AS dim_label"
    )
    dim_join = "JOIN dim_warehouse w ON w.warehouse_id = s.warehouse_id" if needs_warehouse_join else ""
    warehouse_filter = "AND w.warehouse_code = ?" if warehouse_code else ""

    sql = f"""
        WITH latest AS (SELECT MAX(snapshot_date) AS d FROM fact_inventory_snapshot)
        SELECT {dim_select},
               SUM(s.on_hand_cases) AS on_hand_cases,
               SUM(CASE WHEN s.days_to_expiry BETWEEN 0 AND ? THEN s.on_hand_cases ELSE 0 END) AS near_expiry_cases,
               ROUND(100.0 * SUM(CASE WHEN s.days_to_expiry BETWEEN 0 AND ? THEN s.on_hand_cases ELSE 0 END)
                     / NULLIF(SUM(s.on_hand_cases), 0), 1) AS near_expiry_pct
        FROM fact_inventory_snapshot s
        {dim_join}
        WHERE s.snapshot_date = (SELECT d FROM latest) {warehouse_filter}
        GROUP BY 1, 2
        ORDER BY near_expiry_pct DESC
    """
    # near_expiry_days is bound twice (near_expiry_cases, near_expiry_pct);
    # warehouse_code, if given, is bound once more for the filter clause.
    params = [near_expiry_days, near_expiry_days] + ([warehouse_code] if warehouse_code else [])
    with get_connection() as con:
        as_of = con.execute("SELECT MAX(snapshot_date) FROM fact_inventory_snapshot").fetchone()[0]
        rows = run_query(con, sql, params)

    return MetricResponse(
        metric="near_expiry_stock",
        group_by=group_by,
        period_label=f"as of latest snapshot ({as_of})",
        filters_applied={"near_expiry_days": near_expiry_days, "warehouse_code": warehouse_code},
        rows=[MetricRow(dimension=r["dim_code"], dimension_label=r["dim_label"],
                         metrics={k: v for k, v in r.items() if k not in ("dim_code", "dim_label")})
              for r in rows],
        caveats=[
            "Near-expiry is computed from expiry_date - snapshot_date directly, NOT from the source "
            "ageing_bucket column: checked, and ageing_bucket is uncorrelated with actual days-to-expiry "
            "(all four buckets average ~100 days to expiry regardless of label) -- it is unusable and "
            "not relied on anywhere in this build.",
            "Point-in-time as of the latest weekly snapshot, not a period-filtered metric.",
        ],
    )
