"""
Ask-anything.

Two tiers, by design (see DECISIONS.md):
  1. Fast path -- the 8 illustrative questions from the assignment brief,
     recognised by keyword matching and answered with a hand-written,
     parametrized, tested query against the approved analytical views.
     Deterministic, correct, works with zero external dependencies.
  2. LLM fallback -- for anything that doesn't match a fast path. Requires
     GROQ_API_KEY. The model is asked for a single read-only SQL query (plus
     a one-sentence answer lead-in) against the same approved analytical
     views the fast paths use; the generated SQL is never trusted directly
     -- it goes through sql_guard.validate_readonly_sql() (the same guard
     that would gate any other LLM provider) before db.run_query() ever
     touches it, so a mutation statement, a disallowed table, or a
     malformed response fails closed into mode="llm_error", not a 500 and
     not a silent wrong answer.

If no API key is configured, free-form questions get an explicit
mode="unavailable" response -- never a 500, never a silent wrong answer.

Personal data (see privacy.py) is checked at four points before any
free-form question or its result can leave this module: the question text
(blocked-request phrases and PII-value patterns, both before Groq is ever
called), the LLM-facing schema description (built with blocked columns
excluded, asserted below), the generated SQL (after sql_guard, before
execution), and the result rows (before they're returned). Any hit
degrades to mode="blocked" -- never a 500, never partial data.
"""
import json
import logging
import re
import threading
from typing import Optional

from fastapi import APIRouter, HTTPException

from .. import config, date_utils, privacy
from ..db import ALLOWED_TABLES, data_max_order_date, get_connection, run_query
from ..query_helpers import region_filter
from ..schemas import AskRequest, AskResult, MetricResponse, SupportedQuestion
from ..sql_guard import SQLGuardError, validate_readonly_sql
from . import cold_chain, money, price_position, service

router = APIRouter(prefix="/ask", tags=["ask"])
logger = logging.getLogger(__name__)

_BLOCKED_ANSWER = "Sorry, personal data cannot be queried through Ask Anything."


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
    # NOT o.is_test_outlet -- the same canonical test/migration-outlet flag
    # (etl/build_warehouse.py, KP-2377) that fill_rate/otif already exclude
    # by default; this endpoint used to leave it out, so known dummy/test
    # outlets (e.g. "DO NOT USE - migration dummy") outranked real outlets
    # in the drill-down table. Found in final release review.
    sql = f"""
        SELECT o.outlet_code AS outlet_code, o.outlet_name AS outlet_name,
               ol.sku_code, ol.product_name, COUNT(*) AS n_order_lines,
               MIN(ol.order_date) AS first_order_after_discontinuation,
               MAX(ol.order_date) AS last_order_after_discontinuation
        FROM fact_order_lines ol
        JOIN dim_outlet o ON o.outlet_id = ol.outlet_id
        WHERE ol.ordered_after_discontinued AND NOT o.is_test_outlet {region_sql}
        GROUP BY 1, 2, 3, 4
        ORDER BY n_order_lines DESC
        LIMIT 20
    """
    with get_connection() as con:
        rows = run_query(con, sql, region_params)
    total_lines_sql = (
        f"SELECT COUNT(*), COUNT(DISTINCT ol.outlet_id) FROM fact_order_lines ol "
        f"JOIN dim_outlet o ON o.outlet_id = ol.outlet_id "
        f"WHERE ol.ordered_after_discontinued AND NOT o.is_test_outlet {region_sql}"
    )
    # total_outlets is scoped to the same region filter (and the same
    # test-outlet exclusion) so "systemic" compares production outlets that
    # ordered a discontinued SKU against all production outlets, not against
    # a denominator that still counts the 3 excluded test outlets.
    total_outlets_sql = f"SELECT COUNT(*) FROM dim_outlet o WHERE NOT o.is_test_outlet {region_sql}"
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
                 "does not attempt to explain why ordering continued (e.g. stale catalog caches upstream).",
                 "Excludes the 3 known test/migration outlets (is_test_outlet), the same rule "
                 "GET /service/fill-rate and GET /service/otif already apply by default."],
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


# ---------------------------------------------------------------------------
# LLM fallback (Groq). Only reached when no fast path matched.
# ---------------------------------------------------------------------------

# A compact, hand-written schema summary -- not pulled from the DB at
# request time, since ALLOWED_TABLES + this description change only when a
# person edits the warehouse build, not per-request. Kept short deliberately:
# enough for the model to write a correct join, not a full data dictionary.
# Every column named here is checked against the live warehouse.duckdb
# schema (not invented), and every PII_BLOCKED_COLUMNS entry is
# deliberately excluded -- see privacy.py for which columns those are and
# why (this is layer 1 of the privacy design: the LLM is never even told
# a blocked column exists).
_SCHEMA_DESCRIPTION = """
dim_region(region_id, region_code, region_name, status)
dim_warehouse(warehouse_id, warehouse_code, warehouse_name, city, region_id, status)
dim_route(route_id, route_code, route_name, region_id, status)
dim_salesperson(salesperson_id, employee_code, region_id, designation, status)
dim_outlet(outlet_id, outlet_code, outlet_name, city_canonical, region_id, is_test_outlet, is_deleted, status)
dim_product(product_id, sku_code, product_name, brand, category, is_chilled, is_discontinued, discontinued_date, mrp_inr)
dim_date(date, fiscal_year, fiscal_quarter)
fact_order_lines(order_line_id, order_id, order_date, order_status, outlet_id, region_id, warehouse_id, route_id,
    sku_code, product_name, category, ordered_eaches, delivered_eaches, ordered_cases, delivered_cases,
    line_value_inr, is_chilled, ordered_after_discontinued)
fact_deliveries(delivery_id, order_id, dispatch_datetime, outlet_id, region_id, warehouse_id, route_id,
    delay_minutes, temperature_excursion_flag)
fact_returns(return_id, return_date, outlet_id, region_id, category, return_reason_code, credit_note_value_inr)
fact_freight(invoice_id, invoice_date, warehouse_id, route_id, carrier_id, carrier_name, amount_inr)
fact_price_position(listing_id, sku_code, city, retailer, price_inr, mrp_gap_pct, match_confidence)
fact_inventory_snapshot(snapshot_id, snapshot_date, warehouse_id, category, on_hand_cases, days_to_expiry)
fact_weather(city, date)
""".strip()

# The schema shown to the LLM must never advertise a table the SQL guard
# wouldn't actually allow it to query -- catches the two docs/allowlist
# drifting apart, at import time rather than at question-answering time.
# Table names start a line with no leading whitespace (`^\w+\(`); wrapped
# continuation lines for wide tables are indented, so they don't match.
assert set(re.findall(r"^(\w+)\(", _SCHEMA_DESCRIPTION, re.MULTILINE)) <= ALLOWED_TABLES

# Privacy layer 1: the schema description must never mention a blocked
# personal-data column, by name, anywhere -- checked at import time so a
# future edit that accidentally re-adds e.g. `regional_manager` or
# `contact_email` fails the container's own startup rather than shipping.
assert not (privacy.ALL_BLOCKED_COLUMNS & set(re.findall(r"[\w]+", _SCHEMA_DESCRIPTION)))


class GroqCallError(Exception):
    """The Groq API call itself failed, or its response wasn't the
    {"sql": ..., "answer_intro": ...} shape asked for -- distinct from the
    SQL it returned failing the guard, which is a SQLGuardError instead."""


class AskQueryTimeoutError(Exception):
    """LLM-generated SQL didn't finish within ASK_QUERY_TIMEOUT_SECONDS and
    was cancelled. Distinct from a normal execution failure (HTTPException
    from db.run_query) so ask() can give a specific, honest message instead
    of the generic "failed to execute" one."""


def _run_llm_sql_with_timeout(con, sql: str, timeout_seconds: float) -> list[dict]:
    """Executes LLM-generated `sql` via db.run_query() on a worker thread,
    and cancels it with DuckDB's cross-thread Connection.interrupt() if it
    hasn't finished within timeout_seconds. Fast-path queries never go
    through this -- they're hand-written, known-cheap, and already bounded
    (small LIMITs or an inherently small result). This exists specifically
    because Groq-generated SQL is unpredictable: an accidental cross join
    or an unindexed aggregate over the largest fact table could otherwise
    tie up the shared warehouse file for one free-form question.

    Verified behaviour (DuckDB 1.5.x): interrupt() on a connection that's
    executing on another thread raises a catchable error on that thread
    within roughly the polling interval, not after the query would have
    finished naturally -- this is a real cancellation, not a client-side
    give-up that leaves the query running server-side."""
    outcome: dict = {}

    def _target():
        try:
            outcome["rows"] = run_query(con, sql)
        except BaseException as e:  # noqa: BLE001 -- re-raised on the caller's thread below
            outcome["error"] = e

    worker = threading.Thread(target=_target, daemon=True)
    worker.start()
    worker.join(timeout_seconds)
    if worker.is_alive():
        con.interrupt()
        worker.join(timeout_seconds)  # let the interrupted call unwind cleanly before returning
        raise AskQueryTimeoutError(
            f"Query did not complete within {timeout_seconds:.0f}s and was cancelled."
        )
    if "error" in outcome:
        raise outcome["error"]
    return outcome["rows"]


_groq_client = None


def _get_groq_client():
    # Imported lazily so a missing/unconfigured `groq` package only breaks
    # the LLM path, never the fast paths or app startup.
    #
    # timeout/max_retries: the groq SDK is httpx-based (same shape as the
    # OpenAI SDK) and retries transient failures -- connection errors,
    # timeouts, 429/5xx -- internally, up to max_retries times; it does not
    # retry a 4xx (e.g. a bad API key) or anything that only goes wrong
    # after a response body already came back (malformed JSON, a guard
    # rejection) -- those happen in our own code below, after this call
    # returns, and are never retried. max_retries=1 caps this at exactly
    # one retry, satisfying "at most one retry, transient failures only."
    # Without an explicit timeout the SDK's default is generous enough that
    # a hung request could sit well past what a synchronous API request
    # should ever wait -- GROQ_TIMEOUT_SECONDS bounds that explicitly.
    global _groq_client
    if _groq_client is None:
        from groq import Groq

        _groq_client = Groq(
            api_key=config.GROQ_API_KEY,
            timeout=config.GROQ_TIMEOUT_SECONDS,
            max_retries=config.GROQ_MAX_RETRIES,
        )
    return _groq_client


def _generate_sql_with_groq(question: str, region_code: Optional[str]) -> tuple[str, str]:
    """Asks Groq for a single read-only SQL query answering `question`,
    returned as {"sql": ..., "answer_intro": ...} JSON. Raises
    GroqCallError on any failure -- network, API, or malformed response.
    Does NOT validate the SQL; that's sql_guard's job, called by `ask()`."""
    region_hint = (
        f" Scope the query to region_code = '{region_code}' via the relevant table's region_id "
        "(join dim_region if needed)." if region_code else ""
    )
    system_prompt = (
        "You are a read-only SQL assistant for Kestrel's supply-chain analytics warehouse (DuckDB). "
        "Write exactly ONE SELECT or WITH...SELECT query that answers the user's question, using ONLY "
        f"these tables and columns:\n{_SCHEMA_DESCRIPTION}\n\n"
        "Rules: read-only SELECT/WITH only -- no INSERT/UPDATE/DELETE/DROP/ALTER/CREATE or any other "
        "mutation; a single statement, no trailing semicolon, no SQL comments; reference only the "
        "tables listed above (bare or qualified), never any other table." + region_hint + "\n"
        'Respond with ONLY a JSON object of the exact shape {"sql": "<the query>", '
        '"answer_intro": "<one short plain-English sentence introducing the answer, written as if you '
        'have not seen the results yet -- no specific numbers>"}. No markdown, no commentary, JSON only.'
    )
    try:
        client = _get_groq_client()
        completion = client.chat.completions.create(
            model=config.GROQ_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": question},
            ],
            response_format={"type": "json_object"},
            temperature=0,
            max_tokens=600,
        )
        content = completion.choices[0].message.content
    except Exception as e:  # noqa: BLE001 -- any Groq/network failure degrades to llm_error, never a 500
        raise GroqCallError(f"Groq request failed: {e}") from e

    try:
        parsed = json.loads(content)
        sql = parsed["sql"]
        answer_intro = parsed.get("answer_intro") or "Here's what the data shows"
        if not isinstance(sql, str) or not sql.strip():
            raise ValueError("empty or non-string 'sql' field")
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
        raise GroqCallError(f"Could not parse the model's response as the expected JSON shape: {e}") from e

    return sql, answer_intro


def _blocked(req: AskRequest, reason: str, sql: Optional[str] = None) -> AskResult:
    # Log only a safe machine token -- never the question text, never a
    # detected PII value, never the generated SQL's literal values.
    logger.warning("ask_privacy_blocked reason=%s", reason)
    return AskResult(
        question=req.question, mode="blocked",
        answer=_BLOCKED_ANSWER,
        sql=sql,
        caveats=["This build never sends personal-data fields to the AI model or returns them from "
                 "free-form questions -- see README/DECISIONS.md for the privacy design."],
    )


@router.post("", response_model=AskResult)
def ask(req: AskRequest):
    match = match_fastpath(req.question)
    if match:
        _qid, handler = match
        return handler(req.question, req.region_code)

    # Privacy layer, step 1: block a request FOR a category of personal
    # data outright (e.g. "show customer phone numbers") before Groq is
    # ever called -- runs regardless of whether a key is configured.
    try:
        privacy.check_question_for_blocked_request(req.question)
    except privacy.PrivacyBlockedError as e:
        return _blocked(req, e.reason)

    # Privacy layer, step 2: block a question containing a PII-shaped
    # VALUE (an email, phone number, Aadhaar/PAN-style ID). A question
    # naming a specific personal identifier almost always means "look
    # this contact up by X," which requires a blocked column to answer at
    # all -- rejected rather than forwarded, redacted or not (see
    # privacy.redact_pii's docstring for the reasoning and the mechanism
    # a looser policy could build on).
    pii_category = privacy.detect_pii_value_category(req.question)
    if pii_category:
        return _blocked(req, f"blocked_pii_value_{pii_category}")

    if not config.GROQ_API_KEY:
        # Business-facing message deliberately says nothing about which env
        # var gates this -- that's an implementation detail, not something
        # an ops user needs to see. See /health's llm_configured field or
        # README "Ask Anything" section for the actual configuration story.
        return AskResult(
            question=req.question, mode="unavailable",
            answer=(
                "This doesn't match one of the questions this build can answer deterministically, "
                "and AI-powered free-form questions aren't available right now. "
                "See /ask/supported-questions for what this build can answer today."
            ),
            caveats=["AI is not configured for this deployment -- only the 8 supported questions are answered."],
        )

    try:
        llm_sql, answer_intro = _generate_sql_with_groq(req.question, req.region_code)
    except GroqCallError as e:
        return AskResult(
            question=req.question, mode="llm_error",
            answer="Couldn't get an answer from the AI model for this question -- see caveats for why.",
            caveats=[str(e)],
        )

    try:
        validated_sql = validate_readonly_sql(llm_sql)
    except SQLGuardError as e:
        return AskResult(
            question=req.question, mode="llm_error",
            answer=(
                "The model's generated query didn't pass this build's read-only SQL guard, so it was "
                "never run against the database."
            ),
            sql=llm_sql,
            caveats=[str(e)],
        )

    # Privacy layer, step 3: sql_guard only allowlists TABLES, not
    # columns, so `SELECT contact_email FROM dim_outlet` passes the guard
    # (dim_outlet is approved) -- this is the check that catches it,
    # before the query is ever executed.
    try:
        privacy.check_sql_for_blocked_columns(validated_sql)
    except privacy.PrivacyBlockedError as e:
        return _blocked(req, e.reason, sql=validated_sql)

    try:
        with get_connection() as con:
            rows = _run_llm_sql_with_timeout(con, validated_sql, config.ASK_QUERY_TIMEOUT_SECONDS)
    except AskQueryTimeoutError:
        return AskResult(
            question=req.question, mode="llm_error",
            answer=(
                "This question's generated query took too long to run and was stopped before "
                "returning any data -- see caveats for the limit applied."
            ),
            sql=validated_sql,
            caveats=[f"Ask Anything queries are capped at {config.ASK_QUERY_TIMEOUT_SECONDS:.0f} seconds "
                     "to protect the shared warehouse from a single expensive query. Try a narrower "
                     "question (a shorter period, a specific region) rather than retrying as-is."],
        )
    except HTTPException as e:
        return AskResult(
            question=req.question, mode="llm_error",
            answer="The model's query passed the SQL guard but failed to execute.",
            sql=validated_sql,
            caveats=[str(e.detail)],
        )

    # Privacy layer, step 4: final defense in depth on the actual result
    # rows, independent of what the SQL text looked like.
    try:
        privacy.check_result_for_blocked_columns(rows)
    except privacy.PrivacyBlockedError as e:
        return _blocked(req, e.reason, sql=validated_sql)

    # MAX_ROWS_RETURNED (db.run_query) caps every query's result, fast-path
    # or LLM. If this one hit that cap exactly, more rows may genuinely
    # exist and were silently dropped -- flagged here rather than left
    # implicit, so "top 500 rows" isn't mistaken for "all matching rows".
    # There's no cheap way to tell "exactly N rows exist" from "truncated
    # at N" without a second COUNT(*) query for a display-only caveat, so
    # this is deliberately phrased as "may be more", not a precise count.
    truncation_caveat = (
        [f"Results were capped at {config.MAX_ROWS_RETURNED} rows -- there may be more rows than shown."]
        if len(rows) == config.MAX_ROWS_RETURNED else []
    )

    answer = f"{answer_intro} ({len(rows)} row{'s' if len(rows) != 1 else ''} returned)."
    return AskResult(
        question=req.question, mode="llm",
        answer=answer, data=rows, sql=validated_sql,
        source="LLM-generated SQL (Groq), validated read-only against the approved analytical views",
        caveats=[
            "This answer was generated by an AI model from your question, not by a human-written, "
            "tested query like the 8 supported questions -- the SQL is shown above so you can verify "
            "it before relying on the result.",
        ] + truncation_caveat + (
            ["Region scoping for free-form questions is a prompt instruction to the model, not a "
             "structural guarantee the way it is on the dashboard endpoints -- check the SQL above if "
             "the region scope matters for this answer."] if req.region_code else []
        ),
    )
