"""
Rule-based, deterministic personal-data (PII) protection for the Ask
Anything LLM path (backend/app/routers/ask.py).

No LLM is used to detect PII here, on purpose -- every check in this
module is a static allowlist/blocklist or a regex. That makes it
auditable and means it never depends on a model's judgement about what
counts as personal data. This is defense in depth, not a claim that PII
leakage is impossible -- see the module-level caveat at the bottom.

Why this exists as a *separate* layer from sql_guard.py: sql_guard only
allowlists TABLES (a query against `dim_outlet` passes the guard whether
it selects `outlet_code` or `contact_email` -- both are columns on an
approved table). This module adds the COLUMN-level check sql_guard does
not do, at three points: what schema/question reaches the LLM, what SQL
is allowed to execute, and what result rows are allowed back out.

Blocked columns below were found by inspecting the actual raw operational
schema (assignment pack's 02_Data_Dictionary.md) and the live
warehouse.duckdb dim_* tables (`DESCRIBE dim_outlet` etc.) -- not
invented. This is a B2B distributor dataset with no individual-customer
table at all; the personal data that exists is outlet contact details and
employee names carried on otherwise-legitimate business dimension tables.
"""
import re
from typing import Optional

# Real personal-data-bearing columns, by warehouse table. sql_guard.py
# only allowlists tables, not columns -- a table being "approved" (e.g.
# dim_outlet) does not mean every column on it is safe to hand to an LLM
# or return from a free-form query. Verified against the live schema:
#   dim_outlet:      contact_name, contact_phone, contact_email (the
#                     outlet's contact person -- an individual, not the
#                     business), gst_number (a government-issued tax ID)
#   dim_salesperson:  full_name (a Kestrel field employee)
#   dim_warehouse:    manager_name (a Kestrel employee)
#   dim_region:       regional_manager (a Kestrel employee -- note this
#                     column IS legitimately shown in the region-selector
#                     UI via GET /meta/regions, a fixed, reviewed,
#                     brief-driven feature; blocking it here only affects
#                     the free-form LLM path, not that endpoint)
PII_BLOCKED_COLUMNS: dict[str, set[str]] = {
    "dim_outlet": {"contact_name", "contact_phone", "contact_email", "gst_number"},
    "dim_salesperson": {"full_name"},
    "dim_warehouse": {"manager_name"},
    "dim_region": {"regional_manager"},
}

# Flat set for checks that don't know which table a reference belongs to
# (raw SQL text, or a result row's column names). Deliberately
# conservative: any of these words appearing as a column-shaped identifier
# is treated as blocked regardless of table, since none of them collide
# with a legitimate business-identifier name in this schema.
ALL_BLOCKED_COLUMNS: set[str] = {c for cols in PII_BLOCKED_COLUMNS.values() for c in cols}

# Operational business identifiers -- explicitly NOT personal data. Kept
# here so the distinction is documented and testable, not just assumed;
# these are the identifiers Ask Anything's free-form path must keep being
# able to use ("which outlets have the worst fill rate?" etc.).
BUSINESS_ALLOWED_IDENTIFIERS: set[str] = {
    "outlet_code", "outlet_id", "outlet_name",
    "route_code", "route_id", "route_name",
    "warehouse_code", "warehouse_id", "warehouse_name",
    "sku_code", "region_code", "region_id", "region_name",
}


class PrivacyBlockedError(Exception):
    """Raised when a request or a piece of SQL/result data is blocked by
    the privacy policy. `reason` is always a short, safe machine token
    (e.g. "blocked_personal_data_request") -- never the original question
    text or a detected PII value. Callers must only log/expose `reason`,
    never str(exception) built from user input."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


# --- Question-level: detect a request FOR a blocked field -------------------

# Deterministic keyword check for "give me a blocked personal field",
# independent of whether a specific value is present in the question. Not
# an attempt at general natural-language understanding -- a fixed phrase
# list, checked as substrings of the lowercased question.
_BLOCKED_REQUEST_PHRASES = (
    "customer name", "customer names", "contact name", "contact names",
    "phone number", "phone numbers", "customer phone", "mobile number", "mobile numbers",
    "email address", "email addresses", "customer email", "customer emails",
    "personal address", "personal addresses", "home address",
    "aadhaar", "pan number", "pan card",
    "bank account", "bank details", "account number",
    "personal notes", "manager name", "manager's name", "manager names",
    "employee name", "employee names", "employee full name", "employee full names",
    "salesperson's name", "salesperson name", "salesperson names",
    "salesperson full name", "salesperson full names",
    "warehouse manager name", "warehouse manager names",
    "regional manager name", "regional manager names",
    "personal contact details", "contact details",
    "gst number", "gstin",
)


def check_question_for_blocked_request(question: str) -> None:
    """Raises PrivacyBlockedError if the question asks for a category of
    personal data outright (phone numbers, emails, names, government IDs,
    ...), regardless of whether a concrete value is present. Must run
    BEFORE the question is ever sent to Groq -- if this fires, Groq is
    never called."""
    q = question.lower()
    for phrase in _BLOCKED_REQUEST_PHRASES:
        if phrase in q:
            raise PrivacyBlockedError("blocked_personal_data_request")


# --- Question-level: detect/redact a PII VALUE embedded in the question ----

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
# 12-digit Aadhaar-style number, optionally space-separated in groups of 4.
_AADHAAR_RE = re.compile(r"(?<!\d)\d{4}[ ]?\d{4}[ ]?\d{4}(?!\d)")
# Indian PAN: 5 letters, 4 digits, 1 letter (e.g. ABCDE1234F).
_PAN_RE = re.compile(r"\b[A-Za-z]{5}\d{4}[A-Za-z]\b")
# 10-digit Indian mobile, or a hyphen/space/dot separated 10-digit number,
# optionally with a country code -- checked AFTER Aadhaar so a 12-digit
# run isn't also flagged as a phone. Deliberately broad: a false positive
# (over-blocking a large plain number) fails safe; that's an accepted
# trade-off for a deterministic layer, not a bug (see module docstring).
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?\d{1,3}[-.\s]?)?(?:\d{3}[-.\s]\d{3}[-.\s]\d{4}|\d{10})(?!\d)")

# Order matters: Aadhaar/PAN/email are checked before the broader phone
# pattern so a 12-digit Aadhaar number isn't partially re-matched as a
# 10-digit phone number.
_PII_VALUE_PATTERNS = (
    ("email", _EMAIL_RE),
    ("aadhaar", _AADHAAR_RE),
    ("pan", _PAN_RE),
    ("phone", _PHONE_RE),
)


def detect_pii_value_category(question: str) -> Optional[str]:
    """Returns the category name of the first PII-shaped value found in
    `question` ("email", "aadhaar", "pan", or "phone"), or None. Used for
    the block decision and for safe logging -- callers must log only the
    returned category string, never the question itself."""
    for category, pattern in _PII_VALUE_PATTERNS:
        if pattern.search(question):
            return category
    return None


def redact_pii(question: str) -> str:
    """Replaces obvious PII-shaped substrings with a redaction marker.
    Deterministic, regex-based defense in depth -- available for
    logging/error-message use, and as the mechanism a less strict policy
    could build on. This deployment's policy (see ask.py) chooses to
    block outright on any detected PII value rather than forward a
    redacted question to Groq, since a question containing a specific
    email/phone/ID almost always means "look this contact up by X," which
    requires a blocked column to answer at all -- but the redaction
    function itself is exercised and tested independently."""
    q = _EMAIL_RE.sub("[REDACTED_EMAIL]", question)
    q = _AADHAAR_RE.sub("[REDACTED_ID]", q)
    q = _PAN_RE.sub("[REDACTED_ID]", q)
    q = _PHONE_RE.sub("[REDACTED_PHONE]", q)
    return q


# --- SQL-level: block generated SQL that references a blocked column -------

def check_sql_for_blocked_columns(sql: str) -> None:
    """Raises PrivacyBlockedError if `sql` references any blocked column
    name as a whole identifier, anywhere in the statement (SELECT list,
    WHERE, ORDER BY, ...). Runs AFTER sql_guard.validate_readonly_sql()
    -- both controls apply; this one exists because the guard only checks
    table names, not columns, so `SELECT contact_email FROM dim_outlet`
    passes the guard (dim_outlet is an approved table) and must be caught
    here instead."""
    for column in ALL_BLOCKED_COLUMNS:
        if re.search(rf"\b{re.escape(column)}\b", sql, re.IGNORECASE):
            raise PrivacyBlockedError("blocked_sql_column")


# --- Result-level: block a result set that somehow contains a blocked column

def check_result_for_blocked_columns(rows: list[dict]) -> None:
    """Raises PrivacyBlockedError if any row in a query result has a key
    matching a blocked column name. Final defense-in-depth layer, in case
    a blocked column reached execution under an alias the SQL-level check
    didn't resolve (e.g. `SELECT contact_email AS x` still returns a
    result key of `x`, which this alone would miss -- see 'Known
    limitations' in DECISIONS.md; the SQL-level check catches the
    unaliased reference before this ever runs)."""
    for row in rows:
        for key in row:
            if key.lower() in ALL_BLOCKED_COLUMNS:
                raise PrivacyBlockedError("blocked_result_column")


# --- Honest limitations, stated here rather than only in docs --------------
# This is deterministic allowlisting plus regex defense in depth, not a
# claim that personal-data leakage through Ask Anything is impossible:
#   - Phone/Aadhaar/PAN regexes are heuristic; they can both over-match
#     (a large plain number) and under-match (an unusual format).
#   - The SQL-level check is a substring/word-boundary scan, not a real
#     SQL parser -- an aliased blocked column (`SELECT contact_email AS
#     foo`) still fails at the SQL layer because `contact_email` itself
#     is still present in the SQL text, but a sufficiently obfuscated
#     reference could in principle evade a regex the way earlier
#     sql_guard bypasses did (see DECISIONS.md "SQL guard" history) --
#     the result-level check exists precisely as a second line of
#     defense for that class of gap.
#   - This module only knows about the blocked columns listed above,
#     verified against the current schema; a future warehouse column
#     added without updating PII_BLOCKED_COLUMNS would not be protected.
