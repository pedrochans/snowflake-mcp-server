"""Read-only SQL validation and identifier safety for the Snowflake MCP server.

This module centralises two security concerns that used to live inline in the
tool handlers:

1. **Read-only enforcement** - only ``SELECT``/``WITH``/``SHOW``/``DESCRIBE``/
   ``EXPLAIN``/``USE`` statements are permitted. The check is resilient to
   ``sqlglot`` version drift: newer ``sqlglot`` releases parse some read-only
   commands (notably ``EXPLAIN``) as a generic ``Command`` node instead of a
   typed expression, so we fall back to inspecting the leading keyword of the
   statement. Anything we cannot positively classify as read-only is rejected
   (fail-closed).

2. **Identifier safety** - database / schema / object names are interpolated
   into SQL text by several handlers. We validate them against an allow-list
   regex instead of quoting them, which both blocks injection and preserves
   Snowflake's case-insensitive identifier semantics.
"""

import re
from typing import List, Optional, cast

import sqlglot
from sqlglot.errors import ParseError
from sqlglot.expressions import Expression

# Statement types (sqlglot expression keys) considered read-only.
READ_ONLY_KEYS = {"select", "with", "show", "describe", "use", "explain"}

# Leading keywords accepted when sqlglot can only classify a statement as a
# generic ``Command`` (it could not build a typed AST). Anything not in this
# set is rejected.
READ_ONLY_COMMAND_PREFIXES = {
    "select",
    "with",
    "show",
    "describe",
    "desc",
    "explain",
    "use",
    "list",
}

# A single, unquoted Snowflake identifier part. Dotted names are validated
# part-by-part.
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")


class ReadOnlyViolation(ValueError):
    """Raised when a query contains a non read-only statement."""


class InvalidIdentifier(ValueError):
    """Raised when an identifier fails validation."""


def _statement_verb(statement: Optional[Expression]) -> str:
    """Return the lowercase verb that best describes *statement*.

    Uses the typed AST key when available, otherwise the leading token of the
    rendered SQL (the ``Command`` fallback path).
    """
    if statement is None:
        return ""
    key = (statement.key or "").lower()
    if key and key != "command":
        return key
    text = statement.sql(dialect="snowflake").strip()
    return text.split(None, 1)[0].lower() if text else ""


def _parse(query: str) -> List[Expression]:
    try:
        statements = sqlglot.parse(query, dialect="snowflake")
    except ParseError as e:
        raise ReadOnlyViolation(f"Could not parse SQL query: {e}") from e
    return [cast(Expression, s) for s in statements if s is not None]


def assert_read_only(query: str) -> None:
    """Validate that *query* contains only read-only statements.

    Raises:
        ReadOnlyViolation: if any statement is not read-only, or if the query
            is empty / cannot be parsed.
    """
    statements = _parse(query)
    if not statements:
        raise ReadOnlyViolation("Empty or unparseable SQL query")

    for statement in statements:
        key = (statement.key or "").lower()
        verb = _statement_verb(statement)
        if key in READ_ONLY_KEYS or verb in READ_ONLY_COMMAND_PREFIXES:
            continue
        raise ReadOnlyViolation(
            "Only read-only statements are allowed "
            "(SELECT, WITH, SHOW, DESCRIBE, EXPLAIN, USE). "
            f"Rejected statement type: {verb or key or 'unknown'}"
        )


def is_read_only(query: str) -> bool:
    """Return ``True`` if *query* is read-only, ``False`` otherwise."""
    try:
        assert_read_only(query)
        return True
    except ReadOnlyViolation:
        return False


def first_statement_verb(query: str) -> str:
    """Return the verb of the first statement, or ``""`` if none.

    Used to decide whether an automatic ``LIMIT`` clause should be appended.
    """
    statements = _parse(query)
    return _statement_verb(statements[0]) if statements else ""


def validate_identifier(name: Optional[str], kind: str = "identifier") -> str:
    """Validate a Snowflake object identifier and return it unchanged.

    Accepts simple identifiers and dotted paths (``db.schema.object``). Rejects
    anything containing whitespace, quotes, semicolons, comments or other
    characters that could break out of an identifier position.

    Raises:
        InvalidIdentifier: if *name* is missing or malformed.
    """
    if not name or not isinstance(name, str):
        raise InvalidIdentifier(f"Missing {kind}")
    for part in name.split("."):
        if not _IDENTIFIER_RE.match(part):
            raise InvalidIdentifier(
                f"Invalid {kind}: {name!r}. Only letters, digits, underscore "
                "and $ are allowed (dotted names permitted)."
            )
    return name
