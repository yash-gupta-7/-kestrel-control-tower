"""
Competitor price position, from BazaarPulse listings matched to Kestrel
SKUs. Every endpoint here filters to match_confidence = 'matched' only, per
explicit scope decision -- the ~46% of listings flagged 'ambiguous' by the
brand+category+pack-size matcher (see build_warehouse.py) are never
surfaced as if they were a confident SKU-level comparison. Lowest build
priority (5%) by plan; kept intentionally small.
"""
from typing import Optional

from fastapi import APIRouter, Query

from ..db import get_connection, run_query
from ..schemas import MetricResponse, MetricRow

router = APIRouter(prefix="/price-position", tags=["price-position"])


@router.get("/gap", response_model=MetricResponse)
def price_gap(
    city: Optional[str] = Query(None, description="e.g. mumbai, delhi, bengaluru, chennai"),
    top_n_skus_by_value: int = Query(20, ge=1, le=100),
):
    with get_connection() as con:
        city_filter = "AND LOWER(pp.city) = LOWER(?)" if city else ""
        params = [city] if city else []
        sql = f"""
            WITH top_skus AS (
                SELECT sku_code, product_name, SUM(line_value_inr) AS value_inr
                FROM fact_order_lines
                WHERE order_status IN ('DELIVERED', 'PARTIAL')
                GROUP BY 1, 2
                ORDER BY value_inr DESC
                LIMIT ?
            ),
            competitor AS (
                SELECT sku_code, MIN(price_inr) AS lowest_competitor_price_inr,
                       COUNT(DISTINCT retailer) AS n_retailers_observed
                FROM fact_price_position pp
                WHERE match_confidence = 'matched' {city_filter}
                GROUP BY 1
            )
            SELECT t.sku_code AS dim_code, t.product_name AS dim_label,
                   t.value_inr AS kestrel_dispatch_value_inr,
                   dp.mrp_inr AS kestrel_mrp_inr,
                   c.lowest_competitor_price_inr, c.n_retailers_observed,
                   ROUND(dp.mrp_inr - c.lowest_competitor_price_inr, 2) AS gap_inr,
                   ROUND(100.0 * (dp.mrp_inr - c.lowest_competitor_price_inr) / NULLIF(dp.mrp_inr, 0), 1) AS gap_pct
            FROM top_skus t
            JOIN dim_product dp ON dp.sku_code = t.sku_code
            LEFT JOIN competitor c ON c.sku_code = t.sku_code
            ORDER BY t.value_inr DESC
        """
        rows = run_query(con, sql, [top_n_skus_by_value] + params)

    n_no_match = sum(1 for r in rows if r["lowest_competitor_price_inr"] is None)
    return MetricResponse(
        metric="mrp_vs_lowest_competitor_price",
        group_by="sku",
        period_label="all available data (Jan 2025 - Jun 2026), competitor prices as last scraped",
        filters_applied={"city": city, "top_n_skus_by_value": top_n_skus_by_value},
        rows=[MetricRow(dimension=r["dim_code"], dimension_label=r["dim_label"],
                         metrics={k: v for k, v in r.items() if k not in ("dim_code", "dim_label")})
              for r in rows],
        caveats=[
            f"{n_no_match} of {len(rows)} top-value SKUs have no confidently-matched competitor listing"
            + (f" in {city}" if city else "") + " -- shown as null rather than guessed.",
            "Only listings with match_confidence='matched' are used (brand + category + normalised pack "
            "size agree and only one Kestrel SKU fits); ambiguous matches are excluded entirely from this view.",
        ],
    )


@router.get("/summary", response_model=MetricResponse)
def price_summary(city: Optional[str] = None, category: Optional[str] = None):
    filters = ["match_confidence = 'matched'"]
    params: list = []
    if city:
        filters.append("LOWER(city) = LOWER(?)")
        params.append(city)
    if category:
        filters.append("LOWER(category) = LOWER(?)")
        params.append(category)

    sql = f"""
        SELECT category AS dim_code, category AS dim_label,
               COUNT(*) AS n_matched_listings,
               ROUND(AVG(mrp_gap_pct), 1) AS avg_gap_pct,
               ROUND(MIN(mrp_gap_pct), 1) AS min_gap_pct,
               ROUND(MAX(mrp_gap_pct), 1) AS max_gap_pct
        FROM fact_price_position
        WHERE {' AND '.join(filters)}
        GROUP BY 1
        ORDER BY avg_gap_pct DESC
    """
    with get_connection() as con:
        rows = run_query(con, sql, params)

    return MetricResponse(
        metric="price_gap_by_category",
        group_by="category",
        period_label="competitor prices as last scraped",
        filters_applied={"city": city, "category": category},
        rows=[MetricRow(dimension=r["dim_code"], dimension_label=r["dim_label"],
                         metrics={k: v for k, v in r.items() if k not in ("dim_code", "dim_label")})
              for r in rows],
        caveats=["mrp_gap_pct > 0 means Kestrel's MRP is higher than the observed street price."],
    )
