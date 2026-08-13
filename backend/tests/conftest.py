"""
Shared pytest fixtures for the backend test suite.

Test philosophy (see DECISIONS.md): this is a real, runnable pytest suite,
not a mocked one -- it runs against the actual cleaned DuckDB warehouse the
same way the running application does, because the thing worth testing here
is the SQL and the validation contracts, not a fake in-memory stand-in.

That means most of these tests need `warehouse/warehouse.duckdb` to exist
(run the ETL step first -- see README). Tests that don't need real data
(sql_guard unit tests, and the 422 validation tests, which fail before the
endpoint body ever opens a DB connection) run in ANY environment, including
a completely fresh clean checkout that hasn't run the ETL yet -- consistent
with the project's "must work from clean checkout" constraint. Tests that
do need real data skip themselves cleanly (not an error, not a false pass)
when the warehouse isn't there yet, via the `warehouse_available` fixture.

WAREHOUSE_DB_PATH must be set in the environment BEFORE `backend.app.config`
/ `etl.config` are first imported anywhere in the process, since those
modules read it once at import time. This file does that at module level
(not inside a fixture function) specifically so it runs before pytest
imports any sibling test_*.py module in this directory.
"""
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

WAREHOUSE_PATH = REPO_ROOT / "warehouse" / "warehouse.duckdb"
if WAREHOUSE_PATH.exists():
    os.environ["WAREHOUSE_DB_PATH"] = str(WAREHOUSE_PATH)
os.environ.setdefault("ANTHROPIC_API_KEY", "")


@pytest.fixture(scope="session")
def warehouse_available() -> bool:
    return WAREHOUSE_PATH.exists()


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient

    from backend.app.main import app

    with TestClient(app) as c:
        yield c
