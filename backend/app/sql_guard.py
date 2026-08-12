"""
Read-only SQL validation for any query this service executes that did not
come from our own parametrized fast-path code -- i.e. the guard the future
LLM NL-to-SQL path (see routers/ask.py) must pass through before its SQL
ever reaches the database.

Not used to validate fast-path queries: those are built entirely in Python
with bound parameters and known table names, so they're safe by
construction. This module exists for SQL text of unknown origin.
"""
import re

from .db import ALLOWED_TABLES

_FORBIDDEN_KEYWORDS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|ATTACH|DETACH|CREATE|PRAGMA|"
    r"COPY|EXPORT|IMPORT|CALL|INSTALL|LOAD|VACUUM|CHECKPOINT|GRANT)\b",
    re.IGNORECASE,
)
_TABLE_REF_RE = re.compile(r"\b(?:FROM|JOIN)\s+([a-zA-Z_][a-zA-Z0-9_]*)", re.IGNORECASE)
_CTE_NAME_RE = re.compile(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\s+AS\s*\(", re.IGNORECASE)


class SQLGuardError(ValueError):
    pass


def validate_readonly_sql(sql: str) -> str:
    """Raises SQLGuardError if `sql` is anything other than a single
    read-only SELECT/WITH statement touching only tables in ALLOWED_TABLES.
    Returns the (trimmed) SQL unchanged if it passes."""
    stripped = sql.strip().rstrip(";")

    if ";" in stripped:
        raise SQLGuardError("Multiple statements are not allowed.")

    if not re.match(r"^\s*(SELECT|WITH)\b", stripped, re.IGNORECASE):
        raise SQLGuardError("Only SELECT / WITH ... SELECT statements are allowed.")

    if _FORBIDDEN_KEYWORDS.search(stripped):
        raise SQLGuardError("Query contains a forbidden (non-read-only) keyword.")

    # CTE names (WITH foo AS (...), bar AS (...)) are legitimate FROM/JOIN
    # targets that aren't real tables -- exclude them from the allowlist
    # check rather than flagging every query that uses a WITH clause.
    cte_names = {m.group(1).lower() for m in _CTE_NAME_RE.finditer(stripped)}
    referenced = {m.group(1).lower() for m in _TABLE_REF_RE.finditer(stripped)}
    disallowed = referenced - ALLOWED_TABLES - cte_names
    if disallowed:
        raise SQLGuardError(
            f"Query references table(s) not in the approved analytical layer: "
            f"{sorted(disallowed)}. Allowed: {sorted(ALLOWED_TABLES)}"
        )

    if not re.search(r"\bLIMIT\s+\d+\b", stripped, re.IGNORECASE):
        stripped += " LIMIT 500"

    return stripped
