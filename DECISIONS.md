# Decisions

## What we built
A control tower over Kestrel's service, money, cold chain and price-position numbers, plus a plain-English "Ask Anything" box — a React/Vite dashboard over a FastAPI backend over a single-file DuckDB warehouse, built once by a batch ETL pipeline from the operational SQLite DB, a scraped competitor-price site, a mock freight API, and (optionally) public weather data. Runs from a clean checkout with `docker compose up --build`.

## Key architectural choices
Two halves. A batch ETL container cleans and reconciles the operational, competitor-price, freight, and optional weather sources into a documented fact/dim warehouse — the raw operational tables are not carried into the serving warehouse, so downstream application queries never depend on the raw operational schema. An application half (FastAPI + DuckDB + React) only ever runs read-only queries. Docker Compose health-gates startup so the backend won't come up behind a failed ETL run, or a warehouse that failed its own post-build data-quality gate (`etl/validate_warehouse.py` — required tables, row counts, key integrity, date-range sanity). Sized for this problem: a fixed historical dataset, read-only analytics, no concurrent-write requirement.

## Assumptions and ambiguities, resolved
**Fill rate: eaches, not cases.** The brief's own Q1 text says "case fill rate," but Rakesh Menon's follow-up explicitly overrides it — modern trade penalises Kestrel on units short, and mixed-configuration SKUs make cases ambiguous. Applied uniformly, including to Q1 itself, with the resolution stated in the answer rather than silently switching units.

**OTIF: no invented tolerance.** Investigated, not assumed: 0 of 511,516 order lines ever fully deliver against what was ordered — every line carries some shortfall by data design. A strict, 100%-bar OTIF therefore reads near-zero almost everywhere; reported honestly rather than tuned to a threshold that would make it look normal. On-time % and average fulfilment % are shown alongside as the usable signal — a real in-full tolerance is a business decision only Kestrel can supply.

**Fiscal year:** labelled by the year it ends (FY runs Apr–Mar), so Apr–Jun 2026 is FY2027 Q1.

**Regional-manager view:** a `region_code` filter threaded through Service, Money, Cold Chain and Ask Anything, not a new auth/permission system — consistent with this being a single-tenant ops tool. Price Position is the one exception: competitor listings are scraped by city, which has no mapping to Kestrel's sales regions, so that page shows an explicit callout instead of pretending to filter.

**Price position: conservative matching.** Listings matched by (brand, category, normalised pack size) with a name-overlap tiebreak; just over half match with confidence, the rest are excluded from every price-gap view rather than guessed.

## Deliberately not built
Authentication/roles (a region filter, not per-user access control); a general data-quality framework (one gate file, 37 checks, not a platform); a second LLM call to re-phrase Groq's answer against actual result rows; a query planner, SQL-rewriter, or extra model to fix semantically-imprecise LLM-generated SQL (an acknowledged NL-to-SQL limitation, contained by fast-path priority, a restricted schema, and SQL visibility rather than "solved"); and any multi-tenant infrastructure. None of it is required at this scale or by the brief.

## Data-quality decisions
Two source-data issues were investigated and handled explicitly rather than guessed at: `delay_minutes` disagrees with recomputed arrival timestamps in 87% of deliveries (used the documented field, not a derivation the data itself doesn't corroborate), and the source `ageing_bucket` column is uncorrelated with actual days-to-expiry (near-expiry is computed directly from `expiry_date − snapshot_date` instead). The post-ETL validation gate exists so a stale or partially-built warehouse fails closed (503) rather than silently serving wrong numbers.

## Ask Anything, privacy, and security
Two tiers: the 8 illustrative questions are deterministic, hand-written, tested queries that always win over the LLM. Anything else goes to Groq, whose generated SQL is never trusted directly — it's validated read-only and table-allowlisted by `sql_guard.py` before it can execute, with an execution timeout and row cap so one free-form question can't tie up the shared warehouse file.

Personal data (outlet contact details, salesperson/warehouse-manager/regional-manager names, government IDs) is protected by a separate, rule-based four-layer check in `privacy.py` — schema filtering (blocked columns are never described to the model), a deterministic phrase/category check on the question itself (blocks a direct request like "salesperson full names" or "warehouse manager names" before Groq is ever called), a SQL-column check, and a result-column check. Deliberately rule-based, not an LLM judging what's personal, so it's auditable. This is defense-in-depth, not a claim of mathematical perfection — the honest limitations (heuristic regexes, a text-scan rather than a real SQL parser) are documented in `privacy.py` itself.

## What breaks first in production
No auth or rate-limiting on the API. A single DuckDB file with no concurrent-write story — fine for read-only analytics at this volume, not at 100x. Per-request connection-open and full-table scans in several endpoints would need indexing or pre-aggregation at real scale. Container hardening is intentionally minimal for the take-home scope; containers currently run as root and would be hardened before production deployment. Structured server-side logging and centralized observability are outside the take-home scope and would be added for production operations.

## With two more weeks
Resolve the OTIF tolerance question with Kestrel's ops team directly, rather than leaving it a reported ambiguity. Reconcile `delay_minutes` against its true source system. Extend price matching with a real string-similarity library if the unmatched rate matters to the business. Have the LLM phrase its final answer from actual result rows instead of a pre-results intro line, and consider basic request auth before this goes anywhere near a shared environment.
