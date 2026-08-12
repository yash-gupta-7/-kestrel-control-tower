# Decisions

*Running decision log for the Kestrel Control Tower build -- updated as
work progresses. Read alongside the README.*

## Priority allocation

Fill Rate/OTIF 30%, Ask-anything 30%, Freight+Returns 25%, Cold Chain 10%,
Price Position 5% -- set explicitly up front; depth of each phase follows
this order.

## What's built so far

Nothing yet -- data reconnaissance only, no code beyond that.

## Data reconnaissance

Initial pass over the assignment pack: the operational SQLite DB (13 raw
tables), the two mock external services (BazaarPulse listings site,
partner freight API), and public Open-Meteo weather. Goal was to find the
messy-data issues before writing any cleaning logic, not after.

Findings so far, all cross-referenced against the known-issue log:

- Outlet `city` is free text with inconsistent spelling (KP-2288) --
  e.g. "Bangalore" vs "Bengaluru", "New Delhi" vs "Delhi".
- Outlet master has duplicate records not flagged by name (KP-2211) --
  same physical outlet appears more than once under different outlet_ids.
- Test/migration debris outlets exist with no status flag, only name
  patterns (KP-2377) -- "%TEST%", "DO NOT USE%", "ZZ%".
- Return quantities carry an inconsistent sign in one upstream feed
  (KP-2402).
- Order-creation and delivery telematics timestamps arrive in multiple
  source-specific formats (3 order-date formats, 2 telematics formats) --
  will need per-source parsing, not one global format string.
- KP-2301 (order header vs. line value mismatch) is on the known-issue
  log -- flagged for a spot-check once fact tables exist, not yet
  investigated.

None of this is cleaned yet -- this is the profiling pass. Cleaning
happens in the warehouse build.

## Judgement calls

(none yet -- will be documented here as data-quality and scope questions
come up during the build)

## Not yet built

Everything past reconnaissance.
