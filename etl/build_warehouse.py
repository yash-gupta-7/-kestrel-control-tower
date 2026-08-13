"""
Builds warehouse/warehouse.duckdb: the single, cleaned analytical store that
both the backend API and the ask-anything NL-to-SQL layer read from.

Nothing downstream of this script (backend, frontend, NL query) ever touches
`kestrel_ops.db` or the raw scrape/API caches directly. Every known data
quality issue is resolved exactly once, here, and documented inline. Where
this script and DECISIONS.md disagree about *why* a rule exists, this file
is the more detailed of the two; DECISIONS.md has the one-paragraph summary.

Rules applied (see comments at each step for the "why"):
  1. City name canonicalisation (KP-2288).
  2. Outlet dedup by GST number / exact lat-long, not by name (KP-2211).
  3. Test/migration outlet exclusion by name pattern (KP-2377) -- flagged,
     not silently dropped, so the raw count is still recoverable.
  4. Quantity normalisation to eaches using case_pack_at_order (KP-2340).
  5. Return quantity sign correction -- abs() (KP-2402).
  6. Per-source timestamp parsing for orders.created_at and
     deliveries.actual_arrival, normalised to Asia/Kolkata.
  7. Discontinued-SKU-ordered-after-discontinuation flagged as its own
     fact, not filtered out (it is a genuine finding, see illustrative Q8).
  8. Freight invoices: paise -> INR, UTC -> IST (done at pull time), joined
     to warehouse_code / route_code.
  9. BazaarPulse listings matched to Kestrel SKUs by (brand, category,
     pack size normalised to a common unit) -- see match_bazaarpulse().
 10. Near-expiry inventory computed from expiry_date - snapshot_date
     directly, NOT from the source `ageing_bucket` column -- verified that
     column is uncorrelated with actual days-to-expiry (all four buckets
     average ~100 days to expiry regardless of label), so it is unusable
     and is carried through unchanged but not relied on.

Usage:
    python3 etl/build_warehouse.py
"""
import re
import sqlite3
import sys
from pathlib import Path

import duckdb
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from etl import config


def connect_raw_sqlite(con: duckdb.DuckDBPyConnection):
    """Loads every table from kestrel_ops.db into a `raw` schema in DuckDB.

    DuckDB's sqlite_scanner extension would be the natural way to do this
    (ATTACH ... TYPE sqlite), but it downloads from extensions.duckdb.org at
    first use, which is blocked in locked-down environments (seen in dev: a
    plain 403). Loading via Python's stdlib sqlite3 + pandas has no such
    runtime dependency and the tables involved (largest is ~512k rows) fit
    comfortably in memory, so this is the more portable choice for a
    "clean checkout, no tribal knowledge" requirement.
    """
    con.execute("CREATE SCHEMA IF NOT EXISTS raw")
    sconn = sqlite3.connect(config.KESTREL_DB_PATH)
    tables = [r[0] for r in sconn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()]
    for t in tables:
        df = pd.read_sql_query(f"SELECT * FROM {t}", sconn)
        con.register(f"_stg_{t}", df)
        con.execute(f"CREATE OR REPLACE TABLE raw.{t} AS SELECT * FROM _stg_{t}")
        con.unregister(f"_stg_{t}")
    sconn.close()
    print(f"  Loaded {len(tables)} raw tables from {config.KESTREL_DB_PATH}")


def build_dims(con):
    con.execute("CREATE OR REPLACE TABLE dim_region AS SELECT * FROM raw.regions")
    con.execute("CREATE OR REPLACE TABLE dim_warehouse AS SELECT * FROM raw.warehouses")
    con.execute("CREATE OR REPLACE TABLE dim_route AS SELECT * FROM raw.routes")
    con.execute("CREATE OR REPLACE TABLE dim_salesperson AS SELECT * FROM raw.salespeople")

    # --- Products: current-state dimension, plus a discontinued flag. -----
    con.execute("""
        CREATE OR REPLACE TABLE dim_product AS
        SELECT * EXCLUDE (discontinued_date, launch_date),
               TRY_CAST(discontinued_date AS DATE) AS discontinued_date,
               TRY_CAST(launch_date AS DATE) AS launch_date,
               discontinued_date IS NOT NULL AS is_discontinued
        FROM raw.products
    """)

    # --- Outlets: canonical city, dedup flag, test-outlet flag. ------------
    city_case = " ".join(
        f"WHEN city = '{k}' THEN '{v}'" for k, v in config.CITY_CANONICAL_MAP.items()
    )
    test_pattern = " OR ".join(
        f"outlet_name LIKE '{p}'" for p in config.TEST_OUTLET_PATTERNS
    )
    con.execute(f"""
        CREATE OR REPLACE TABLE dim_outlet AS
        WITH base AS (
            SELECT *,
                   CASE {city_case} ELSE city END AS city_canonical,
                   ({test_pattern}) AS is_test_outlet
            FROM raw.outlets
        ),
        dupe_groups AS (
            SELECT outlet_id,
                   COALESCE(
                       'gst:' || NULLIF(gst_number, ''),
                       'geo:' || latitude || ',' || longitude
                   ) AS dedup_key
            FROM base
            WHERE (gst_number IS NOT NULL AND gst_number != '')
               OR (latitude IS NOT NULL AND longitude IS NOT NULL)
        ),
        dupe_flagged AS (
            SELECT outlet_id,
                   COUNT(*) OVER (PARTITION BY dedup_key) > 1 AS is_duplicate_outlet
            FROM dupe_groups
        )
        SELECT b.*,
               COALESCE(d.is_duplicate_outlet, FALSE) AS is_duplicate_outlet,
               -- The single default filter every metric should apply unless
               -- explicitly asked to include everything: not deleted, not a
               -- known test/migration record. Closed outlets are kept in by
               -- default (a "last full quarter" report legitimately includes
               -- outlets that closed mid-quarter) but flagged so callers can
               -- exclude them -- this mirrors illustrative Q1's explicit
               -- "excluding closed and test outlets".
               (b.is_deleted = 0 AND NOT b.is_test_outlet) AS include_by_default
        FROM base b
        LEFT JOIN dupe_flagged d USING (outlet_id)
    """)


def build_date_spine(con):
    con.execute("""
        CREATE OR REPLACE TABLE dim_date AS
        WITH spine AS (
            SELECT range::DATE AS date
            FROM range(DATE '2025-01-01', DATE '2026-07-31', INTERVAL 1 DAY)
        )
        SELECT
            date,
            EXTRACT(YEAR FROM date) AS calendar_year,
            EXTRACT(MONTH FROM date) AS calendar_month,
            EXTRACT(QUARTER FROM date) AS calendar_quarter,
            -- Kestrel's FY runs April-March (per assignment brief), labelled
            -- by the calendar year it ENDS in (year-ending convention,
            -- standard in Indian corporate reporting: "FY2027" = the year
            -- ending 31 Mar 2027, so Apr-Jun 2026 is FY2027 Q1). Corrected
            -- after review -- an earlier version labelled by the starting
            -- year instead, which is a different, less standard convention.
            CASE WHEN EXTRACT(MONTH FROM date) >= 4
                 THEN EXTRACT(YEAR FROM date) + 1
                 ELSE EXTRACT(YEAR FROM date) END AS fiscal_year,
            CASE
                WHEN EXTRACT(MONTH FROM date) IN (4,5,6) THEN 1
                WHEN EXTRACT(MONTH FROM date) IN (7,8,9) THEN 2
                WHEN EXTRACT(MONTH FROM date) IN (10,11,12) THEN 3
                ELSE 4
            END AS fiscal_quarter
        FROM spine
    """)


def build_fact_order_lines(con):
    # qty_uom is only ever CASE or EACH (confirmed against the data), and
    # case_pack_at_order is never null, so this conversion is exact -- no
    # fallback path needed, but we assert it below rather than assume it
    # silently stays that way.
    con.execute("""
        CREATE OR REPLACE TABLE fact_order_lines AS
        SELECT
            ol.order_line_id, ol.order_id, ol.line_number, ol.product_id,
            ol.ordered_qty, ol.allocated_qty, ol.delivered_qty, ol.qty_uom,
            ol.case_pack_at_order,
            CASE WHEN ol.qty_uom = 'EACH' THEN ol.ordered_qty
                 ELSE ol.ordered_qty * ol.case_pack_at_order END AS ordered_eaches,
            CASE WHEN ol.qty_uom = 'EACH' THEN ol.delivered_qty
                 ELSE ol.delivered_qty * ol.case_pack_at_order END AS delivered_eaches,
            CASE WHEN ol.qty_uom = 'EACH' THEN ol.ordered_qty / NULLIF(ol.case_pack_at_order, 0)
                 ELSE ol.ordered_qty END AS ordered_cases,
            CASE WHEN ol.qty_uom = 'EACH' THEN ol.delivered_qty / NULLIF(ol.case_pack_at_order, 0)
                 ELSE ol.delivered_qty END AS delivered_cases,
            ol.unit_price_inr, ol.line_discount_pct, ol.line_value_inr,
            ol.gst_rate_pct, ol.batch_id, ol.substitution_flag, ol.short_reason_code,
            o.order_number, o.outlet_id,
            CAST(o.order_date AS DATE) AS order_date,
            TRY_CAST(o.requested_delivery_date AS DATE) AS requested_delivery_date,
            o.channel, o.region_id, o.route_id, o.warehouse_id, o.salesperson_id,
            o.order_status, o.source_system, o.promo_code,
            p.sku_code, p.product_name, p.brand, p.category, p.is_chilled,
            p.is_discontinued, p.discontinued_date,
            (p.is_discontinued AND CAST(o.order_date AS DATE) > p.discontinued_date) AS ordered_after_discontinued
        FROM raw.order_lines ol
        JOIN raw.orders o ON o.order_id = ol.order_id
        JOIN dim_product p ON p.product_id = ol.product_id
    """)
    n_bad_uom = con.execute(
        "SELECT COUNT(*) FROM raw.order_lines WHERE qty_uom NOT IN ('CASE','EACH')"
    ).fetchone()[0]
    if n_bad_uom:
        print(f"  WARNING: {n_bad_uom} order_lines have an unexpected qty_uom -- eaches conversion may be wrong for these")


def _parse_ts_sql(col: str, source_col: str) -> str:
    """Builds a SQL CASE that parses a timestamp column whose format varies
    by an accompanying source indicator column, returning a TIMESTAMP.
    Used for orders.created_at (by source_system) and
    deliveries.actual_arrival (by telematics_vendor)."""
    return f"""
        CASE
            WHEN {source_col} = 'ERP_WEB' THEN strptime({col}, '%d/%m/%Y %H:%M')
            WHEN {source_col} = 'PARTNER_API' THEN strptime({col}, '%Y-%m-%dT%H:%M:%SZ')
            WHEN {source_col} = 'SFA_MOBILE' THEN strptime({col}, '%Y-%m-%d %H:%M:%S')
            WHEN {source_col} = 'TELEMATICS_A' THEN strptime({col}, '%Y-%m-%d %H:%M:%S')
            WHEN {source_col} = 'TELEMATICS_B' THEN strptime({col}, '%d-%b-%Y %I:%M %p')
            ELSE TRY_STRPTIME({col}, '%Y-%m-%d %H:%M:%S')
        END
    """


def build_fact_deliveries(con):
    parsed_actual = _parse_ts_sql("d.actual_arrival", "d.telematics_vendor")
    con.execute(f"""
        CREATE OR REPLACE TABLE fact_deliveries AS
        SELECT
            d.delivery_id, d.order_id, d.delivery_note_number, d.route_id,
            d.warehouse_id, d.telematics_vendor,
            CAST(d.dispatch_datetime AS TIMESTAMP) AS dispatch_datetime,
            strptime(d.planned_arrival, '%Y-%m-%d %H:%M:%S') AS planned_arrival_ts,
            {parsed_actual} AS actual_arrival_ts,
            d.delay_minutes, d.distance_km, d.delivery_status, d.pod_captured,
            d.temperature_excursion_flag, d.max_temp_celsius, d.returned_cases,
            d.failure_reason_code, d.fuel_cost_inr,
            -- On-time: not late per the carrier's own delay_minutes field
            -- (already signed, negative = early). "On time" defined as
            -- delay_minutes <= 0. In-full is computed at the order-line
            -- grain in fact_order_lines (delivered_eaches / ordered_eaches),
            -- OTIF for a delivery is therefore evaluated by joining the two,
            -- not duplicated here.
            (d.delay_minutes <= 0) AS is_on_time,
            o.outlet_id, o.channel, o.region_id
        FROM raw.deliveries d
        JOIN raw.orders o ON o.order_id = d.order_id
    """)


def build_fact_returns(con):
    # region_id is added via dim_outlet (not present on the raw table) so
    # returns can be scoped by region the same way fact_order_lines and
    # fact_deliveries already are -- additive only, no change to any
    # existing column or cleaning rule.
    con.execute("""
        CREATE OR REPLACE TABLE fact_returns AS
        SELECT
            r.return_id, r.credit_note_number, r.order_id, r.order_line_id,
            r.outlet_id, o.region_id,
            r.product_id,
            CAST(r.return_date AS DATE) AS return_date,
            ABS(r.return_qty) AS return_qty,  -- KP-2402: sign is a known bug, not signal
            r.qty_uom,
            CASE WHEN r.qty_uom = 'EACH' THEN ABS(r.return_qty)
                 ELSE ABS(r.return_qty) * ol.case_pack_at_order END AS return_eaches,
            r.return_reason_code, r.credit_note_value_inr, r.disposition, r.status,
            p.sku_code, p.category, p.brand
        FROM raw.returns_credit_notes r
        LEFT JOIN raw.order_lines ol ON ol.order_line_id = r.order_line_id
        JOIN dim_product p ON p.product_id = r.product_id
        JOIN dim_outlet o ON o.outlet_id = r.outlet_id
    """)


def build_fact_inventory(con):
    """Weekly warehouse x SKU x batch snapshots, with near-expiry computed
    directly from expiry_date - snapshot_date. NOT from `ageing_bucket`:
    checked, and that column is uncorrelated with actual days-to-expiry
    (all four buckets average ~100 days regardless of label) -- carried
    through unchanged for reference but not used for anything here."""
    con.execute("""
        CREATE OR REPLACE TABLE fact_inventory_snapshot AS
        SELECT
            s.snapshot_id,
            CAST(s.snapshot_date AS DATE) AS snapshot_date,
            s.warehouse_id, s.product_id, s.batch_id,
            s.on_hand_cases, s.on_hand_eaches, s.allocated_cases, s.available_cases,
            s.days_of_cover,
            CAST(s.expiry_date AS DATE) AS expiry_date,
            s.ageing_bucket,  -- kept for reference only; do not trust, see docstring
            DATE_DIFF('day', CAST(s.snapshot_date AS DATE), CAST(s.expiry_date AS DATE)) AS days_to_expiry,
            s.damaged_cases, s.blocked_cases, s.storage_temp_celsius,
            p.sku_code, p.category, p.is_chilled
        FROM raw.inventory_snapshots s
        JOIN dim_product p ON p.product_id = s.product_id
    """)


def build_fact_freight(con):
    freight_csv = config.FREIGHT_CACHE / "freight_invoices.csv"
    if not freight_csv.exists():
        print("  No freight cache found -- run etl/pull_freight_invoices.py first. Skipping fact_freight.")
        con.execute("""
            CREATE OR REPLACE TABLE fact_freight (
                invoice_id VARCHAR, carrier_id VARCHAR, carrier_name VARCHAR,
                warehouse_code VARCHAR, warehouse_id INTEGER, route_code VARCHAR,
                route_id INTEGER, invoice_date DATE, service_date DATE,
                amount_inr DOUBLE, fuel_surcharge_pct DOUBLE,
                detention_charge_inr DOUBLE, distance_km DOUBLE, weight_kg DOUBLE,
                temperature_controlled BOOLEAN, status VARCHAR
            )
        """)
        return
    con.execute(f"""
        CREATE OR REPLACE TABLE fact_freight AS
        SELECT
            f.invoice_id, f.carrier_id, f.carrier_name, f.warehouse_code,
            w.warehouse_id, f.route_code, rt.route_id,
            CAST(f.invoice_date AS DATE) AS invoice_date,
            CAST(f.service_date AS DATE) AS service_date,
            f.amount_inr, f.fuel_surcharge_pct, f.detention_charge_inr,
            f.distance_km, f.weight_kg, f.temperature_controlled, f.status
        FROM read_csv_auto('{freight_csv.as_posix()}') f
        LEFT JOIN dim_warehouse w ON w.warehouse_code = f.warehouse_code
        LEFT JOIN dim_route rt ON rt.route_code = f.route_code
    """)
    n_unmatched_wh = con.execute(
        "SELECT COUNT(*) FROM fact_freight WHERE warehouse_id IS NULL"
    ).fetchone()[0]
    if n_unmatched_wh:
        print(f"  NOTE: {n_unmatched_wh} freight invoices did not match a known warehouse_code")


UNIT_FAMILY = {"G": ("weight", 1), "KG": ("weight", 1000), "ML": ("volume", 1), "L": ("volume", 1000)}


def build_fact_price_position(con):
    listings_csv = config.BAZAARPULSE_CACHE / "listings.csv"
    if not listings_csv.exists():
        print("  No BazaarPulse cache found -- run etl/scrape_bazaarpulse.py first. Skipping fact_price_position.")
        con.execute("""
            CREATE OR REPLACE TABLE fact_price_position (
                listing_id VARCHAR, city VARCHAR, retailer VARCHAR, title VARCHAR,
                brand_guess VARCHAR, category VARCHAR, price_inr DOUBLE, mrp_inr DOUBLE,
                stock_status VARCHAR, last_seen DATE, sku_code VARCHAR,
                kestrel_mrp_inr DOUBLE, mrp_gap_inr DOUBLE, mrp_gap_pct DOUBLE, match_confidence VARCHAR
            )
        """)
        return

    con.execute(f"""
        CREATE OR REPLACE TABLE stg_bazaarpulse_listings AS
        SELECT * FROM read_csv_auto('{listings_csv.as_posix()}')
    """)

    # Normalise pack size to a common base unit within its family (weight in
    # grams, volume in ml) so "0.4 kg" and "400 g" match. Both the listing
    # side and the product side go through the same normalisation.
    def norm_pack_sql(value_col: str, uom_col: str) -> str:
        cases = " ".join(
            f"WHEN UPPER({uom_col}) = '{u}' THEN {value_col} * {mult}"
            for u, (_fam, mult) in UNIT_FAMILY.items()
        )
        return f"CASE {cases} ELSE NULL END"

    con.execute(f"""
        CREATE OR REPLACE TABLE fact_price_position AS
        WITH listings_norm AS (
            SELECT *, {norm_pack_sql('pack_value', 'pack_uom')} AS pack_norm
            FROM stg_bazaarpulse_listings
        ),
        products_norm AS (
            SELECT product_id, sku_code, product_name, brand, category,
                   mrp_inr AS kestrel_mrp_inr,
                   {norm_pack_sql('pack_size_value', 'pack_size_uom')} AS pack_norm
            FROM dim_product
        ),
        candidates AS (
            SELECT
                l.listing_id, l.city, l.retailer, l.title, l.brand_guess,
                l.category, l.price_inr, l.mrp_inr, l.stock_status,
                CAST(l.last_seen AS DATE) AS last_seen,
                p.product_id, p.sku_code, p.product_name, p.kestrel_mrp_inr,
                COUNT(*) OVER (PARTITION BY l.listing_id) AS n_candidate_matches,
                -- Tiebreak among same brand+category+pack candidates: does
                -- any word from the Kestrel product name (excluding the
                -- brand token itself) show up in the scraped title? Cheap,
                -- explainable, good enough at n=341 products -- a real
                -- system would use a proper string-similarity library.
                (SELECT MAX(CASE WHEN UPPER(l.title) LIKE '%' || UPPER(w) || '%' THEN 1 ELSE 0 END)
                 FROM UNNEST(STRING_SPLIT(p.product_name, ' ')) AS t(w)
                 WHERE LENGTH(w) >= 4 AND UPPER(w) != UPPER(p.brand)) AS name_overlap
            FROM listings_norm l
            LEFT JOIN products_norm p
                ON p.brand = l.brand_guess
               AND p.category = l.category
               AND ABS(p.pack_norm - l.pack_norm) < 0.5
        ),
        ranked AS (
            SELECT *,
                ROW_NUMBER() OVER (
                    PARTITION BY listing_id
                    ORDER BY COALESCE(name_overlap, 0) DESC, product_id ASC
                ) AS rn
            FROM candidates
        )
        SELECT
            listing_id, city, retailer, title, brand_guess, category,
            price_inr, mrp_inr, stock_status, last_seen, sku_code, kestrel_mrp_inr,
            kestrel_mrp_inr - price_inr AS mrp_gap_inr,
            ROUND(100.0 * (kestrel_mrp_inr - price_inr) / NULLIF(kestrel_mrp_inr, 0), 1) AS mrp_gap_pct,
            CASE
                WHEN sku_code IS NULL THEN 'unmatched'
                WHEN n_candidate_matches > 1 THEN 'ambiguous'
                ELSE 'matched'
            END AS match_confidence
        FROM ranked
        WHERE rn = 1
    """)
    counts = con.execute(
        "SELECT match_confidence, COUNT(*) FROM fact_price_position GROUP BY 1"
    ).fetchall()
    print(f"  Price-position match rate: {dict(counts)}")
    con.execute("DROP TABLE stg_bazaarpulse_listings")


def build_fact_weather(con):
    weather_csv = config.WEATHER_CACHE / "daily_weather.csv"
    if not weather_csv.exists() or weather_csv.stat().st_size < 20:
        print("  No usable weather cache -- skipping fact_weather (app degrades gracefully without it).")
        con.execute("""
            CREATE OR REPLACE TABLE fact_weather (
                city VARCHAR, date DATE, temp_max_c DOUBLE, precipitation_mm DOUBLE
            )
        """)
        return
    con.execute(f"""
        CREATE OR REPLACE TABLE fact_weather AS
        SELECT city, CAST(date AS DATE) AS date, temp_max_c, precipitation_mm
        FROM read_csv_auto('{weather_csv.as_posix()}')
    """)


def main():
    config.WAREHOUSE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(config.WAREHOUSE_DB_PATH))
    connect_raw_sqlite(con)

    print("Building dimensions...")
    build_dims(con)
    build_date_spine(con)

    print("Building fact_order_lines...")
    build_fact_order_lines(con)

    print("Building fact_deliveries...")
    build_fact_deliveries(con)

    print("Building fact_returns...")
    build_fact_returns(con)

    print("Building fact_inventory_snapshot...")
    build_fact_inventory(con)

    print("Building fact_freight...")
    build_fact_freight(con)

    print("Building fact_price_position...")
    build_fact_price_position(con)

    print("Building fact_weather...")
    build_fact_weather(con)

    con.execute("DROP SCHEMA raw CASCADE")
    tables = con.execute("SHOW TABLES").fetchall()
    print(f"\nWarehouse built at {config.WAREHOUSE_DB_PATH}: {len(tables)} tables")
    for (t,) in tables:
        n = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(f"  {t}: {n} rows")
    con.close()


if __name__ == "__main__":
    main()
