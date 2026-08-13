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

# An identifier is either a bareword or a double-quoted string (DuckDB/
# Postgres-style quoting; "" inside a quoted identifier is an escaped
# literal quote). SQL_INJECTION-shaped input (garbage punctuation, stray
# quotes) simply fails to match either form and contributes no captured
# identifier, which is fine -- it still has to clear the SELECT/WITH-only
# and forbidden-keyword checks above, and produces no table match, which
# is what routes anything that isn't well-formed SQL to failing the
# eventual "no valid statement at all" path via DuckDB itself.
_IDENT = r'(?:"(?:[^"]|"")*"|[a-zA-Z_][a-zA-Z0-9_]*)'
# A SQL lexer treats a /* ... */ comment as equivalent to whitespace -- it
# can separate two tokens with no literal space character at all, e.g.
# `FROM/**/information_schema.tables` is valid, spec-compliant SQL that
# means exactly the same as `FROM information_schema.tables`. A separator
# defined as "one or more literal whitespace characters" misses that, so
# _SEP treats "whitespace or a block comment" as one unit and requires one
# or more of them wherever real SQL requires separation.
_SEP = r"(?:\s|/\*[\s\S]*?\*/)"
_SEP1 = _SEP + "+"
_SEP0 = _SEP + "*"
# Captures a FROM/JOIN target as either a single identifier (group 1) or a
# schema-qualified pair (group 1 = schema/catalog, group 2 = table) --
# `FROM foo`, `FROM "foo"`, `FROM foo.bar`, `FROM "foo"."bar"`, comment-
# obfuscated separators, and mixed quoting all match. This is the fix for
# the quoted-identifier bypass: the previous version only matched
# barewords, so `FROM "information_schema"."tables"` was invisible to the
# allowlist check entirely.
_TABLE_REF_RE = re.compile(
    rf"\b(?:FROM|JOIN){_SEP1}({_IDENT}){_SEP0}(?:\.{_SEP0}({_IDENT}))?",
    re.IGNORECASE,
)
_CTE_NAME_RE = re.compile(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\s+AS\s*\(", re.IGNORECASE)
# Old-style comma join (`FROM a, b`) puts a second table in the FROM list
# without a FROM/JOIN keyword in front of it, so _TABLE_REF_RE above never
# sees it -- `FROM dim_outlet, information_schema.tables` passed the
# allowlist untouched. Rather than parse the full table list, reject the
# construct outright: matches "FROM <table>[.<table>][ <alias>],", which is
# only possible when reachable from a table already covered above (comma
# right after the FROM target's identifier[.identifier][ alias]) -- every
# real query in this app uses explicit JOIN, so this has no false positives
# against approved SQL.
_COMMA_TABLE_LIST_RE = re.compile(
    rf"\bFROM{_SEP1}{_IDENT}(?:{_SEP0}\.{_SEP0}{_IDENT})?(?:{_SEP1}{_IDENT})?{_SEP0},",
    re.IGNORECASE,
)


class SQLGuardError(ValueError):
    pass


def _unquote(ident: str) -> str:
    """Strips DuckDB/Postgres-style double-quote identifier quoting and
    un-escapes a doubled quote ("") to a literal quote, so `"dim_outlet"`
    and `dim_outlet` compare equal against ALLOWED_TABLES."""
    if len(ident) >= 2 and ident[0] == '"' and ident[-1] == '"':
        return ident[1:-1].replace('""', '"')
    return ident


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

    if _COMMA_TABLE_LIST_RE.search(stripped):
        raise SQLGuardError(
            "Comma-separated FROM table lists (implicit joins) are not allowed -- use an explicit JOIN."
        )

    # CTE names (WITH foo AS (...), bar AS (...)) are legitimate FROM/JOIN
    # targets that aren't real tables -- exclude them from the allowlist
    # check rather than flagging every query that uses a WITH clause.
    cte_names = {m.group(1).lower() for m in _CTE_NAME_RE.finditer(stripped)}

    unqualified: set[str] = set()
    qualified: set[str] = set()
    for m in _TABLE_REF_RE.finditer(stripped):
        first, second = m.group(1), m.group(2)
        if second is not None:
            # schema.table (or "schema"."table", or any quoting mix) -- the
            # real table name is the final component. A schema-qualified
            # reference is never a CTE alias (CTEs can't be schema-prefixed
            # in DuckDB), so it is NEVER exempted by cte_names below -- this
            # closes a second bypass where a CTE could be deliberately
            # named the same as a disallowed table to smuggle a qualified
            # reference to that same name past the allowlist.
            qualified.add(_unquote(second).lower())
        else:
            unqualified.add(_unquote(first).lower())

    disallowed = (unqualified - ALLOWED_TABLES - cte_names) | (qualified - ALLOWED_TABLES)
    if disallowed:
        raise SQLGuardError(
            f"Query references table(s) not in the approved analytical layer: "
            f"{sorted(disallowed)}. Allowed: {sorted(ALLOWED_TABLES)}"
        )

    if not re.search(r"\bLIMIT\s+\d+\b", stripped, re.IGNORECASE):
        stripped += " LIMIT 500"

    return stripped
