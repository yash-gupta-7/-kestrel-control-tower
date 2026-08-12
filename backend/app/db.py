"""
Read-only access to warehouse.duckdb.

One connection per request, opened read_only=True. DuckDB connections are
not safe to share across threads, and FastAPI runs sync path functions in a
worker threadpool -- a pooled/shared connection would need its own locking
for no real benefit at this data volume (an open+query+close round trip on
an embedded DuckDB file is sub-millisecond). If this ever needs to serve
meaningfully concurrent load, switch to a small connection pool; not worth
the complexity here (see DECISIONS.md, "what breaks first in production").
"""
import functools
from contextlib import contextmanager
from datetime import date

import duckdb
from fastapi import HTTPException

from . import config

# The only tables the running application (dashboard endpoints *and* any
# future LLM-generated SQL) is ever allowed to touch. This is enforced twice:
# physically, because build_warehouse.py drops the `raw` schema after use, so
# the operational tables do not exist in this file at all; and again here as
# an explicit allowlist so a bug elsewhere can't silently start querying
# something new without a conscious decision to add it to this list.
ALLOWED_TABLES = {
    "dim_region", "dim_warehouse", "dim_route", "dim_salesperson",
    "dim_outlet", "dim_product", "dim_date",
    "fact_order_lines", "fact_deliveries", "fact_returns", "fact_freight",
    "fact_price_position", "fact_weather",
}


@contextmanager
def get_connection():
    if not config.WAREHOUSE_DB_PATH.exists():
        raise HTTPException(
            status_code=503,
            detail=(
                f"Warehouse not found at {config.WAREHOUSE_DB_PATH}. "
                "Run the ETL step first: see README 'Setup' section "
                "(python3 etl/scrape_bazaarpulse.py && "
                "python3 etl/pull_freight_invoices.py && "
                "python3 etl/pull_weather.py && python3 etl/build_warehouse.py)."
            ),
        )
    con = duckdb.connect(str(config.WAREHOUSE_DB_PATH), read_only=True)
    try:
        yield con
    finally:
        con.close()


def run_query(con, sql: str, params: list | None = None) -> list[dict]:
    """Executes a SELECT and returns rows as a list of dicts, capped at
    MAX_ROWS_RETURNED. Every query the API executes -- fast-path or future
    LLM-generated -- goes through this one function."""
    try:
        cur = con.execute(sql, params or [])
        cols = [d[0] for d in cur.description]
        rows = cur.fetchmany(config.MAX_ROWS_RETURNED)
    except duckdb.Error as e:
        raise HTTPException(status_code=500, detail=f"Query failed: {e}")
    return [dict(zip(cols, r)) for r in rows]


@functools.lru_cache(maxsize=1)
def data_max_order_date() -> date:
    """The dataset's own notion of "today" -- the latest order_date present.
    Used for "last month" / "last complete quarter" style questions, since
    this is a fixed historical snapshot (through 30 June 2026), not a live
    feed. Cached for the process lifetime; the warehouse is rebuilt, not
    mutated in place, so this cannot go stale within a single run."""
    with get_connection() as con:
        row = con.execute("SELECT MAX(order_date) FROM fact_order_lines").fetchone()
    return row[0]
