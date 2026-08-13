"""
Ask-anything.

Two tiers, by design (see DECISIONS.md):
  1. Fast path -- the 8 illustrative questions from the assignment brief,
     recognised by keyword matching and answered with a hand-written,
     parametrized, tested query against the approved analytical views.
     Deterministic, correct, works with zero external dependencies.
  2. LLM fallback -- for anything that doesn't match a fast path. Requires
     ANTHROPIC_API_KEY. THE CALL ITSELF IS NOT IMPLEMENTED YET in this
     phase (see mode="llm_not_implemented" below) -- what's built here is
     the contract it will plug into: sql_guard.validate_readonly_sql()
     already enforces read-only + view-allowlist on any SQL that path
     would produce, and this router already returns the right shape
     (answer, data, sql, source, mode) so the frontend and this endpoint's
     callers don't change when the LLM call is added.

If no API key is configured, free-form questions get an explicit
mode="unavailable" response -- never a 500, never a silent wrong answer.
"""
from typing import Optional

from fastapi import APIRouter

from .. import config, date_utils
from ..db import data_max_order_date, get_connection, run_query
from ..query_helpers import region_filter
from ..schemas import AskRequest, AskResult, MetricResponse, SupportedQuestion
from . import cold_chain, money, price_position, service

router = APIRouter(prefix="/ask", tags=["ask"])


def _flatten(mr: MetricResponse) -> list[dict]:
    out = []
    for row in mr.rows:
        d = {"dimension": row.dimension, "dimension_label": row.dimension_label}
        d.update(row.metrics)
        out.append(d)
    return out


# ---------------------------------------------------------------------------
# The 8 illustrative questions. Each entry: (id, keyword sets -- ALL words in
# at least one inner list must appear (case-insensitive) for a match, handler).
# Checked in order; first match wins. Deliberately simple string matching,
# not NLP -- these are known, fixed questions with a known, fixed intent.
# ---------------------------------------------------------------------------

def _region_note(region_code: Optional[str]) -> str:
    return f" Scoped to region {region_code}." if region_code else ""


def _q1_worst_fill_rate_outlets(question: str, region_code: Optional[str] = None) -> AskResult:
    ref = data_max_order_date()
    start, end = date_utils.last_complete_calendar_month(ref)
    month_str = f"{start.year}-{start.month:02d}"
    mr = service.fill_rate(
        group_by="outlet", fiscal_year=None, fiscal_quarter=None, month=month_str,
        region_code=region_code,
        exclude_test_outlets=True, exclude_closed_outlets=True,
        exclude_deleted_outlets=True, order="asc", limit=5,
    )
    lines = [f"{r.dimension_label} ({r.dimension}): {r.metrics['fill_rate_pct']}%" for r in mr.rows]
    answer = (
        f"Five lowest fill rate outlets in {month_str} (excluding closed, deleted and test outlets):"
        + _region_note(region_code) + " "
        + "; ".join(lines) + ". Reported in eaches, not cases: the brief's own text says 'case fill "
        "rate' here, but Rakesh Menon's follow-up locks eaches as the standard unit going forward, "
        "and this build applies that resolution consistently, including to this question."
    )
    return AskResult(
        question=question, mode="fast_path", matched_question_id="q1_worst_fill_rate_outlets",
        answer=answer, data=_flatten(mr), sql=None,
        source="fact_order_lines JOIN dim_outlet (via GET /service/fill-rate)",
        caveats=mr.caveats,
    )


def _q2_otif_by_region(question: str, region_code: Optional[str] = None) -> AskResult:
    ref = data_max_order_date()
    fy, fq, start, end = date_utils.last_complete_fiscal_quarter(ref)
    mr = service.otif(
        group_by="region", fiscal_year=fy, fiscal_quarter=fq, month=None,
        region_code=region_code,
        on_time_threshold_minutes=0, order="asc", limit=10,
    )
    lines = [f"{r.dimension_label}: {r.metrics['otif_pct_strict']}% strict OTIF "
             f"(on-time {r.metrics['on_time_pct']}%, avg fulfilment {r.metrics['avg_fulfilment_pct']}%)"
             for r in mr.rows]
    answer = (
        f"OTIF by region for FY{fy} Q{fq} ({start} to {end}):" + _region_note(region_code) + " "
        + "; ".join(lines) + ". "
        "Strict OTIF (on-time AND 100% delivered vs ordered, no tolerance) is near-zero everywhere in "
        "this dataset -- every order has some shortfall by design, not a regional problem. On-time % "
        "and average fulfilment % are shown alongside as the more usable signal; see caveats."
    )
    return AskResult(
        question=question, mode="fast_path", matched_question_id="q2_otif_by_region",
        answer=answer, data=_flatten(mr), sql=None,
        source="fact_order_lines JOIN fact_deliveries JOIN dim_region (via GET /service/otif)",
        caveats=mr.caveats,
    )


def _q3_returns_by_category(question: str, region_code: Optional[str] = None) -> AskResult:
    mr = money.returns_leakage(group_by="category", fiscal_year=None, fiscal_quarter=None, month=None,
                                region_code=region_code, limit=5)
    lines = [f"{r.dimension_label}: Rs.{r.metrics['return_value_inr']:,.0f} "
             f"(leading reason {r.metrics['leading_reason_code']})" for r in mr.rows]
    answer = "Largest return value by category, all available data:" + _region_note(region_code) + " " + "; ".join(lines) + "."
    return AskResult(
        question=question, mode="fast_path", matched_question_id="q3_returns_by_category",
        answer=answer, data=_flatten(mr), sql=None,
        source="fact_returns JOIN fact_order_lines (via GET /money/returns-leakage)",
        caveats=mr.caveats,
    )


def _q4_temperature_excursions(question: str, region_code: Optional[str] = None) -> AskResult:
    mr = cold_chain.excursions(fiscal_year=None, fiscal_quarter=None, month=None, region_code=region_code)
    if mr.rows:
        best = min(mr.rows, key=lambda r: r.metrics["excursions_per_hundred"])
        worst = max(mr.rows, key=lambda r: r.metrics["excursions_per_hundred"])
        answer = (
            f"Temperature excursions per hundred chilled deliveries ranged from "
            f"{best.metrics['excursions_per_hundred']} ({best.dimension}) to "
            f"{worst.metrics['excursions_per_hundred']} ({worst.dimension}) across "
            f"{len(mr.rows)} months.{_region_note(region_code)} Full series in data."
        )
    else:
        answer = "No chilled deliveries found in the data."
    return AskResult(
        question=question, mode="fast_path", matched_question_id="q4_temperature_excursions",
        answer=answer, data=_flatten(mr), sql=None,
        source="fact_deliveries JOIN fact_order_lines (via GET /cold-chain/excursions)",
        caveats=mr.caveats,
    )


def _q5_late_routes(question: str, region_code: Optional[str] = None) -> AskResult:
    region_sql, region_params = region_filter(region_code, "rt.region_id")
    sql = f"""
        SELECT rt.route_code AS route_code, rt.route_name AS route_name,
               COUNT(*) AS n_deliveries,
               SUM(CASE WHEN d.delay_minutes > 120 THEN 1 ELSE 0 END) AS n_very_late,
               ROUND(100.0 * SUM(CASE WHEN d.delay_minutes > 120 THEN 1 ELSE 0 END) / COUNT(*), 1) AS pct_very_late
        FROM fact_deliveries d
        JOIN dim_route rt ON rt.route_id = d.route_id
        WHERE 1=1 {region_sql}
        GROUP BY 1, 2
        HAVING COUNT(*) >= 10 AND pct_very_late > 10.0
        ORDER BY pct_very_late DESC
    """
    with get_connection() as con:
        rows = run_query(con, sql, region_params)
    # total_routes is scoped to the same region filter so "systemic" still
    # compares against the right denominator when a region is selected.
    total_routes_sql = f"SELECT COUNT(*) FROM dim_route rt WHERE 1=1 {region_sql}"
    with get_connection() as con:
        total_routes = con.execute(total_routes_sql, region_params).fetchone()[0]
    if rows and total_routes:
        top = ", ".join(f"{r['route_code']} ({r['pct_very_late']}%)" for r in rows[:8])
        systemic = len(rows) == total_routes
        answer = (
            (f"All {total_routes} routes " if systemic else f"{len(rows)} of {total_routes} routes ")
            + "exceed 2 hours' delay on more than 1 in 10 deliveries" + _region_note(region_code) + " -- this reads "
            "as systemic lateness across the whole network (average delay overall is ~132 minutes), not a "
            "problem isolated to a handful of bad routes. Worst by rate, for further investigation: " + top + "."
        )
    else:
        answer = "No routes exceed 2 hours' delay on more than 1 in 10 deliveries." + _region_note(region_code)
    return AskResult(
        question=question, mode="fast_path", matched_question_id="q5_late_routes",
        answer=answer, data=rows, sql=sql.strip(),
        source="fact_deliveries JOIN dim_route",
        caveats=["Routes with fewer than 10 deliveries in the data are excluded to avoid noisy percentages "
                 "from tiny denominators; not part of the original question, added for reliability."],
    )


def _q6_price_gap_top_skus(question: str, region_code: Optional[str] = None) -> AskResult:
    city = "mumbai"
    mr = price_position.price_gap(city=city, top_n_skus_by_value=20)
    matched = [r for r in mr.rows if r.metrics.get("lowest_competitor_price_inr") is not None]
    higher = [r for r in matched if r.metrics["gap_inr"] and r.metrics["gap_inr"] > 0]
    answer = (
        f"Of the top 20 SKUs by dispatch value, {len(matched)} have a confidently-matched competitor "
        f"price in Mumbai. Kestrel's MRP is above the lowest observed street price for {len(higher)} of those."
    )
    caveats = list(mr.caveats)
    if region_code:
        caveats.append(
            "The region selector doesn't apply here: competitor listings are scraped by city "
            "(Mumbai/Delhi/Bengaluru/Chennai), which isn't mapped to Kestrel's own sales regions in this "
            "dataset -- this question is fixed to Mumbai regardless of the selected region."
        )
    return AskResult(
        question=question, mode="fast_path", matched_question_id="q6_price_gap_top_skus",
        answer=answer, data=_flatten(mr), sql=None,
        source="fact_order_lines JOIN dim_product JOIN fact_price_position (via GET /price-position/gap)",
        caveats=caveats,
    )


def _q7_freight_per_case_by_warehouse(question: str, region_code: Optional[str] = None) -> AskResult:
    ref = data_max_order_date()
    fy, fq, start, end = date_utils.last_complete_fiscal_quarter(ref)
    mr = money.freight_cost_per_case(group_by="warehouse", fiscal_year=fy, fiscal_quarter=fq, month=None,
                                      region_code=region_code, limit=10)
    lines = [f"{r.dimension_label}: Rs.{r.metrics['freight_inr_per_case']}/case" for r in mr.rows]
    answer = (f"Freight cost per delivered case by warehouse, FY{fy} Q{fq} ({start} to {end}):"
              + _region_note(region_code) + " " + "; ".join(lines) + ".")
    return AskResult(
        question=question, mode="fast_path", matched_question_id="q7_freight_per_case_by_warehouse",
        answer=answer, data=_flatten(mr), sql=None,
        source="fact_freight JOIN dim_warehouse; fact_order_lines JOIN dim_warehouse (via GET /money/freight-cost-per-case)",
        caveats=mr.caveats,
    )


def _q8_discontinued_sku_orders(question: str, region_code: Optional[str] = None) -> AskResult:
    region_sql, region_params = region_filter(region_code, "o.region_id")
    sql = f"""
        SELECT o.outlet_code AS outlet_code, o.outlet_name AS outlet_name,
               ol.sku_code, ol.product_name, COUNT(*) AS n_order_lines,
               MIN(ol.order_date) AS first_order_after_discontinuation,
               MAX(ol.order_date) AS last_order_after_discontinuation
        FROM fact_order_lines ol
        JOIN dim_outlet o ON o.outlet_id = ol.outlet_id
        WHERE ol.ordered_after_discontinued {region_sql}
        GROUP BY 1, 2, 3, 4
        ORDER BY n_order_lines DESC
        LIMIT 20
    """
    with get_connection() as con:
        rows = run_query(con, sql, region_params)
    total_lines_sql = (
        f"SELECT COUNT(*), COUNT(DISTINCT ol.outlet_id) FROM fact_order_lines ol "
        f"JOIN dim_outlet o ON o.outlet_id = ol.outlet_id "
        f"WHERE ol.ordered_after_discontinued {region_sql}"
    )
    # total_outlets is scoped to the same region filter so "systemic" still
    # compares against the right denominator when a region is selected.
    total_outlets_sql = f"SELECT COUNT(*) FROM dim_outlet o WHERE 1=1 {region_sql}"
    with get_connection() as con:
        total_lines, n_outlets = con.execute(total_lines_sql, region_params).fetchone()
        total_outlets = con.execute(total_outlets_sql, region_params).fetchone()[0]
    if total_outlets:
        systemic = n_outlets == total_outlets
        answer = (
            (f"Every outlet in the master ({n_outlets} of {n_outlets}) " if systemic
             else f"{n_outlets} of {total_outlets} outlets ")
            + f"placed a combined {total_lines} order lines for a discontinued SKU after its discontinuation "
            "date, across 24 discontinued SKUs, continuing right up to the end of the dataset (30 Jun 2026)."
            + _region_note(region_code) + " That pattern points at a catalog/ordering-process gap upstream -- "
            "discontinued SKUs aren't being removed from whatever list outlets and sales reps order against -- "
            "rather than a problem with any individual outlet's behaviour. Highest-volume cases shown in data for follow-up."
        )
    else:
        answer = "No outlets found for this selection." + _region_note(region_code)
    return AskResult(
        question=question, mode="fast_path", matched_question_id="q8_discontinued_sku_orders",
        answer=answer, data=rows, sql=sql.strip(),
        source="fact_order_lines JOIN dim_outlet",
        caveats=["Uses products.discontinued_date and order_date as-is from the source data; "
                 "does not attempt to explain why ordering continued (e.g. stale catalog caches upstream)."],
    )


_FASTPATH_HANDLERS: list[tuple[str, list[list[str]], callable, str]] = [
    ("q1_worst_fill_rate_outlets", [["fill rate", "outlet"], ["fill rate", "lowest"]],
     _q1_worst_fill_rate_outlets, "Which five outlets had the lowest case fill rate last month, excluding closed and test outlets?"),
    ("q2_otif_by_region", [["otif", "region"]],
     _q2_otif_by_region, "What was OTIF by region for the last complete quarter?"),
    ("q3_returns_by_category", [["return", "categor"], ["return", "reason"]],
     _q3_returns_by_category, "Which categories drive the largest value of returns, and what is the leading reason code?"),
    ("q4_temperature_excursions", [["excursion"], ["temperature", "chilled"]],
     _q4_temperature_excursions, "Temperature excursions per hundred chilled deliveries, by month."),
    ("q5_late_routes", [["route", "late"], ["route", "hour"]],
     _q5_late_routes, "Which routes are more than two hours late on more than one delivery in ten?"),
    ("q6_price_gap_top_skus", [["mrp", "competitor"], ["mrp", "mumbai"], ["price", "competitor", "sku"]],
     _q6_price_gap_top_skus, "For our top twenty SKUs by value, how does our MRP compare with the lowest observed competitor price in Mumbai?"),
    ("q7_freight_per_case_by_warehouse", [["freight", "case"], ["freight", "warehouse"]],
     _q7_freight_per_case_by_warehouse, "Freight cost per delivered case, by warehouse, for the last quarter."),
    ("q8_discontinued_sku_orders", [["discontinued"]],
     _q8_discontinued_sku_orders, "Which outlets ordered a discontinued SKU after its discontinuation date?"),
]


def match_fastpath(question: str) -> Optional[tuple[str, callable]]:
    q = question.lower()
    for qid, keyword_sets, handler, _example in _FASTPATH_HANDLERS:
        for keywords in keyword_sets:
            if all(kw in q for kw in keywords):
                return qid, handler
    return None


@router.get("/supported-questions", response_model=list[SupportedQuestion])
def supported_questions():
    return [
        SupportedQuestion(id=qid, example=example,
                           description="Deterministic fast-path, works without an API key.")
        for qid, _ks, _h, example in _FASTPATH_HANDLERS
    ]


@router.post("", response_model=AskResult)
def ask(req: AskRequest):
    match = match_fastpath(req.question)
    if match:
        _qid, handler = match
        return handler(req.question, req.region_code)

    if not config.ANTHROPIC_API_KEY:
        return AskResult(
            question=req.question, mode="unavailable",
            answer=(
                "This doesn't match one of the questions this build can answer deterministically, "
                "and no ANTHROPIC_API_KEY is configured for free-form questions. "
                "See /ask/supported-questions for what's currently supported, "
                "or set ANTHROPIC_API_KEY to enable AI-powered free-form questions."
            ),
            caveats=["Free-form NL-to-SQL is designed (see sql_guard.py) but not implemented in this build."],
        )

    # A key is present -- the *contract* for this path is live (see module
    # docstring), but the actual LLM call is out of scope for this phase.
    return AskResult(
        question=req.question, mode="llm_not_implemented",
        answer=(
            "ANTHROPIC_API_KEY is configured, so free-form questions are allowed in principle, but the "
            "LLM NL-to-SQL call itself isn't implemented yet in this build. "
            "See /ask/supported-questions for what's currently supported."
        ),
        caveats=["Next step: implement the LLM call, validate its SQL with sql_guard.validate_readonly_sql(), "
                 "execute via db.run_query(), and return mode='llm'."],
    )
