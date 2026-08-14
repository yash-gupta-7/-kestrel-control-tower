"""
Post-build data-quality gate for warehouse.duckdb.

Runs immediately after build_warehouse.py, before the backend is allowed to
start (etl/Dockerfile chains this with `&&`; docker-compose.yml's backend
service has `depends_on: etl: condition: service_completed_successfully`, so
a non-zero exit here blocks the backend the same way a failed freight pull
already does -- see docs/HLD.md Section 7).

This is deliberately small: required tables exist, critical row counts are
non-zero, a handful of the highest-value FK/null/duplicate/range checks, and
two of this project's own documented invariants (KP-2377 test-outlet count,
match_confidence categories). It is not a data-quality framework -- no
config file, no plugin system, no general-purpose rule engine. Add a check
by adding one function to CHECKS below; nothing else to wire up.

Usage:
    python3 etl/validate_warehouse.py
Exit code 0 = all hard checks passed (warnings may still have printed).
Exit code 1 = at least one hard check failed; the warehouse must not be
served. See the printed report for exactly which check(s) failed and why.
"""
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from etl import config

# Tables build_warehouse.py always creates with real rows -- a build that
# leaves any of these empty produced a broken warehouse, full stop.
REQUIRED_NONEMPTY_TABLES = [
    "dim_region", "dim_warehouse", "dim_route", "dim_salesperson",
    "dim_outlet", "dim_product", "dim_date",
    "fact_order_lines", "fact_deliveries", "fact_returns",
    "fact_inventory_snapshot", "fact_freight", "fact_price_position",
]
# fact_weather is documented everywhere (README, DECISIONS.md) as optional
# public-API enrichment that degrades to an empty cache non-fatally -- an
# empty fact_weather is a legitimate, expected state, not a broken build.
OPTIONAL_TABLES = ["fact_weather"]

ALL_EXPECTED_TABLES = REQUIRED_NONEMPTY_TABLES + OPTIONAL_TABLES

# Loose sanity bounds for the dataset's known window (18 months to 30 Jun
# 2026, per 01_Assignment_Brief.md Section 3) -- generous on purpose, this
# is a smoke check for "wildly wrong" (e.g. a bad CAST producing 1970-01-01
# or a future-dated row), not a tight assertion on the exact date range.
EXPECTED_MIN_DATE = "2024-06-01"
EXPECTED_MAX_DATE = "2026-12-31"


@dataclass
class CheckResult:
    name: str
    passed: bool
    hard: bool  # hard failure blocks the warehouse; soft is a warning only
    message: str


@dataclass
class Report:
    results: list[CheckResult] = field(default_factory=list)

    def add(self, name: str, passed: bool, message: str, hard: bool = True):
        self.results.append(CheckResult(name, passed, hard, message))

    @property
    def hard_failures(self) -> list[CheckResult]:
        return [r for r in self.results if not r.passed and r.hard]

    @property
    def warnings(self) -> list[CheckResult]:
        return [r for r in self.results if not r.passed and not r.hard]


def _table_exists(con, table: str) -> bool:
    row = con.execute(
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = ?", [table]
    ).fetchone()
    return row[0] > 0


def check_required_tables_exist(con, report: Report):
    missing = [t for t in ALL_EXPECTED_TABLES if not _table_exists(con, t)]
    report.add(
        "required_tables_exist", not missing,
        "All expected tables present." if not missing
        else f"Missing table(s): {missing}",
    )


def check_nonzero_row_counts(con, report: Report):
    for table in REQUIRED_NONEMPTY_TABLES:
        if not _table_exists(con, table):
            continue  # already reported by check_required_tables_exist
        n = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        report.add(
            f"nonzero_rows:{table}", n > 0,
            f"{table}: {n} rows." if n > 0 else f"{table} is empty -- expected real rows.",
        )
    for table in OPTIONAL_TABLES:
        if not _table_exists(con, table):
            continue
        n = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        report.add(
            f"nonzero_rows:{table}", True,  # never a failure -- informational only
            f"{table}: {n} rows (optional enrichment; 0 is a valid degraded state).",
            hard=False,
        ) if n == 0 else report.add(f"nonzero_rows:{table}", True, f"{table}: {n} rows.")


def check_primary_key_uniqueness(con, report: Report):
    pk_tables = [
        ("dim_region", "region_id"), ("dim_warehouse", "warehouse_id"),
        ("dim_route", "route_id"), ("dim_outlet", "outlet_id"),
        ("dim_product", "product_id"),
        ("fact_order_lines", "order_line_id"), ("fact_deliveries", "delivery_id"),
    ]
    for table, pk in pk_tables:
        if not _table_exists(con, table):
            continue
        dupes = con.execute(
            f"SELECT COUNT(*) FROM (SELECT {pk} FROM {table} GROUP BY {pk} HAVING COUNT(*) > 1)"
        ).fetchone()[0]
        report.add(
            f"unique_pk:{table}.{pk}", dupes == 0,
            f"{table}.{pk} is unique." if dupes == 0
            else f"{table}.{pk} has {dupes} duplicate value(s) -- not a valid primary key.",
        )


def check_business_key_nulls(con, report: Report):
    null_checks = [
        ("fact_order_lines", "order_line_id"), ("fact_order_lines", "outlet_id"),
        ("fact_order_lines", "sku_code"), ("fact_deliveries", "delivery_id"),
        ("fact_deliveries", "order_id"), ("dim_outlet", "outlet_id"),
        ("dim_product", "sku_code"),
    ]
    for table, col in null_checks:
        if not _table_exists(con, table):
            continue
        n_null = con.execute(f"SELECT COUNT(*) FROM {table} WHERE {col} IS NULL").fetchone()[0]
        report.add(
            f"no_null_key:{table}.{col}", n_null == 0,
            f"{table}.{col} has no unexpected nulls." if n_null == 0
            else f"{table}.{col} has {n_null} unexpected null(s) on a business/join key.",
        )


def check_fk_integrity(con, report: Report):
    fk_checks = [
        ("fact_order_lines", "outlet_id", "dim_outlet", "outlet_id"),
        ("fact_deliveries", "route_id", "dim_route", "route_id"),
        ("fact_deliveries", "warehouse_id", "dim_warehouse", "warehouse_id"),
        ("fact_returns", "outlet_id", "dim_outlet", "outlet_id"),
    ]
    for child, fk_col, parent, pk_col in fk_checks:
        if not (_table_exists(con, child) and _table_exists(con, parent)):
            continue
        orphans = con.execute(f"""
            SELECT COUNT(*) FROM {child} c
            WHERE c.{fk_col} IS NOT NULL
              AND NOT EXISTS (SELECT 1 FROM {parent} p WHERE p.{pk_col} = c.{fk_col})
        """).fetchone()[0]
        report.add(
            f"fk_integrity:{child}.{fk_col}->{parent}.{pk_col}", orphans == 0,
            f"Every {child}.{fk_col} resolves in {parent}." if orphans == 0
            else f"{orphans} row(s) in {child} have {fk_col} not present in {parent}.{pk_col}.",
        )


def check_date_range_sanity(con, report: Report):
    date_cols = [("fact_order_lines", "order_date"), ("fact_deliveries", "dispatch_datetime")]
    for table, col in date_cols:
        if not _table_exists(con, table):
            continue
        lo, hi = con.execute(f"SELECT MIN(CAST({col} AS DATE)), MAX(CAST({col} AS DATE)) FROM {table}").fetchone()
        if lo is None:
            report.add(f"date_range:{table}.{col}", False, f"{table}.{col} has no non-null values at all.")
            continue
        in_range = str(lo) >= EXPECTED_MIN_DATE and str(hi) <= EXPECTED_MAX_DATE
        report.add(
            f"date_range:{table}.{col}", in_range,
            f"{table}.{col} spans {lo} to {hi} (within expected {EXPECTED_MIN_DATE}..{EXPECTED_MAX_DATE})."
            if in_range else
            f"{table}.{col} spans {lo} to {hi} -- outside the expected {EXPECTED_MIN_DATE}..{EXPECTED_MAX_DATE} "
            "window; likely a parsing bug, not a real data point.",
        )


def check_documented_invariants(con, report: Report):
    # KP-2377 (see DECISIONS.md / build_warehouse.py): the test-outlet name
    # pattern is documented as matching exactly 3 outlets in this dataset.
    # A different count doesn't necessarily mean the warehouse is broken,
    # but it does mean the documented finding no longer matches the data --
    # worth a loud warning, not silence, and not a hard failure (the pattern
    # itself is still correctly applied either way).
    if _table_exists(con, "dim_outlet"):
        n_test = con.execute("SELECT COUNT(*) FROM dim_outlet WHERE is_test_outlet").fetchone()[0]
        report.add(
            "invariant:test_outlet_count", n_test == 3,
            f"is_test_outlet matches exactly 3 outlets, as documented (KP-2377)." if n_test == 3
            else f"is_test_outlet matches {n_test} outlets, not the documented 3 (KP-2377) -- "
                 "DECISIONS.md's stated count may be stale for this data.",
            hard=False,
        )

    if _table_exists(con, "fact_price_position"):
        n_rows = con.execute("SELECT COUNT(*) FROM fact_price_position").fetchone()[0]
        if n_rows > 0:
            bad = con.execute(
                "SELECT COUNT(*) FROM fact_price_position "
                "WHERE match_confidence NOT IN ('matched', 'ambiguous', 'unmatched')"
            ).fetchone()[0]
            report.add(
                "invariant:price_position_match_confidence", bad == 0,
                "match_confidence only contains the 3 documented categories." if bad == 0
                else f"{bad} fact_price_position row(s) have an unexpected match_confidence value.",
            )


CHECKS: list[Callable[[duckdb.DuckDBPyConnection, Report], None]] = [
    check_required_tables_exist,
    check_nonzero_row_counts,
    check_primary_key_uniqueness,
    check_business_key_nulls,
    check_fk_integrity,
    check_date_range_sanity,
    check_documented_invariants,
]


def run_all_checks(con) -> Report:
    report = Report()
    for check in CHECKS:
        check(con, report)
    return report


def write_meta_table(con, report: Report) -> None:
    """Records the outcome directly in the warehouse file, so the backend
    (db.py's get_connection()) can refuse to serve a warehouse that either
    never ran this gate or ran it and failed -- defense in depth beyond the
    Docker Compose service_completed_successfully gate, which only protects
    the documented `docker compose up` path, not manual/local runs."""
    status = "ok" if not report.hard_failures else "failed"
    con.execute("CREATE OR REPLACE TABLE _warehouse_meta (validated_at TIMESTAMP, status VARCHAR, "
                "checks_passed INTEGER, checks_failed INTEGER, detail VARCHAR)")
    failed_names = ", ".join(r.name for r in report.hard_failures) or "none"
    con.execute(
        "INSERT INTO _warehouse_meta VALUES (?, ?, ?, ?, ?)",
        [datetime.now(timezone.utc), status,
         sum(1 for r in report.results if r.passed),
         len(report.hard_failures), f"failed_checks: {failed_names}"],
    )


def main() -> int:
    if not config.WAREHOUSE_DB_PATH.exists():
        print(f"FATAL: warehouse not found at {config.WAREHOUSE_DB_PATH} -- run build_warehouse.py first.")
        return 1

    con = duckdb.connect(str(config.WAREHOUSE_DB_PATH))
    try:
        report = run_all_checks(con)
        write_meta_table(con, report)
    finally:
        con.close()

    print("\n=== Warehouse data-quality gate ===")
    for r in report.results:
        tag = "PASS" if r.passed else ("WARN" if not r.hard else "FAIL")
        print(f"  [{tag}] {r.name}: {r.message}")

    print(f"\n{len(report.results) - len(report.hard_failures) - len(report.warnings)} passed, "
          f"{len(report.warnings)} warning(s), {len(report.hard_failures)} hard failure(s).")

    if report.hard_failures:
        print("\nFAILED -- warehouse.duckdb did not pass validation and must not be served.")
        print("Failed checks:")
        for r in report.hard_failures:
            print(f"  - {r.name}: {r.message}")
        return 1

    print("\nOK -- warehouse.duckdb passed validation.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
