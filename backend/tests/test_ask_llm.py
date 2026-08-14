"""
Tests for the Groq-backed free-form Ask Anything path (backend/app/routers/ask.py).

The actual Groq API call is mocked (via monkeypatching `_generate_sql_with_groq`,
the one function that talks to the network) -- these tests are about the
orchestration around it (SQL guard, execution, error handling), not about
Groq's service itself, and must not require network access or a real API key
to run. See conftest.py: GROQ_API_KEY is force-cleared for the test session,
so `client` fixture tests only see a "configured" key when a test explicitly
monkeypatches it.
"""
import json

import pytest

from backend.app.routers import ask as ask_module

pytestmark = pytest.mark.usefixtures("client")


@pytest.fixture(autouse=True)
def _skip_without_warehouse(warehouse_available):
    if not warehouse_available:
        pytest.skip("warehouse.duckdb not built -- run the ETL step first (see README)")


@pytest.fixture
def groq_configured(monkeypatch):
    """Makes the app believe a Groq key is set, without ever calling out to
    the network -- individual tests still control what _generate_sql_with_groq
    returns/raises."""
    monkeypatch.setattr(ask_module.config, "GROQ_API_KEY", "fake-key-for-tests")


def test_llm_path_no_key_message_names_no_env_vars(client):
    """The business-facing 'unavailable' message must not name GROQ_API_KEY,
    ANTHROPIC_API_KEY, or any other env var -- that's an implementation
    detail, not something an ops user should see (see /health's
    llm_configured field for the actual configuration signal)."""
    resp = client.post("/ask", json={"question": "why did fill rate drop in the West last week"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "unavailable"
    for env_var in ("GROQ_API_KEY", "ANTHROPIC_API_KEY"):
        assert env_var not in body["answer"]
        assert not any(env_var in c for c in body["caveats"])


def test_llm_path_success_returns_answer_data_and_sql(client, groq_configured, monkeypatch):
    monkeypatch.setattr(
        ask_module, "_generate_sql_with_groq",
        lambda question, region_code: ("SELECT region_code, region_name FROM dim_region ORDER BY region_code", "Here are the regions"),
    )
    resp = client.post("/ask", json={"question": "list all sales regions"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mode"] == "llm"
    assert body["sql"].startswith("SELECT region_code")
    assert body["data"] and len(body["data"]) == 5
    assert "Here are the regions" in body["answer"]


def test_llm_path_disallowed_table_becomes_llm_error_not_500(client, groq_configured, monkeypatch):
    monkeypatch.setattr(
        ask_module, "_generate_sql_with_groq",
        lambda question, region_code: ("SELECT * FROM information_schema.tables", "Here you go"),
    )
    resp = client.post("/ask", json={"question": "show me the database schema"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mode"] == "llm_error"
    assert body["data"] is None
    assert body["sql"] == "SELECT * FROM information_schema.tables"
    assert any("not in the approved analytical layer" in c for c in body["caveats"])


def test_llm_path_mutation_sql_becomes_llm_error_not_500(client, groq_configured, monkeypatch):
    monkeypatch.setattr(
        ask_module, "_generate_sql_with_groq",
        lambda question, region_code: ("DROP TABLE dim_outlet", "Done"),
    )
    resp = client.post("/ask", json={"question": "delete the test outlets"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["mode"] == "llm_error"


def test_llm_path_syntactically_invalid_sql_becomes_llm_error_not_500(client, groq_configured, monkeypatch):
    """SQL that passes the guard's checks (single SELECT, no forbidden
    keywords, only approved tables) but is malformed and fails at DuckDB
    execution time must still degrade to llm_error, not a raw 500."""
    monkeypatch.setattr(
        ask_module, "_generate_sql_with_groq",
        lambda question, region_code: ("SELECT region_code FROM dim_region WHERE", "Here you go"),
    )
    resp = client.post("/ask", json={"question": "list region codes please"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mode"] == "llm_error"
    assert body["data"] is None


def test_llm_path_groq_call_failure_becomes_llm_error_not_500(client, groq_configured, monkeypatch):
    def _boom(question, region_code):
        raise ask_module.GroqCallError("simulated network failure")

    monkeypatch.setattr(ask_module, "_generate_sql_with_groq", _boom)
    resp = client.post("/ask", json={"question": "anything at all"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mode"] == "llm_error"
    assert "simulated network failure" in body["caveats"][0]


def test_llm_path_still_yields_to_fast_path(client, groq_configured, monkeypatch):
    """A configured key must not hijack a question that already matches a
    fast path -- the deterministic answer always wins."""
    monkeypatch.setattr(
        ask_module, "_generate_sql_with_groq",
        lambda question, region_code: (_ for _ in ()).throw(AssertionError("LLM path should not run for a fast-path question")),
    )
    resp = client.post("/ask", json={"question": "Which outlets ordered a discontinued SKU after its discontinuation date?"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["mode"] == "fast_path"


# ---------------------------------------------------------------------------
# Privacy layer (backend/app/privacy.py). Full ask() integration: a real
# request for personal data must never reach Groq at all, and Groq-generated
# SQL/results that somehow reference a blocked column must never execute or
# be returned. See test_privacy.py for pure unit tests of the policy itself.
# ---------------------------------------------------------------------------

class _FakeGroqMessage:
    def __init__(self, content):
        self.content = content


class _FakeGroqChoice:
    def __init__(self, content):
        self.message = _FakeGroqMessage(content)


class _FakeGroqCompletion:
    def __init__(self, content):
        self.choices = [_FakeGroqChoice(content)]


def _fake_groq_client(monkeypatch, calls, content='{"sql": "SELECT region_code FROM dim_region", "answer_intro": "ok"}'):
    """Replaces _get_groq_client() with a fake whose .chat.completions.create()
    records every call (so a test can assert Groq was/wasn't reached, and
    inspect exactly what was sent) instead of hitting the real network."""

    class _FakeCompletions:
        def create(self, **kwargs):
            calls.append(kwargs)
            return _FakeGroqCompletion(content)

    class _FakeChat:
        completions = _FakeCompletions()

    class _FakeClient:
        chat = _FakeChat()

    monkeypatch.setattr(ask_module, "_get_groq_client", lambda: _FakeClient())


def test_blocked_personal_data_request_never_calls_groq(client, groq_configured, monkeypatch):
    calls = []
    _fake_groq_client(monkeypatch, calls)
    resp = client.post("/ask", json={"question": "Show customer phone numbers"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mode"] == "blocked"
    assert body["data"] is None
    assert body["sql"] is None
    assert calls == [], "Groq must never be called for a blocked personal-data request"


@pytest.mark.parametrize("question", [
    "Give me customer email addresses.",
    "Show customer names.",
    "List personal addresses.",
    "Give me Aadhaar numbers.",
])
def test_blocked_personal_data_categories_all_blocked(client, groq_configured, monkeypatch, question):
    calls = []
    _fake_groq_client(monkeypatch, calls)
    resp = client.post("/ask", json={"question": question})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mode"] == "blocked"
    assert calls == []


# ---------------------------------------------------------------------------
# Closed gap: direct requests for a protected personal-data category (names,
# not just contact-value fields) must be caught by the question-level check
# and never reach Groq at all -- mode="blocked", sql=None, no network call.
# See privacy.py's _BLOCKED_REQUEST_PHRASES and DECISIONS.md.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("question", [
    "List all salesperson full names",
    "Show warehouse manager names",
    "Give regional manager names",
])
def test_salesperson_and_manager_name_requests_blocked_before_groq(client, groq_configured, monkeypatch, question):
    calls = []
    _fake_groq_client(monkeypatch, calls)
    resp = client.post("/ask", json={"question": question})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mode"] == "blocked"
    assert body["sql"] is None
    assert body["data"] is None
    assert calls == [], "Groq must never be called for a blocked personal-data-name request"


@pytest.mark.parametrize("question", [
    "Which region has the highest freight cost?",
    "Which outlets had the highest fill rate?",
])
def test_legitimate_business_questions_not_blocked(client, groq_configured, monkeypatch, question):
    """The two explicit non-PII sanity checks: a legitimate business
    question must not be caught by the widened phrase list -- it either
    hits a fast path or genuinely reaches Groq, never mode="blocked"."""
    calls = []
    _fake_groq_client(monkeypatch, calls)
    resp = client.post("/ask", json={"question": question})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mode"] in ("fast_path", "llm")


def test_pii_value_in_question_never_reaches_groq(client, groq_configured, monkeypatch):
    """The exact scenario from the security review: a question containing
    an embedded email address. The original PII must never be sent to
    Groq -- verified by asserting Groq is never even called. (The
    response's own `question` field legitimately echoes back what the
    user typed -- that's normal request/response UX, not a leak to a
    third party; what must never contain the PII is the generated
    answer/caveats, and what must never receive it is Groq.)"""
    calls = []
    _fake_groq_client(monkeypatch, calls)
    resp = client.post("/ask", json={"question": "Find orders for yash@example.com."})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mode"] == "blocked"
    assert "yash@example.com" not in body["answer"]
    assert not any("yash@example.com" in c for c in body["caveats"])
    assert calls == []


def test_groq_generated_sql_referencing_blocked_column_is_blocked_before_execution(client, groq_configured, monkeypatch):
    """Even if Groq somehow generates SELECT contact_email FROM dim_outlet
    -- which PASSES sql_guard, since dim_outlet is an approved table --
    the privacy layer's SQL-level check must reject it before db.run_query()
    ever executes it."""
    monkeypatch.setattr(
        ask_module, "_generate_sql_with_groq",
        lambda question, region_code: ("SELECT contact_email FROM dim_outlet", "Here you go"),
    )
    resp = client.post("/ask", json={"question": "look up the outlet contact"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mode"] == "blocked"
    assert body["data"] is None


def test_groq_generated_sql_with_aliased_blocked_column_is_blocked(client, groq_configured, monkeypatch):
    monkeypatch.setattr(
        ask_module, "_generate_sql_with_groq",
        lambda question, region_code: ("SELECT contact_phone AS phone FROM dim_outlet", "Here you go"),
    )
    resp = client.post("/ask", json={"question": "look up the outlet phone"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["mode"] == "blocked"


def test_safe_novel_question_still_reaches_groq_with_clean_schema(client, groq_configured, monkeypatch):
    """The end-to-end positive case: a genuinely novel, safe business
    question reaches Groq, and the schema/prompt Groq receives contains
    none of the blocked personal-data columns anywhere in its text."""
    calls = []
    _fake_groq_client(monkeypatch, calls)
    resp = client.post("/ask", json={"question": "Which region has the highest freight cost?"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mode"] == "llm"
    assert len(calls) == 1

    sent_text = json.dumps(calls[0]["messages"])
    for blocked_column in ask_module.privacy.ALL_BLOCKED_COLUMNS:
        assert blocked_column not in sent_text, f"{blocked_column!r} leaked into the Groq prompt"

    # Business identifiers must still be present -- the privacy layer must
    # not have accidentally stripped legitimate operational fields too.
    assert "region_code" in sent_text
    assert "outlet_code" in sent_text


@pytest.mark.parametrize("question", [
    "Which warehouse has the highest freight cost?",
    "Which region has the lowest fill rate?",
    "Which SKUs are most frequently ordered after discontinuation?",
    "Show late routes in the West region.",
])
def test_safe_questions_never_blocked(client, groq_configured, monkeypatch, question):
    """These are the "SAFE" example questions from the privacy review.
    Some happen to also match a fast-path keyword set (e.g. "freight" +
    "warehouse" matches q7) -- that's fine and expected, fast-path always
    wins and is privacy-safe by construction (hand-written queries, no
    LLM involved at all). The one thing that must never happen for a
    genuinely safe question is mode="blocked"."""
    calls = []
    _fake_groq_client(monkeypatch, calls)
    resp = client.post("/ask", json={"question": question})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mode"] in ("fast_path", "llm")
    if body["mode"] == "llm":
        assert len(calls) == 1


def test_blocked_response_never_echoes_the_privacy_reason_as_pii(client, groq_configured, monkeypatch):
    """The response body itself must stay safe -- no raw exception text,
    no echoed question fragment beyond the original `question` field the
    request itself already contained."""
    calls = []
    _fake_groq_client(monkeypatch, calls)
    resp = client.post("/ask", json={"question": "show me customer phone numbers please"})
    body = resp.json()
    assert body["mode"] == "blocked"
    assert body["answer"] == "Sorry, personal data cannot be queried through Ask Anything."
