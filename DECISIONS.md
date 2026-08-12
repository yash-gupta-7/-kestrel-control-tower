# Decisions

*Running decision log for the Kestrel Control Tower build -- updated as
work progresses. Read alongside the README.*

## What's built so far

Data reconnaissance, external ingestion (BazaarPulse scrape, freight API
pull, weather pull), a cleaned DuckDB warehouse, and a FastAPI backend
serving fill rate, OTIF, freight, returns leakage, cold chain, and price
position analytics over it.

## Priority allocation

Fill Rate/OTIF 30%, Ask-anything 30%, Freight+Returns 25%, Cold Chain 10%,
Price Position 5% -- set explicitly, depth follows this order.

## Judgement calls

This section captures the KPI-definition and data-quality corrections made
during the backend build -- decisions investigated and locked in rather
than assumed.

**Fill rate: eaches, not cases -- locked, no toggle in the API.** The
brief's own illustrative Q1 says "case fill rate," but Rakesh Menon's
follow-up email explicitly overrides it: modern trade penalises Kestrel on
units short, not cases short, and mixed-configuration SKUs make cases
ambiguous. We apply eaches uniformly, including when literally answering
Q1, and say so in the answer rather than silently switching units.

**OTIF: no in-full tolerance threshold.** Investigated rather than
assumed: 0 of 511,516 order lines -- across every order_status including
DELIVERED -- ever have delivered_eaches >= ordered_eaches; 99th percentile
of order-level fulfilment is 94.8%. Every line carries a real shortfall by
data design. A blended OTIF at the strict 100% bar is therefore ~0% almost
everywhere. We report it that way -- not tuned to an arbitrary tolerance
to make it "look normal" -- and expose on-time % and avg fulfilment %
(continuous, not boolean) alongside it as the usable signal. Kestrel's own
shrinkage-tolerance policy would be needed to define a real in-full
threshold; that's a business decision we don't have.

**delay_minutes used as-is, not recomputed from timestamps.** Checked:
delay_minutes disagrees with (actual_arrival - planned_arrival) computed
from the parsed timestamp columns in 87% of deliveries, including sign.
Used the documented field (data dictionary defines its sign convention
directly) rather than substitute our own derivation that the data itself
doesn't corroborate.

**Fiscal year: year-ending label -- corrected.** FY runs April-March;
labelled by the year it ends in (Indian convention), so Apr-Jun 2026 is
**FY2027 Q1**, not FY2026 Q1. The first pass at `fiscal_year()` used the
starting calendar year instead, which silently mislabelled every quarter
except Q4. Caught when `fiscal_quarter_bounds()` (written against the
year-ending convention) produced date ranges that didn't line up with the
labels `fiscal_year()` was generating. Fixed and verified across all
quarters, not just one.

**Near-expiry: implemented, not cut.** `inventory_snapshots` was outside
the initial warehouse build; a quick feasibility pass showed it was one
clean fact table (weekly, expiry_date always populated, no format
surprises) -- added `fact_inventory_snapshot` and `GET
/cold-chain/near-expiry`. Near-expiry is computed from `expiry_date -
snapshot_date` directly, **not** from the source `ageing_bucket` column:
checked, and that column is uncorrelated with actual days-to-expiry (all
four buckets average ~100 days regardless of label) -- it's carried
through for reference but not trusted.

**Price position: conservative matching, not loosened.** BazaarPulse
listings are matched to Kestrel SKUs by (brand, category, normalised pack
size), with a product-name-overlap tiebreak. 614 of 1,137 listings match
with confidence; the remaining 523 stay `ambiguous` and are excluded from
every price-gap view rather than guessed. This rate was not inflated on
review.

**Q5 (late routes) and Q8 (discontinued-SKU orders): reported as
systemic, not per-entity blame.** All 140 routes exceed ">2hr late on
>10% of deliveries" (avg delay ~132 min network-wide); all 724 outlets
have at least one post-discontinuation order line across 24 discontinued
SKUs, continuing to the end of the dataset. Both are framed as
process/network findings, with worst-offender rankings included for
follow-up, not as "these specific entities are broken."

**Known-issue log spot-check.** KP-2301 (order header vs. line value
mismatch) did not reproduce -- gross ties to `sum(line_value_inr)`
exactly; net differs only by discount/tax as expected. Not chased
further.

**Data cleanup applied once, in the warehouse build:** city spelling
(KP-2288), outlet dedup by GST/geo not name (KP-2211, ~6 outlets, not
hundreds), test-outlet exclusion by name pattern (KP-2377, exactly 3),
return-quantity sign correction (KP-2402, abs()), per-source timestamp
parsing (3 order-creation formats, 2 telematics formats).

## Not yet built

The ask-anything interface, the frontend, and Docker packaging.
