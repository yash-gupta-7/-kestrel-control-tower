"""
Unit tests for backend/app/privacy.py -- pure functions, no DB and no
network needed, run in any environment (same philosophy as
test_sql_guard.py). Covers all four layers: blocked-request detection,
PII-value detection/redaction, SQL-level column blocking, and
result-level column blocking.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest

from backend.app import privacy


# --- Policy content itself ---------------------------------------------------

def test_blocked_columns_match_the_real_schema():
    """Locks the exact set found by inspecting the live warehouse schema
    (see privacy.py module docstring) -- a change here should be a
    deliberate schema audit, not an accidental edit."""
    assert privacy.PII_BLOCKED_COLUMNS == {
        "dim_outlet": {"contact_name", "contact_phone", "contact_email", "gst_number"},
        "dim_salesperson": {"full_name"},
        "dim_warehouse": {"manager_name"},
        "dim_region": {"regional_manager"},
    }


def test_business_identifiers_are_not_blocked():
    assert not (privacy.BUSINESS_ALLOWED_IDENTIFIERS & privacy.ALL_BLOCKED_COLUMNS)


# --- Question-level: blocked-request phrases ---------------------------------

@pytest.mark.parametrize("question", [
    "show customer phone numbers",
    "give me customer emails",
    "show personal addresses",
    "list customer names",
    "give me Aadhaar numbers",
    "show bank account details",
    "what is the warehouse manager's name?",
    "show me the GST number for outlet OUT00001",
])
def test_blocked_request_phrases_rejected(question):
    with pytest.raises(privacy.PrivacyBlockedError) as exc:
        privacy.check_question_for_blocked_request(question)
    assert exc.value.reason == "blocked_personal_data_request"


@pytest.mark.parametrize("question", [
    "Which warehouse has the highest freight cost?",
    "Which region has the lowest fill rate?",
    "Which SKUs are most frequently ordered after discontinuation?",
    "Show late routes in the West region.",
    "Which outlets have the worst fill rate?",
    "Which routes are consistently late?",
])
def test_safe_business_questions_not_blocked(question):
    privacy.check_question_for_blocked_request(question)  # must not raise


# --- Question-level: embedded PII values --------------------------------------

def test_email_detected():
    assert privacy.detect_pii_value_category("Find orders for yash@example.com.") == "email"


def test_phone_detected():
    assert privacy.detect_pii_value_category("Call the outlet at 9876543210 please") == "phone"


def test_aadhaar_style_detected():
    assert privacy.detect_pii_value_category("What is linked to Aadhaar 1234 5678 9012?") == "aadhaar"


def test_pan_style_detected():
    assert privacy.detect_pii_value_category("Look up PAN ABCDE1234F for this account") == "pan"


@pytest.mark.parametrize("question", [
    "Which warehouse has the highest freight cost?",
    "Freight cost per delivered case, by warehouse, for the last quarter.",
    "How many chilled deliveries happened in 2026?",
])
def test_safe_questions_have_no_pii_value(question):
    assert privacy.detect_pii_value_category(question) is None


def test_redact_pii_replaces_email():
    redacted = privacy.redact_pii("Show orders associated with yash@example.com")
    assert "yash@example.com" not in redacted
    assert "[REDACTED_EMAIL]" in redacted


def test_redact_pii_replaces_phone():
    redacted = privacy.redact_pii("Contact them at 987-654-3210 about the delay")
    assert "987-654-3210" not in redacted
    assert "[REDACTED_PHONE]" in redacted


# --- SQL-level -----------------------------------------------------------------

@pytest.mark.parametrize("sql", [
    "SELECT contact_email FROM dim_outlet",
    "SELECT contact_phone, outlet_code FROM dim_outlet",
    "SELECT o.contact_name FROM dim_outlet o",
    "SELECT full_name FROM dim_salesperson",
    "SELECT manager_name FROM dim_warehouse",
    "SELECT regional_manager FROM dim_region",
    "SELECT gst_number FROM dim_outlet WHERE outlet_code = 'OUT00001'",
])
def test_sql_with_blocked_columns_rejected(sql):
    with pytest.raises(privacy.PrivacyBlockedError) as exc:
        privacy.check_sql_for_blocked_columns(sql)
    assert exc.value.reason == "blocked_sql_column"


@pytest.mark.parametrize("sql", [
    "SELECT outlet_code, outlet_name FROM dim_outlet",
    "SELECT o.outlet_code FROM fact_order_lines ol JOIN dim_outlet o ON o.outlet_id = ol.outlet_id",
    "SELECT region_code, region_name FROM dim_region",
    "SELECT warehouse_code, warehouse_name FROM dim_warehouse",
])
def test_sql_without_blocked_columns_passes(sql):
    privacy.check_sql_for_blocked_columns(sql)  # must not raise


# --- Result-level ----------------------------------------------------------------

def test_result_with_blocked_column_key_rejected():
    rows = [{"outlet_code": "OUT00001", "contact_email": "someone@example.com"}]
    with pytest.raises(privacy.PrivacyBlockedError) as exc:
        privacy.check_result_for_blocked_columns(rows)
    assert exc.value.reason == "blocked_result_column"


def test_result_without_blocked_columns_passes():
    rows = [{"outlet_code": "OUT00001", "fill_rate_pct": 85.6}]
    privacy.check_result_for_blocked_columns(rows)  # must not raise


def test_empty_result_passes():
    privacy.check_result_for_blocked_columns([])  # must not raise
