"""
Money: freight cost per delivered case (the only source of *actual* billed
freight cost -- deliveries.fuel_cost_inr is driver-entered and unreconciled,
per the data dictionary), and returns/credit notes as leakage.
"""
from typing import Literal, Optional

from fastapi import APIRouter, Query

from ..db import get_connection, run_query
from ..query_helpers import FULFILLED_STATUSES, period_filter
from ..schemas import MetricResponse, MetricRow

router = APIRouter(prefix="/money", tags=["money"])


@router.get("/freight-cost-per-case", response_model=MetricResponse)
def freight_cost_per_case(
    group_by: Literal["warehouse", "carrier"] = Query("warehouse"),
    fiscal_year: Optional[int] = None,
    fiscal_quarter: Optional[int] = None,
    month: Optional[str] = None,
    limit: int = Query(50, ge=1, le=500),
):
    freight_date_col = "f.invoice_date"
    order_date_col = "ol.order_date"
    freight_period_sql, freight_params, period_label = period_filter(
        fiscal_year, fiscal_quarter, month, freight_date_col
    )
    order_period_sql, order_params, _ = period_filter(fiscal_year, fiscal_quarter, month, order_date_col)

    if group_by == "warehouse":
        dim_select = "w.warehouse_code AS dim_code, w.warehouse_name AS dim_label"
        freight_group_join = "JOIN dim_warehouse w ON w.warehouse_id = f.warehouse_id"
        freight_group_col = "w.warehouse_code"
        cases_group_join = "JOIN dim_warehouse w2 ON w2.warehouse_id = ol.warehouse_id"
        cases_group_col = "w2.warehouse_code"
    else:
        dim_select = "f.carrier_id AS dim_code, f.carrier_name AS dim_label"
        freight_group_join = ""
        freight_group_col = "f.carrier_id, f.carrier_name"
        cases_group_join = ""
        cases_group_col = None  # carrier isn't on order_lines; cost/case not computable per-carrier

    with get_connection() as con:
        if group_by == "warehouse":
            sql = f"""
                WITH freight_agg AS (
                    SELECT {freight_group_col} AS dim_code, SUM(f.amount_inr) AS freight_inr
                    FROM fact_freight f
                    {freight_group_join}
                    WHERE 1=1 {freight_period_sql}
                    GROUP BY 1
                ),
                cases_agg AS (
                    SELECT {cases_group_col} AS dim_code, SUM(ol.delivered_cases) AS delivered_cases
                    FROM fact_order_lines ol
                    {cases_group_join}
                    WHERE ol.order_status IN {FULFILLED_STATUSES} {order_period_sql}
                    GROUP BY 1
                )
                SELECT dm.warehouse_code AS dim_code, dm.warehouse_name AS dim_label,
                       fa.freight_inr, ca.delivered_cases,
                       ROUND(fa.freight_inr / NULLIF(ca.delivered_cases, 0), 2) AS freight_inr_per_case
                FROM freight_agg fa
                JOIN cases_agg ca ON ca.dim_code = fa.dim_code
                JOIN dim_warehouse dm ON dm.warehouse_code = fa.dim_code
                ORDER BY freight_inr_per_case DESC
                LIMIT ?
            """
            rows = run_query(con, sql, freight_params + order_params + [limit])
            caveats = [
                "Freight invoice date and order date are both filtered to the same period independently "
                "(invoices aren't individually linkable to a specific order in this API) -- "
                "treat this as 'freight billed in period X' over 'cases delivered in period X', not a "
                "line-by-line reconciliation.",
            ]
        else:
            sql = f"""
                SELECT f.carrier_id AS dim_code, f.carrier_name AS dim_label,
                       SUM(f.amount_inr) AS freight_inr, COUNT(*) AS n_invoices,
                       ROUND(AVG(f.amount_inr), 2) AS avg_invoice_inr
                FROM fact_freight f
                WHERE 1=1 {freight_period_sql}
                GROUP BY 1, 2
                ORDER BY freight_inr DESC
                LIMIT ?
            """
            rows = run_query(con, sql, freight_params + [limit])
            caveats = [
                "Cost-per-delivered-case is not shown by carrier: freight invoices carry warehouse_code "
                "and route_code but not an outlet/order-line-level key, so cases delivered can't be "
                "attributed to a specific carrier from this data -- shown as total freight and invoice count instead.",
            ]

    return MetricResponse(
        metric="freight_cost_per_case" if group_by == "warehouse" else "freight_by_carrier",
        group_by=group_by,
        period_label=period_label,
        filters_applied={"order_status_in": list(FULFILLED_STATUSES)} if group_by == "warehouse" else {},
        rows=[MetricRow(dimension=r["dim_code"], dimension_label=r["dim_label"],
                         metrics={k: v for k, v in r.items() if k not in ("dim_code", "dim_label")})
              for r in rows],
        caveats=caveats,
    )


@router.get("/returns-leakage", response_model=MetricResponse)
def returns_leakage(
    group_by: Literal["category", "carrier"] = Query("category"),
    fiscal_year: Optional[int] = None,
    fiscal_quarter: Optional[int] = None,
    month: Optional[str] = None,
    limit: int = Query(50, ge=1, le=500),
):
    if group_by == "carrier":
        return MetricResponse(
            metric="returns_leakage", group_by=group_by, period_label="n/a",
            rows=[],
            caveats=[
                "returns_credit_notes has no carrier attribution in this dataset -- a return is tied to "
                "an outlet/order/product, not a delivery or carrier. Grouping by category is supported; "
                "by carrier is not, rather than fabricating a join that doesn't exist.",
            ],
        )

    ret_period_sql, ret_params, period_label = period_filter(fiscal_year, fiscal_quarter, month, "r.return_date")
    dispatch_period_sql, dispatch_params, _ = period_filter(fiscal_year, fiscal_quarter, month, "ol.order_date")

    sql = f"""
        WITH returns_agg AS (
            SELECT r.category,
                   SUM(r.credit_note_value_inr) AS return_value_inr,
                   COUNT(*) AS n_returns,
                   MODE(r.return_reason_code) AS leading_reason_code
            FROM fact_returns r
            WHERE 1=1 {ret_period_sql}
            GROUP BY 1
        ),
        dispatch_agg AS (
            SELECT category, SUM(line_value_inr) AS dispatch_value_inr
            FROM fact_order_lines ol
            WHERE order_status IN {FULFILLED_STATUSES} {dispatch_period_sql}
            GROUP BY 1
        )
        SELECT ra.category AS dim_code, ra.category AS dim_label,
               ra.return_value_inr, ra.n_returns, ra.leading_reason_code,
               da.dispatch_value_inr,
               ROUND(100.0 * ra.return_value_inr / NULLIF(da.dispatch_value_inr, 0), 2) AS returns_pct_of_dispatch
        FROM returns_agg ra
        LEFT JOIN dispatch_agg da ON da.category = ra.category
        ORDER BY ra.return_value_inr DESC
        LIMIT ?
    """
    with get_connection() as con:
        rows = run_query(con, sql, ret_params + dispatch_params + [limit])

    return MetricResponse(
        metric="returns_leakage",
        group_by=group_by,
        period_label=period_label,
        filters_applied={"order_status_in": list(FULFILLED_STATUSES)},
        rows=[MetricRow(dimension=r["dim_code"], dimension_label=r["dim_label"],
                         metrics={k: v for k, v in r.items() if k not in ("dim_code", "dim_label")})
              for r in rows],
        caveats=[
            "Return quantities/values with a negative sign in the source data (KP-2402, one upstream "
            "feed) are taken as abs() during ETL -- treated as a sign bug, not a refund/reversal signal.",
        ],
    )
