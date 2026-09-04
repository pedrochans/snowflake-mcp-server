"""Tests for the read-only SQL guard and identifier validation."""

import pytest

from snowflake_mcp_server.utils.sql_guard import (
    InvalidIdentifier,
    ReadOnlyViolation,
    assert_read_only,
    first_statement_verb,
    is_read_only,
    validate_identifier,
)

READ_ONLY_QUERIES = [
    "SELECT 1",
    "select * from t",
    "SHOW DATABASES",
    "SHOW TABLES IN SCHEMA db.public",
    "DESCRIBE VIEW a.b.c",
    "DESC TABLE t",
    "EXPLAIN SELECT 1",
    "WITH x AS (SELECT 1) SELECT * FROM x",
    "USE DATABASE FOO",
    "USE SCHEMA WORK",
    "SELECT current_database(), current_schema()",
    # Set operators: sqlglot keys these as union/intersect/except, not select
    "SELECT 1 UNION ALL SELECT 2",
    "SELECT 1 UNION SELECT 2 UNION ALL SELECT 3",
    "WITH x AS (SELECT 1 a) SELECT a FROM x UNION ALL SELECT 2",
    "SELECT 1 INTERSECT SELECT 1",
    "SELECT 1 EXCEPT SELECT 2",
]

WRITE_QUERIES = [
    "DELETE FROM t",
    "DROP TABLE t",
    "INSERT INTO t VALUES (1)",
    "UPDATE t SET a = 1",
    "CREATE TABLE t (a int)",
    "ALTER TABLE t ADD COLUMN b int",
    "TRUNCATE TABLE t",
    "MERGE INTO t USING s ON t.a = s.a WHEN MATCHED THEN UPDATE SET t.a = 1",
    "GRANT SELECT ON t TO ROLE r",
    "REVOKE SELECT ON t FROM ROLE r",
    "CALL my_proc()",
    "SELECT 1; DROP TABLE t",  # multi-statement smuggling
    # A set operator must not launder a write into the allow-list
    "INSERT INTO t SELECT 1 UNION ALL SELECT 2",
    "",
    "   ",
]


@pytest.mark.parametrize("query", READ_ONLY_QUERIES)
def test_read_only_allows(query: str) -> None:
    assert is_read_only(query) is True
    assert_read_only(query)  # should not raise


@pytest.mark.parametrize("query", WRITE_QUERIES)
def test_read_only_blocks(query: str) -> None:
    assert is_read_only(query) is False
    with pytest.raises(ReadOnlyViolation):
        assert_read_only(query)


def test_explain_survives_sqlglot_command_fallback() -> None:
    # Regression: newer sqlglot parses EXPLAIN as a generic Command node.
    assert_read_only("EXPLAIN SELECT * FROM big_table")


def test_first_statement_verb() -> None:
    assert first_statement_verb("SELECT 1") == "select"
    assert first_statement_verb("WITH x AS (SELECT 1) SELECT * FROM x") in {
        "with",
        "select",
    }
    assert first_statement_verb("USE DATABASE FOO") == "use"
    assert first_statement_verb("EXPLAIN SELECT 1") == "explain"


@pytest.mark.parametrize(
    "name",
    ["FINANCIERO", "WORK", "my_view", "db.schema.view", "T123", "_hidden", "a$b"],
)
def test_valid_identifiers(name: str) -> None:
    assert validate_identifier(name) == name


@pytest.mark.parametrize(
    "name",
    [
        "",
        "a b",
        "a;DROP TABLE t",
        'a"b',
        "a'b",
        "1abc",
        "a-b",
        "a/*x*/b",
        "a) UNION SELECT",
    ],
)
def test_invalid_identifiers(name: str) -> None:
    with pytest.raises(InvalidIdentifier):
        validate_identifier(name)
