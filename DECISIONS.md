# Decisions

*All phases complete — data/ETL, backend, frontend, Docker packaging, README, and QA. Trimmed to one page below.*

## What's built so far
A DuckDB warehouse (`etl/build_warehouse.py`) cleaning all 13 raw tables plus BazaarPulse scrape + freight API pull + inventory snapshots into documented fact/dim tables; a FastAPI backend serving read-only analytics over it (no route or query touches the raw operational tables — the raw schema is physically dropped after the warehouse build); a React/Vite dashboard (Overview, Service, Money, Cold Chain, Price Position, Ask Anything) consuming that API; and Docker Compose packaging five services (mock BazaarPulse site, mock partner API, one-shot ETL job, backend, frontend) with health-gated startup order, documented in the README.

## Priority allocation
Fill Rate/OTIF 30%, Ask-anything 30%, Freight+Returns 25%, Cold Chain 10%, Price Position 5% — set explicitly, depth follows this order.

## Judgement calls

**Fill rate: eaches, not cases — locked, no toggle in the API.** The brief's own illustrative Q1 says "case fill rate," but Rakesh Menon's follow-up email explicitly overrides it: modern trade penalises Kestrel on units short, not cases short, and mixed-configuration SKUs make cases ambiguous. We apply eaches uniformly, including when literally answering Q1, and say so in the answer rather than silently switching units.

**OTIF: no in-full tolerance threshold.** Investigated rather than assumed: 0 of 511,516 order lines — across every order_status including DELIVERED — ever have delivered_eaches >= ordered_eaches; 99th percentile of order-level fulfilment is 94.8%. Every line carries a real shortfall by data design. A blended OTIF at the strict 100% bar is therefore ~0% almost everywhere. We report it that way — not tuned to an arbitrary tolerance to make it "look normal" — and expose on-time % and avg fulfilment % (continuous, not boolean) alongside it as the usable signal. Kestrel's own shrinkage-tolerance policy would be needed to define a real in-full threshold; that's a business decision we don't have.

**delay_minutes used as-is, not recomputed from timestamps.** Checked: delay_minutes disagrees with (actual_arrival − planned_arrival) computed from the parsed timestamp columns in 87% of deliveries, including sign. Used the documented field (data dictionary defines its sign convention directly) rather than substitute our own derivation that the data itself doesn't corroborate.

**Fiscal year: year-ending label.** FY runs April–March; labelled by the year it ends in (Indian convention), so Apr–Jun 2026 is **FY2027 Q1**, not FY2026 Q1. Verified across all quarters, not just one.

**Near-expiry: implemented, not cut.** `inventory_snapshots` was outside the initial warehouse build; a quick feasibility pass showed it was one clean fact table (weekly, expiry_date always populated, no format surprises) — added `fact_inventory_snapshot` and `GET /cold-chain/near-expiry`. Near-expiry is computed from `expiry_date − snapshot_date` directly, **not** from the source `ageing_bucket` column: checked, and that column is uncorrelated with actual days-to-expiry (all four buckets average ~100 days regardless of label) — it's carried through for reference but not trusted.

**Price position: conservative matching, not loosened.** BazaarPulse listings are matched to Kestrel SKUs by (brand, category, normalised pack size), with a product-name-overlap tiebreak. 614 of 1,137 listings match with confidence; the remaining 523 stay `ambiguous` and are excluded from every price-gap view rather than guessed. This rate was not inflated on review.

**Q5 (late routes) and Q8 (discontinued-SKU orders): reported as systemic, not per-entity blame.** All 140 routes exceed ">2hr late on >10% of deliveries" (avg delay ~132 min network-wide); all 724 outlets have at least one post-discontinuation order line across 24 discontinued SKUs, continuing to the end of the dataset. Both are framed as process/network findings, with worst-offender rankings included for follow-up, not as "these specific entities are broken."

**Known-issue log spot-check.** KP-2301 (order header vs. line value mismatch) did not reproduce — gross ties to `sum(line_value_inr)` exactly; net differs only by discount/tax as expected. Not chased further.

**Data cleanup applied once, in the warehouse build:** city spelling (KP-2288), outlet dedup by GST/geo not name (KP-2211, ~6 outlets, not hundreds), test-outlet exclusion by name pattern (KP-2377, exactly 3), return-quantity sign correction (KP-2402, abs()), per-source timestamp parsing (3 order-creation formats, 2 telematics formats).

## Not yet built
LLM NL-to-SQL call (contract and read-only guard exist; the Anthropic API call itself is deliberately deferred — works with or without a key, never fails).

## Bugs found and fixed during verification

**`/service/fill-rate` 500 on `group_by=outlet` + fiscal period (Phase 4 pass).** The period-filter stitching used `period_sql.replace("AND ", "")`, which strips every "AND " in the string, not just the leading one, corrupting `BETWEEN ? AND ?` into `BETWEEN ? ?`. Not covered by the Phase 3 checkpoint's tests (only tested with `month`, never with fiscal-quarter params, on this endpoint). Found by a full endpoint sweep before shipping the frontend; fixed with `.removeprefix("AND ")`.

**Out-of-range `fiscal_quarter` caused a raw 500 instead of a clean validation error (Phase 5 pass).** `fiscal_quarter` was typed as an unconstrained `Optional[int]` on four endpoints (`/service/fill-rate`, `/service/otif`, `/money/freight-cost-per-case`, `/money/returns-leakage`, `/cold-chain/excursions`, `/cold-chain/returns`); a value outside 1-4 hit an unhandled `KeyError` inside `fiscal_quarter_bounds()` and returned a bare "Internal Server Error" with no JSON body. Found during the Phase 5 endpoint sweep. Fixed by constraining the query parameter to `ge=1, le=4` on all affected endpoints, so out-of-range input now returns a proper 422 with a field-level error message. Re-verified all 8 illustrative questions and the full endpoint/parameter matrix after both fixes.

## What we'd do next with two more weeks
Resolve the OTIF tolerance question with Kestrel's ops team directly rather than leaving it as a reported ambiguity; implement the LLM ask-anything fallback; reconcile delay_minutes against its true source system; extend price matching with a proper string-similarity library if the 46% ambiguous rate matters to the business.

## What breaks first in production
No auth or rate-limiting on the API. Single DuckDB file with no concurrent-write story — fine for a read-only analytics workload at this volume, not fine if this becomes a write path. At 100x data volume, the per-request DuckDB connection-open pattern and full-table scans in several endpoints (e.g. `fill_rate` over all order lines) would need indexing/pre-aggregation.
