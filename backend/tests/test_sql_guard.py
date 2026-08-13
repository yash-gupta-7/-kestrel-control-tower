"""
Unit tests for backend/app/sql_guard.py -- no database needed, this module
is pure string validation. Written after a real bug was found in this exact
guard (see DECISIONS.md, "SQL guard hardening"): the original table-name
regex only matched bareword identifiers, so `FROM "information_schema"."tables"`
was invisible to the allowlist check and passed straight through.

Covers, per the correction checkpoint requirements: a normal allowed view,
a disallowed bare table, a disallowed quoted table, a quoted schema.table
reference, a JOIN against a disallowed table, mutation statements, and
SQL-injection-shaped input.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest

from backend.app.sql_guard import SQLGuardError, validate_readonly_sql


# --- Allowed queries: should pass through unchanged (module appends a LIMIT
# only if the query doesn't already have one) -----------------------------

def test_allowed_simple_select():
    sql = "SELECT outlet_code FROM dim_outlet LIMIT 10"
    assert validate_readonly_sql(sql) == sql


def test_allowed_join_between_two_approved_tables():
    sql = "SELECT o.outlet_code FROM fact_order_lines ol JOIN dim_outlet o ON o.outlet_id = ol.outlet_id LIMIT 10"
    assert validate_readonly_sql(sql) == sql


def test_allowed_with_cte():
    sql = "WITH x AS (SELECT * FROM dim_outlet) SELECT * FROM x LIMIT 10"
    # Must not raise -- 'x' is a CTE alias, not a real table, and shouldn't
    # be flagged as disallowed.
    assert validate_readonly_sql(sql) == sql


def test_missing_limit_gets_one_appended():
    sql = "SELECT * FROM dim_region"
    result = validate_readonly_sql(sql)
    assert result.endswith("LIMIT 500")


# --- Disallowed tables, bareword -------------------------------------------

def test_disallowed_bare_table_raw_operational():
    with pytest.raises(SQLGuardError, match="not in the approved analytical layer"):
        validate_readonly_sql("SELECT * FROM orders LIMIT 10")


def test_disallowed_bare_table_system_catalog():
    with pytest.raises(SQLGuardError):
        validate_readonly_sql("SELECT * FROM information_schema LIMIT 10")


# --- Disallowed tables, quoted (the actual bug found in the audit) --------

def test_disallowed_quoted_table():
    with pytest.raises(SQLGuardError, match="not in the approved analytical layer"):
        validate_readonly_sql('SELECT * FROM "orders" LIMIT 10')


def test_disallowed_quoted_schema_table():
    """The exact bypass example from the audit: a schema-qualified, fully
    quoted reference to a disallowed system table."""
    with pytest.raises(SQLGuardError, match="not in the approved analytical layer"):
        validate_readonly_sql('SELECT * FROM "information_schema"."tables" LIMIT 10')


def test_disallowed_mixed_quoting_schema_table():
    with pytest.raises(SQLGuardError):
        validate_readonly_sql('SELECT * FROM information_schema."tables" LIMIT 10')
    with pytest.raises(SQLGuardError):
        validate_readonly_sql('SELECT * FROM "information_schema".tables LIMIT 10')


def test_qualified_reference_not_exempted_by_matching_cte_name():
    """A CTE named the same as a disallowed table must not exempt a
    schema-qualified reference to that same name -- CTEs can't be
    schema-prefixed, so a qualified reference is never a CTE alias."""
    sql = 'WITH tables AS (SELECT 1) SELECT * FROM information_schema.tables LIMIT 10'
    with pytest.raises(SQLGuardError):
        validate_readonly_sql(sql)


# --- Disallowed table via JOIN, not just FROM ------------------------------

def test_disallowed_table_via_join():
    with pytest.raises(SQLGuardError, match="not in the approved analytical layer"):
        validate_readonly_sql(
            "SELECT * FROM dim_outlet o JOIN raw_operational_secrets s ON s.id = o.outlet_id LIMIT 10"
        )


def test_disallowed_table_via_quoted_join():
    with pytest.raises(SQLGuardError):
        validate_readonly_sql(
            'SELECT * FROM dim_outlet o JOIN "information_schema"."columns" c ON 1=1 LIMIT 10'
        )


# --- Comma-separated FROM list (implicit join) -- found in the final
# release audit: the FROM/JOIN-keyword regex never saw a second table
# introduced by a bare comma, so a disallowed table smuggled in via
# `FROM a, b` passed the allowlist untouched. ------------------------------

def test_comma_separated_from_list_with_disallowed_table_rejected():
    with pytest.raises(SQLGuardError, match="implicit join"):
        validate_readonly_sql("SELECT * FROM dim_outlet, information_schema.tables LIMIT 10")


def test_comma_separated_from_list_rejected_even_with_allowed_tables():
    with pytest.raises(SQLGuardError, match="implicit join"):
        validate_readonly_sql("SELECT * FROM dim_outlet a, dim_route b LIMIT 10")


# --- Mutation / non-read-only statements -----------------------------------

@pytest.mark.parametrize("sql", [
    "DROP TABLE dim_outlet",
    "DELETE FROM fact_order_lines",
    "UPDATE dim_outlet SET outlet_name = 'x'",
    "INSERT INTO dim_outlet VALUES (1)",
    "ALTER TABLE dim_outlet ADD COLUMN x INT",
    "ATTACH 'evil.db' AS evil",
    "PRAGMA table_info('dim_outlet')",
    "COPY dim_outlet TO 'out.csv'",
    "INSTALL httpfs",
    "LOAD httpfs",
    "VACUUM",
    "CREATE TABLE evil AS SELECT * FROM dim_outlet",
])
def test_mutation_statements_rejected(sql):
    with pytest.raises(SQLGuardError):
        validate_readonly_sql(sql)


def test_multiple_statements_rejected():
    with pytest.raises(SQLGuardError, match="Multiple statements"):
        validate_readonly_sql("SELECT * FROM dim_outlet; DROP TABLE dim_outlet;")


def test_non_select_statement_rejected():
    with pytest.raises(SQLGuardError, match="Only SELECT"):
        validate_readonly_sql("EXPLAIN SELECT * FROM dim_outlet")


# --- SQL-injection-shaped input ---------------------------------------------

@pytest.mark.parametrize("sql", [
    "SELECT * FROM dim_outlet WHERE outlet_code = '' OR '1'='1'; DROP TABLE dim_outlet; --",
    "SELECT * FROM dim_outlet -- ; DROP TABLE dim_outlet",
    "SELECT * FROM dim_outlet UNION SELECT * FROM information_schema.tables",
    "'; DROP TABLE dim_outlet; --",
    "SELECT * FROM dim_outlet/**/UNION/**/SELECT/**/*/**/FROM/**/information_schema.tables",
])
def test_injection_shaped_input_rejected_or_safely_scoped(sql):
    # Every one of these either contains a forbidden keyword, references a
    # disallowed table, or trips the multiple-statements check -- none
    # should ever return successfully with access to something outside
    # ALLOWED_TABLES.
    with pytest.raises(SQLGuardError):
        validate_readonly_sql(sql)
