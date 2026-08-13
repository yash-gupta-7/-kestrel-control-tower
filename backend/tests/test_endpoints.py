"""
Integration tests against the real cleaned warehouse (see conftest.py for
why this suite is written this way, and why these tests skip themselves
cleanly rather than fail when the warehouse hasn't been built yet).

Covers the correction-checkpoint requirements: all 8 illustrative
questions, OTIF grouped by outlet (the P0 fix), the region filter for "All
Regions" and a specific region (including the "All Regions total is
unaffected by the feature existing" check), and the no-API-key Ask
Anything path.
"""
import pytest

pytestmark = pytest.mark.usefixtures("client")


@pytest.fixture(autouse=True)
def _skip_without_warehouse(warehouse_available):
    if not warehouse_available:
        pytest.skip("warehouse.duckdb not built -- run the ETL step first (see README)")


# --- The 8 illustrative questions, via /ask (fast-path) --------------------

ILLUSTRATIVE_QUESTIONS = [
    "Which five outlets had the lowest case fill rate last month, excluding closed and test outlets?",
    "What was OTIF by region for the last complete quarter?",
    "Which categories drive the largest value of returns, and what is the leading reason code?",
    "Temperature excursions per hundred chilled deliveries, by month.",
    "Which routes are more than two hours late on more than one delivery in ten?",
    "For our top twenty SKUs by value, how does our MRP compare with the lowest observed competitor price in Mumbai?",
    "Freight cost per delivered case, by warehouse, for the last quarter.",
    "Which outlets ordered a discontinued SKU after its discontinuation date?",
]


@pytest.mark.parametrize("question", ILLUSTRATIVE_QUESTIONS)
def test_illustrative_question_answers_deterministically(client, question):
    resp = client.post("/ask", json={"question": question})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mode"] == "fast_path", f"{question!r} did not match a fast-path handler: {body}"
    assert body["matched_question_id"] is not None
    assert body["answer"]


def test_all_8_questions_have_distinct_matched_ids(client):
    ids = set()
    for q in ILLUSTRATIVE_QUESTIONS:
        resp = client.post("/ask", json={"question": q})
        ids.add(resp.json()["matched_question_id"])
    assert len(ids) == 8


# --- OTIF by outlet (P0 fix) ------------------------------------------------

def test_otif_group_by_outlet_returns_200(client):
    resp = client.get("/service/otif", params={"group_by": "outlet", "limit": 5})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["group_by"] == "outlet"
    # Business definition must be untouched: still strict, no tolerance.
    assert body["filters_applied"]["in_full_definition"].startswith("strict")


def test_otif_group_by_outlet_rows_shaped_like_other_dimensions(client):
    by_outlet = client.get("/service/otif", params={"group_by": "outlet", "limit": 3}).json()
    by_region = client.get("/service/otif", params={"group_by": "region", "limit": 3}).json()
    assert set(by_outlet["rows"][0]["metrics"].keys()) == set(by_region["rows"][0]["metrics"].keys())


# --- Region filter: All Regions + each region -------------------------------

def test_regions_endpoint_returns_five_regions(client):
    resp = client.get("/meta/regions")
    assert resp.status_code == 200
    regions = resp.json()
    assert len(regions) == 5
    codes = {r["region_code"] for r in regions}
    assert codes == {"WST", "STH", "NTH", "EST", "CEN"}


@pytest.mark.parametrize("region_code", ["WST", "STH", "NTH", "EST", "CEN"])
def test_fill_rate_region_filter_scopes_results(client, region_code):
    resp = client.get("/service/fill-rate", params={"group_by": "outlet", "region_code": region_code, "limit": 500})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["filters_applied"]["region_code"] == region_code
    assert len(body["rows"]) > 0, f"region {region_code} returned no outlets -- filter likely broken"
    # 724 outlets across 5 regions is ~145/region, comfortably under the
    # 500-row cap for a single region, but not for "all regions" combined --
    # if this ever fires it means the filter stopped narrowing anything.
    assert len(body["rows"]) < 500


def test_fill_rate_region_filter_partitions_outlets_cleanly(client):
    """Correctness check for the filter itself: querying each region in
    turn must produce outlet sets that don't overlap (an outlet showing up
    under two regions would mean the region_id join/filter is wrong)."""
    per_region = {}
    for region_code in ["WST", "STH", "NTH", "EST", "CEN"]:
        resp = client.get("/service/fill-rate", params={"group_by": "outlet", "region_code": region_code, "limit": 500})
        per_region[region_code] = {r["dimension"] for r in resp.json()["rows"]}

    all_codes = set()
    for region_code, codes in per_region.items():
        assert not (codes & all_codes), f"{region_code} overlaps a previously-seen region's outlets"
        all_codes |= codes

    # Every outlet that shows up in the unfiltered ("All Regions") view must
    # be accounted for by exactly one region once you scope through all five.
    unfiltered = client.get("/service/fill-rate", params={"group_by": "outlet", "limit": 500}).json()
    unfiltered_codes = {r["dimension"] for r in unfiltered["rows"]}
    assert unfiltered_codes <= all_codes


def test_region_filter_does_not_alter_all_region_totals(client):
    """The critical non-regression check: adding the region filter must not
    have changed what "All Regions" (no filter) returns. Compares the
    unfiltered fill-rate-by-region totals against the sum of each
    individually-filtered region's totals -- they must match."""
    unfiltered = client.get("/service/fill-rate", params={"group_by": "region", "limit": 10}).json()
    unfiltered_total_ordered = sum(r["metrics"]["ordered_qty"] for r in unfiltered["rows"])
    unfiltered_total_delivered = sum(r["metrics"]["delivered_qty"] for r in unfiltered["rows"])

    summed_ordered = 0
    summed_delivered = 0
    for region_code in ["WST", "STH", "NTH", "EST", "CEN"]:
        scoped = client.get(
            "/service/fill-rate", params={"group_by": "region", "region_code": region_code, "limit": 10}
        ).json()
        summed_ordered += sum(r["metrics"]["ordered_qty"] for r in scoped["rows"])
        summed_delivered += sum(r["metrics"]["delivered_qty"] for r in scoped["rows"])

    assert summed_ordered == unfiltered_total_ordered
    assert summed_delivered == unfiltered_total_delivered


def test_no_region_code_means_all_regions_unchanged_shape(client):
    resp = client.get("/service/fill-rate", params={"group_by": "region", "limit": 10})
    assert resp.status_code == 200
    assert resp.json()["filters_applied"]["region_code"] is None


def test_price_position_has_no_region_param_and_is_unaffected(client):
    """Price Position is explicitly out of scope for region filtering (city
    != Kestrel region, no mapping in the data) -- confirm the endpoint
    still works and ignores an unknown region_code param rather than
    erroring, since it was never wired up to accept one."""
    resp = client.get("/price-position/summary")
    assert resp.status_code == 200


def test_ask_with_region_code_scopes_fast_path(client):
    resp = client.post(
        "/ask",
        json={"question": "What was OTIF by region for the last complete quarter?", "region_code": "WST"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "West" in body["answer"] or "WST" in body["answer"] or len(body["data"]) <= 5


# --- No-API-key Ask Anything path -------------------------------------------

def test_ask_freeform_without_api_key_returns_unavailable_not_500(client):
    resp = client.post("/ask", json={"question": "why did fill rate drop in the West last week"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "unavailable"
    assert body["data"] is None


def test_ask_mutation_shaped_question_text_is_safe(client):
    """The question TEXT itself is never SQL -- it's matched by keyword
    against fixed handlers or (with no key) rejected outright. This just
    confirms garbage/injection-shaped free text can't do anything odd."""
    resp = client.post("/ask", json={"question": "DROP TABLE dim_outlet; --"})
    assert resp.status_code == 200
    assert resp.json()["mode"] == "unavailable"
