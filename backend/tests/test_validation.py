"""
Regression tests for query-parameter validation: invalid values must return
a clean HTTP 422 with a JSON body, never a raw 500. These specifically
cover two bugs found during the project audit (see DECISIONS.md):

  1. fiscal_quarter outside 1-4 used to hit an unhandled KeyError inside
     date_utils.fiscal_quarter_bounds() and return a bare 500.
  2. fiscal_year outside a sane range (0, negative, absurdly large) hit an
     unhandled ValueError from Python's date() constructor -- the same bug
     class as (1), found on the fiscal_year parameter specifically during
     the follow-up audit.

Also covers near_expiry_days bounds and near-expiry's group_by, which used
to silently fall back to "category" on invalid input instead of rejecting
it like every other endpoint's group_by does.

None of these need the warehouse to exist: FastAPI/Pydantic validates query
parameters before the endpoint body runs, so an invalid value never reaches
get_connection(). That's deliberate -- it means this file runs in any
environment, including a fresh clean checkout that hasn't run the ETL yet.
"""
import pytest

FISCAL_YEAR_ENDPOINTS = [
    "/service/fill-rate",
    "/service/otif",
    "/money/freight-cost-per-case",
    "/money/returns-leakage",
    "/cold-chain/excursions",
    "/cold-chain/returns",
]


@pytest.mark.parametrize("path", FISCAL_YEAR_ENDPOINTS)
@pytest.mark.parametrize("bad_fq", [0, 5, -1, 99])
def test_invalid_fiscal_quarter_returns_422(client, path, bad_fq):
    resp = client.get(path, params={"fiscal_year": 2027, "fiscal_quarter": bad_fq})
    assert resp.status_code == 422, f"{path} fiscal_quarter={bad_fq} -> {resp.status_code} {resp.text[:200]}"
    assert resp.headers["content-type"].startswith("application/json")


@pytest.mark.parametrize("path", FISCAL_YEAR_ENDPOINTS)
@pytest.mark.parametrize("bad_fy", [0, -5, 99999, 1])
def test_invalid_fiscal_year_returns_422(client, path, bad_fy):
    resp = client.get(path, params={"fiscal_year": bad_fy, "fiscal_quarter": 1})
    assert resp.status_code == 422, f"{path} fiscal_year={bad_fy} -> {resp.status_code} {resp.text[:200]}"
    assert resp.headers["content-type"].startswith("application/json")


@pytest.mark.parametrize("path", FISCAL_YEAR_ENDPOINTS)
def test_valid_fiscal_year_and_quarter_not_rejected_by_validation(client, path, warehouse_available):
    if not warehouse_available:
        pytest.skip("warehouse.duckdb not built -- run the ETL step first (see README)")
    resp = client.get(path, params={"fiscal_year": 2027, "fiscal_quarter": 1})
    assert resp.status_code == 200, f"{path} -> {resp.status_code} {resp.text[:200]}"


@pytest.mark.parametrize("bad_days", [0, -1, 366, 10000])
def test_invalid_near_expiry_days_returns_422(client, bad_days):
    resp = client.get("/cold-chain/near-expiry", params={"near_expiry_days": bad_days})
    assert resp.status_code == 422, f"near_expiry_days={bad_days} -> {resp.status_code} {resp.text[:200]}"


@pytest.mark.parametrize("good_days", [1, 30, 365])
def test_valid_near_expiry_days_not_rejected_by_validation(client, good_days, warehouse_available):
    if not warehouse_available:
        pytest.skip("warehouse.duckdb not built -- run the ETL step first (see README)")
    resp = client.get("/cold-chain/near-expiry", params={"near_expiry_days": good_days})
    assert resp.status_code == 200


def test_invalid_near_expiry_group_by_returns_422_not_silent_fallback(client):
    """Used to silently fall back to 'category' on bad input; must now
    reject it the same way every other endpoint's group_by does."""
    resp = client.get("/cold-chain/near-expiry", params={"group_by": "not_a_real_dimension"})
    assert resp.status_code == 422


@pytest.mark.parametrize("path,valid_values", [
    ("/service/fill-rate", ["outlet", "region", "warehouse", "route"]),
    ("/service/otif", ["region", "warehouse", "route", "outlet"]),
])
def test_group_by_rejects_unknown_dimension(client, path, valid_values):
    resp = client.get(path, params={"group_by": "not_a_real_dimension"})
    assert resp.status_code == 422


def test_month_format_validation_still_returns_clean_400(client):
    """Not part of the fiscal_year/quarter bug, but the same 'clean error,
    not a crash' contract -- regression-guard it too."""
    resp = client.get("/service/fill-rate", params={"month": "not-a-month"})
    assert resp.status_code == 400
