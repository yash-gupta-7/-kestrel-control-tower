"""
Service: fill rate and OTIF, by outlet / region / warehouse / route.

Fill rate is EACHES ONLY, not configurable. The brief's illustrative Q1
says "case fill rate" but Rakesh's follow-up email explicitly overrides
that: modern trade penalises Kestrel on units short, not cases short, and
the brief's own numbered-question list is illustrative, not binding. This
is a locked decision (see DECISIONS.md) -- the API does not expose a
cases/eaches toggle so nothing downstream can silently regress to cases.

Both metrics are scoped to orders with status DELIVERED or PARTIAL by
default -- CANCELLED orders were never meant to be fulfilled and OPEN
orders haven't reached a conclusion yet, so including them would
understate performance on things that aren't performance problems. This
is a judgement call, documented here and in DECISIONS.md, not derived
from the brief.

OTIF's "in-full" component is reported at the strict 100% definition
(delivered_eaches >= ordered_eaches), with NO tolerance threshold --
verified this is ~0% almost everywhere in this dataset (see caveats
below and DECISIONS.md) because every order line carries some shortfall
by data design, not because 100% is the wrong bar. Rather than quietly
picking a threshold that makes the number "look normal," in_full_pct is
reported strictly and a second, non-boolean metric --
avg_fulfilment_pct -- is reported alongside it so the endpoint still
carries a useful, honest signal.
"""
from typing import Literal, Optional

from fastapi import APIRouter, Query

from ..db import data_max_order_date, get_connection, run_query
from ..query_helpers import FULFILLED_STATUSES, period_filter
from ..schemas import MetricResponse, MetricRow

router = APIRouter(prefix="/service", tags=["service"])

GroupBy = Literal["outlet", "region", "warehouse", "route"]

_DIM_CONFIG = {
    "outlet": dict(table="dim_outlet", id_col="outlet_id", code_col="outlet_code", label_col="outlet_name"),
    "region": dict(table="dim_region", id_col="region_id", code_col="region_code", label_col="region_name"),
    "warehouse": dict(table="dim_warehouse", id_col="warehouse_id", code_col="warehouse_code", label_col="warehouse_name"),
    "route": dict(table="dim_route", id_col="route_id", code_col="route_code", label_col="route_name"),
}


@router.get("/fill-rate", response_model=MetricResponse)
def fill_rate(
    group_by: GroupBy = Query("outlet"),
    fiscal_year: Optional[int] = None,
    fiscal_quarter: Optional[int] = Query(None, ge=1, le=4),
    month: Optional[str] = Query(None, description="YYYY-MM, overrides fiscal_year/quarter if given"),
    exclude_test_outlets: bool = True,
    exclude_closed_outlets: bool = False,
    exclude_deleted_outlets: bool = True,
    order: Literal["asc", "desc"] = Query("asc", description="asc = worst performers first"),
    limit: int = Query(50, ge=1, le=500),
):
    cfg = _DIM_CONFIG[group_by]
    where_clauses = [f"ol.order_status IN {FULFILLED_STATUSES}"]
    params: list = []

    period_sql, period_params, period_label = period_filter(fiscal_year, fiscal_quarter, month, "ol.order_date")
    if period_sql:
        # period_sql is "AND <condition>"; strip only the leading "AND " to
        # fold it into where_clauses (joined with " AND " below) -- a bare
        # .replace("AND ", "") here would also strip the "AND" inside
        # "BETWEEN ? AND ?", corrupting the query. Found by testing this
        # exact group_by=outlet + fiscal_year/quarter combination, which
        # wasn't exercised in the Phase 3 checkpoint.
        where_clauses.append(period_sql.removeprefix("AND "))
        params += period_params

    outlet_join = ""
    if group_by == "outlet" or exclude_test_outlets or exclude_closed_outlets or exclude_deleted_outlets:
        outlet_join = "JOIN dim_outlet do2 ON do2.outlet_id = ol.outlet_id"
        if exclude_test_outlets:
            where_clauses.append("NOT do2.is_test_outlet")
        if exclude_deleted_outlets:
            where_clauses.append("do2.is_deleted = 0")
        if exclude_closed_outlets:
            where_clauses.append("do2.status != 'CLOSED'")

    qty_col = "ordered_eaches"
    del_col = "delivered_eaches"

    dim_join = "" if group_by == "outlet" else f"JOIN {cfg['table']} d ON d.{cfg['id_col']} = ol.{cfg['id_col']}"
    dim_select = (
        f"do2.{cfg['code_col']} AS dim_code, do2.{cfg['label_col']} AS dim_label"
        if group_by == "outlet"
        else f"d.{cfg['code_col']} AS dim_code, d.{cfg['label_col']} AS dim_label"
    )

    sql = f"""
        SELECT {dim_select},
               SUM(ol.{qty_col}) AS ordered_qty,
               SUM(ol.{del_col}) AS delivered_qty,
               ROUND(100.0 * SUM(ol.{del_col}) / NULLIF(SUM(ol.{qty_col}), 0), 1) AS fill_rate_pct
        FROM fact_order_lines ol
        {outlet_join}
        {dim_join}
        WHERE {' AND '.join(where_clauses)}
        GROUP BY 1, 2
        HAVING SUM(ol.{qty_col}) > 0
        ORDER BY fill_rate_pct {order}
        LIMIT ?
    """
    with get_connection() as con:
        rows = run_query(con, sql, params + [limit])

    return MetricResponse(
        metric="fill_rate_eaches",
        group_by=group_by,
        period_label=period_label,
        filters_applied=dict(
            uom="eaches", exclude_test_outlets=exclude_test_outlets,
            exclude_closed_outlets=exclude_closed_outlets,
            exclude_deleted_outlets=exclude_deleted_outlets,
            order_status_in=list(FULFILLED_STATUSES),
        ),
        rows=[
            MetricRow(
                dimension=r["dim_code"], dimension_label=r["dim_label"],
                metrics={"ordered_qty": r["ordered_qty"], "delivered_qty": r["delivered_qty"],
                         "fill_rate_pct": r["fill_rate_pct"]},
            )
            for r in rows
        ],
        caveats=[
            "Fill rate is reported in eaches, not cases -- a locked decision. The brief's illustrative "
            "Q1 says 'case fill rate' but Rakesh Menon's follow-up email explicitly requires eaches "
            "('modern trade penalise us on units short, not cases short'); this API implements that "
            "resolution uniformly, including when answering Q1 itself. See DECISIONS.md.",
            "Fill rate is scoped to orders with status DELIVERED or PARTIAL; "
            "CANCELLED and still-OPEN orders are excluded as a deliberate choice, not a brief requirement.",
        ],
    )


@router.get("/otif", response_model=MetricResponse)
def otif(
    group_by: Literal["region", "warehouse", "route"] = Query("region"),
    fiscal_year: Optional[int] = None,
    fiscal_quarter: Optional[int] = Query(None, ge=1, le=4),
    month: Optional[str] = None,
    on_time_threshold_minutes: int = Query(0, description="delay_minutes <= this counts as on-time"),
    order: Literal["asc", "desc"] = Query("asc"),
    limit: int = Query(50, ge=1, le=500),
):
    cfg = _DIM_CONFIG[group_by]
    period_sql, period_params, period_label = period_filter(fiscal_year, fiscal_quarter, month, "oa.order_date")
    where_extra = period_sql or "AND 1=1"

    sql = f"""
        WITH order_agg AS (
            SELECT order_id, MIN(order_date) AS order_date,
                   SUM(ordered_eaches) AS ordered_eaches,
                   SUM(delivered_eaches) AS delivered_eaches
            FROM fact_order_lines
            WHERE order_status IN {FULFILLED_STATUSES}
            GROUP BY order_id
        ),
        otif_base AS (
            SELECT d.delivery_id, d.region_id, d.warehouse_id, d.route_id,
                   (d.delay_minutes <= ?) AS is_on_time,
                   -- Strict, textbook in-full: no tolerance. See module
                   -- docstring and DECISIONS.md -- this is close to 0%
                   -- almost everywhere in this dataset, and that is a
                   -- reported finding, not a bug to thresholds away.
                   (oa.delivered_eaches >= oa.ordered_eaches) AS is_in_full,
                   (100.0 * oa.delivered_eaches / NULLIF(oa.ordered_eaches, 0)) AS fulfilment_pct
            FROM fact_deliveries d
            JOIN order_agg oa ON oa.order_id = d.order_id
            WHERE 1=1 {where_extra}
        )
        SELECT dm.{cfg['code_col']} AS dim_code, dm.{cfg['label_col']} AS dim_label,
               COUNT(*) AS n_deliveries,
               SUM(CASE WHEN is_on_time AND is_in_full THEN 1 ELSE 0 END) AS n_otif_strict,
               ROUND(100.0 * SUM(CASE WHEN is_on_time AND is_in_full THEN 1 ELSE 0 END) / COUNT(*), 2) AS otif_pct_strict,
               ROUND(100.0 * SUM(CASE WHEN is_on_time THEN 1 ELSE 0 END) / COUNT(*), 1) AS on_time_pct,
               ROUND(100.0 * SUM(CASE WHEN is_in_full THEN 1 ELSE 0 END) / COUNT(*), 2) AS in_full_pct_strict,
               ROUND(AVG(fulfilment_pct), 1) AS avg_fulfilment_pct
        FROM otif_base b
        JOIN {cfg['table']} dm ON dm.{cfg['id_col']} = b.{cfg['id_col']}
        GROUP BY 1, 2
        ORDER BY otif_pct_strict {order}
        LIMIT ?
    """
    with get_connection() as con:
        rows = run_query(con, sql, [on_time_threshold_minutes] + period_params + [limit])

    return MetricResponse(
        metric="otif",
        group_by=group_by,
        period_label=period_label,
        filters_applied={"on_time_threshold_minutes": on_time_threshold_minutes,
                          "in_full_definition": "strict: delivered_eaches >= ordered_eaches, no tolerance",
                          "order_status_in": list(FULFILLED_STATUSES)},
        rows=[
            MetricRow(
                dimension=r["dim_code"], dimension_label=r["dim_label"],
                metrics={"n_deliveries": r["n_deliveries"], "n_otif_strict": r["n_otif_strict"],
                         "otif_pct_strict": r["otif_pct_strict"], "on_time_pct": r["on_time_pct"],
                         "in_full_pct_strict": r["in_full_pct_strict"],
                         "avg_fulfilment_pct": r["avg_fulfilment_pct"]},
            )
            for r in rows
        ],
        caveats=[
            "otif_pct_strict / in_full_pct_strict use NO tolerance (delivered_eaches >= ordered_eaches, "
            "summed across the order's lines) -- verified this is close to 0% almost everywhere: 0 of "
            "511,516 order lines, across every order_status including DELIVERED, ever reach 100% "
            "fulfilment, and the 99th percentile of order-level fulfilment is 94.8%. This is a reported "
            "finding about the data, not tuned away with an arbitrary tolerance -- see DECISIONS.md. "
            "avg_fulfilment_pct (mean delivered/ordered ratio per order) is included as the actionable, "
            "non-boolean substitute; Kestrel's own trade-shrinkage tolerance policy would be needed to "
            "define a meaningful in-full threshold, and that's a business decision we don't have.",
            "on_time_pct uses the delivery's own delay_minutes field. Checked: delay_minutes does NOT "
            "reconcile with actual_arrival - planned_arrival computed from the timestamp columns "
            "(87% of rows disagree, including sign). delay_minutes is used as-is because the data "
            "dictionary defines it directly ('signed, negative means early') without tying it to those "
            "two specific timestamp fields -- recomputing our own delay from timestamps that don't "
            "agree with the documented field would be substituting our guess for the source data.",
            "Assumes one delivery per order, which holds for all DELIVERED/PARTIAL orders in this dataset.",
        ],
    )
