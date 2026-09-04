"""MCP server implementation for Snowflake.

This module provides a Model Context Protocol (MCP) server that allows Claude
to perform read-only operations against Snowflake databases. It connects to
Snowflake using either service account authentication with a private key or
external browser authentication. It exposes various tools for querying database
metadata and data, including support for multi-view and multi-database queries.

The server is built on the high-level ``MCPServer`` API (mcp >= 2.0): each tool
is a typed function whose signature generates the JSON Schema and validates
arguments, and raised exceptions are reported to the client as tool errors
(``isError: true``).
"""

# pip-system-certs patches the SSL stack to use the operating system trust
# store (Windows cert store, macOS keychain, Linux CA bundle). Import it before
# snowflake.connector so corporate / VPN TLS interception works on every OS.
import pip_system_certs.wrapt_requests  # noqa: F401  # isort: skip

import contextlib
import os
from collections.abc import AsyncIterator, Callable, Iterator, Sequence
from contextlib import asynccontextmanager
from typing import Annotated, Any, Optional, TypeVar

import anyio
from dotenv import load_dotenv
from mcp.server import MCPServer
from mcp_types import CallToolResult, TextContent, ToolAnnotations
from pydantic import BaseModel, Field, SecretStr
from snowflake.connector import SnowflakeConnection

from snowflake_mcp_server.utils.snowflake_conn import (
    AuthType,
    SnowflakeConfig,
    connection_manager,
)
from snowflake_mcp_server.utils.sql_guard import (
    assert_read_only,
    first_statement_verb,
    validate_identifier,
)

# Load environment variables from .env file
load_dotenv()

SERVER_VERSION = "0.4.0"

# Every tool in this server is a read-only query; advertise that to clients.
_READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False)

# Cell values longer than this are truncated in markdown tables to keep tool
# results at a size the client can digest.
_MAX_CELL_CHARS = 200


# Initialize Snowflake configuration from environment variables
def get_snowflake_config() -> SnowflakeConfig:
    """Load Snowflake configuration from environment variables."""
    auth_type_str = os.getenv("SNOWFLAKE_AUTH_TYPE", "private_key").lower()
    auth_type = (
        AuthType.PRIVATE_KEY
        if auth_type_str == "private_key"
        else AuthType.EXTERNAL_BROWSER
    )

    private_key_auth = auth_type == AuthType.PRIVATE_KEY
    private_key_passphrase = (
        os.getenv("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE") if private_key_auth else None
    )
    return SnowflakeConfig(
        account=os.getenv("SNOWFLAKE_ACCOUNT", ""),
        user=os.getenv("SNOWFLAKE_USER", ""),
        auth_type=auth_type,
        private_key_path=(
            os.getenv("SNOWFLAKE_PRIVATE_KEY_PATH", "") if private_key_auth else None
        ),
        private_key_passphrase=(
            SecretStr(private_key_passphrase) if private_key_passphrase else None
        ),
        warehouse=os.getenv("SNOWFLAKE_WAREHOUSE"),
        database=os.getenv("SNOWFLAKE_DATABASE"),
        schema_name=os.getenv("SNOWFLAKE_SCHEMA"),
        role=os.getenv("SNOWFLAKE_ROLE"),
        statement_timeout_seconds=int(
            os.getenv("SNOWFLAKE_STATEMENT_TIMEOUT_SECONDS", "300")
        ),
    )


@asynccontextmanager
async def _lifespan(_server: "MCPServer[None]") -> AsyncIterator[None]:
    """Configure the connection manager on startup, close it on shutdown.

    The manager connects lazily on first use (see ``snowflake_conn``), so
    startup never blocks the MCP handshake on an interactive browser login.
    """
    connection_manager.initialize(get_snowflake_config())
    try:
        yield
    finally:
        connection_manager.close()


server: MCPServer[None] = MCPServer(
    "snowflake-mcp-server",
    version=SERVER_VERSION,
    instructions="MCP server for performing read-only operations against Snowflake.",
    lifespan=_lifespan,
)


_T = TypeVar("_T")


async def _run_db(impl: Callable[[SnowflakeConnection], _T]) -> _T:
    """Run a synchronous tool implementation off the event loop.

    The blocking Snowflake work is executed in a worker thread, and the whole
    operation runs under the connection manager's lock so concurrent tool calls
    cannot corrupt the shared connection's session state.
    """

    def work() -> _T:
        return connection_manager.run_with_connection(impl)

    try:
        return await anyio.to_thread.run_sync(work)
    except Exception as e:
        message = str(e)
        # 250001 with external-browser auth almost always means SNOWFLAKE_USER
        # is not the user's Snowflake LOGIN_NAME. Point the user at the fix.
        if "250001" in message or "differs from the user" in message:
            raise RuntimeError(
                f"{message}\n\nHint: with external-browser SSO, SNOWFLAKE_USER "
                "must be your Snowflake LOGIN_NAME (run `DESC USER <you>;` in "
                "Snowsight and use the LOGIN_NAME value), not your display name."
            ) from e
        raise


class QueryResult(BaseModel):
    """Structured result for row-returning tools.

    Sent to the client as ``structuredContent`` alongside the human-readable
    markdown table in the text content.
    """

    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int
    truncated: bool = Field(
        description="True when the row limit was hit; more rows may exist"
    )
    database: Optional[str] = None
    schema_name: Optional[str] = None


def _to_json_value(val: object) -> Any:
    """Coerce a Snowflake cell value into a JSON-safe value."""
    if val is None or isinstance(val, (bool, int, float, str)):
        out = val
    else:
        out = str(val)  # Decimal, datetime, bytes, VARIANT, ...
    if isinstance(out, str) and len(out) > _MAX_CELL_CHARS:
        out = out[: _MAX_CELL_CHARS - 3] + "..."
    return out


def _query_result(
    column_names: Sequence[str],
    rows: Sequence[Sequence[object]],
    limit: int,
    database: Optional[str] = None,
    schema_name: Optional[str] = None,
) -> QueryResult:
    return QueryResult(
        columns=list(column_names),
        rows=[
            {col: _to_json_value(val) for col, val in zip(column_names, row)}
            for row in rows
        ],
        row_count=len(rows),
        truncated=len(rows) >= limit,
        database=database,
        schema_name=schema_name,
    )


def _hybrid_result(markdown: str, structured: QueryResult) -> CallToolResult:
    """Markdown text for humans/LLMs plus typed structuredContent for clients."""
    return CallToolResult(
        content=[TextContent(type="text", text=markdown)],
        structured_content=structured.model_dump(mode="json"),
    )


@contextlib.contextmanager
def _session_context(
    conn: SnowflakeConnection, database: Optional[str], schema: Optional[str]
) -> Iterator[tuple[str, str]]:
    """Temporarily switch the session's USE context and restore it on exit.

    The shared connection is long-lived, so a ``USE`` issued for one tool call
    must not leak into the next. Yields the effective ``(database, schema)``
    after any requested switch. Restoration is best-effort: it never masks the
    result (or error) of the wrapped operation.
    """
    original: Optional[tuple[Any, Any]] = None
    if database or schema:
        with conn.cursor() as cursor:
            cursor.execute("SELECT CURRENT_DATABASE(), CURRENT_SCHEMA()")
            original = cursor.fetchone()

    with conn.cursor() as cursor:
        if database:
            cursor.execute(f"USE DATABASE {database}")
        if schema:
            cursor.execute(f"USE SCHEMA {schema}")
        cursor.execute("SELECT CURRENT_DATABASE(), CURRENT_SCHEMA()")
        row = cursor.fetchone()
    current = (
        str(row[0]) if row and row[0] else "Unknown",
        str(row[1]) if row and row[1] else "Unknown",
    )

    try:
        yield current
    finally:
        if original is not None:
            orig_db, orig_schema = original
            try:
                with conn.cursor() as cursor:
                    if orig_db:
                        cursor.execute(
                            f"USE DATABASE {validate_identifier(str(orig_db))}"
                        )
                        if orig_schema:
                            cursor.execute(
                                f"USE SCHEMA {validate_identifier(str(orig_schema))}"
                            )
            except Exception:
                pass  # best-effort restore; never mask the query result


def _resolve_schema(conn: SnowflakeConnection, schema: Optional[str]) -> str:
    """Return the validated *schema*, or resolve the session's current schema."""
    if schema:
        return validate_identifier(schema, "schema")
    with conn.cursor() as cursor:
        cursor.execute("SELECT CURRENT_SCHEMA()")
        schema_result = cursor.fetchone()
    if not schema_result or not schema_result[0]:
        raise RuntimeError(
            "Could not determine current schema; pass the schema argument"
        )
    return str(schema_result[0])


def _markdown_table(
    column_names: Sequence[str], rows: Sequence[Sequence[object]]
) -> str:
    """Render query results as a markdown table.

    ``None`` becomes ``NULL``, pipes are escaped, and long values are truncated
    to ``_MAX_CELL_CHARS`` characters.
    """
    lines = [
        "| " + " | ".join(column_names) + " |",
        "| " + " | ".join("---" for _ in column_names) + " |",
    ]
    for row in rows:
        formatted_values = []
        for val in row:
            if val is None:
                formatted_values.append("NULL")
            else:
                val_str = str(val).replace("|", "\\|")
                if len(val_str) > _MAX_CELL_CHARS:
                    val_str = val_str[: _MAX_CELL_CHARS - 3] + "..."
                formatted_values.append(val_str)
        lines.append("| " + " | ".join(formatted_values) + " |")
    return "\n".join(lines)


@server.tool(
    description="List all accessible Snowflake databases",
    annotations=_READ_ONLY,
)
async def list_databases() -> str:
    """Tool handler to list all accessible Snowflake databases."""

    def impl(conn: SnowflakeConnection) -> str:
        with conn.cursor() as cursor:
            cursor.execute("SHOW DATABASES")
            # Database name is in the second column
            databases = [row[1] for row in cursor]
        return "Available Snowflake databases:\n" + "\n".join(databases)

    return await _run_db(impl)


@server.tool(
    description="List all views in a specified database and schema",
    annotations=_READ_ONLY,
)
async def list_views(
    database: Annotated[str, Field(description="The database name")],
    schema: Annotated[
        Optional[str],
        Field(description="The schema name (uses current schema if not provided)"),
    ] = None,
) -> str:
    """Tool handler to list views in a specified database and schema."""

    def impl(conn: SnowflakeConnection) -> str:
        db = validate_identifier(database, "database")
        if schema:
            validate_identifier(schema, "schema")

        with _session_context(conn, db, schema) as (_, current_schema):
            sch = schema or current_schema
            if sch == "Unknown":
                raise RuntimeError(
                    "Could not determine current schema; pass the schema argument"
                )

            views = []
            with conn.cursor() as cursor:
                cursor.execute(f"SHOW VIEWS IN {db}.{sch}")
                for row in cursor:
                    view_name = row[1]  # View name is in the second column
                    created_on = row[5]  # Creation date
                    views.append(f"{view_name} (created: {created_on})")

        if not views:
            return f"No views found in {db}.{sch}"
        return f"Views in {db}.{sch}:\n" + "\n".join(views)

    return await _run_db(impl)


@server.tool(
    description=(
        "Get detailed information about a specific view including columns "
        "and SQL definition"
    ),
    annotations=_READ_ONLY,
)
async def describe_view(
    database: Annotated[str, Field(description="The database name")],
    view_name: Annotated[str, Field(description="The name of the view to describe")],
    schema: Annotated[
        Optional[str],
        Field(description="The schema name (uses current schema if not provided)"),
    ] = None,
) -> str:
    """Tool handler to describe the structure of a view."""

    def impl(conn: SnowflakeConnection) -> str:
        db = validate_identifier(database, "database")
        view = validate_identifier(view_name, "view_name")
        sch = _resolve_schema(conn, schema)
        full_view_name = f"{db}.{sch}.{view}"

        # Describe the view and fetch its definition
        columns = []
        with conn.cursor() as cursor:
            cursor.execute(f"DESCRIBE VIEW {full_view_name}")
            for row in cursor:
                col_name = row[0]
                col_type = row[1]
                col_null = "NULL" if row[3] == "Y" else "NOT NULL"
                columns.append(f"{col_name} : {col_type} {col_null}")

            cursor.execute(f"SELECT GET_DDL('VIEW', '{full_view_name}')")
            view_ddl_result = cursor.fetchone()
            view_ddl = (
                view_ddl_result[0] if view_ddl_result else "Definition not available"
            )

        if not columns:
            return (
                f"View {full_view_name} not found or you don't have permission "
                "to access it."
            )

        result = f"## View: {full_view_name}\n\n"
        result += "### Columns:\n"
        for col in columns:
            result += f"- {col}\n"
        result += "\n### View Definition:\n```sql\n"
        result += view_ddl
        result += "\n```"
        return result

    return await _run_db(impl)


@server.tool(
    description="Query data from a view with an optional row limit",
    annotations=_READ_ONLY,
)
async def query_view(
    database: Annotated[str, Field(description="The database name")],
    view_name: Annotated[str, Field(description="The name of the view to query")],
    schema: Annotated[
        Optional[str],
        Field(description="The schema name (uses current schema if not provided)"),
    ] = None,
    limit: Annotated[
        int, Field(description="Maximum number of rows to return", ge=1)
    ] = 10,
) -> QueryResult:
    """Tool handler to query data from a view with optional limit."""

    def impl(conn: SnowflakeConnection) -> CallToolResult:
        db = validate_identifier(database, "database")
        view = validate_identifier(view_name, "view_name")
        sch = _resolve_schema(conn, schema)
        full_view_name = f"{db}.{sch}.{view}"

        with conn.cursor() as cursor:
            cursor.execute(f"SELECT * FROM {full_view_name} LIMIT {limit}")
            column_names = (
                [col[0] for col in cursor.description] if cursor.description else []
            )
            rows = cursor.fetchall()

        if not rows:
            markdown = f"No data found in view {full_view_name} or the view is empty."
        else:
            markdown = f"## Data from {full_view_name} (Showing {len(rows)} rows)\n\n"
            markdown += _markdown_table(column_names, rows)
        structured = _query_result(
            column_names, rows, limit, database=db, schema_name=sch
        )
        return _hybrid_result(markdown, structured)

    # A CallToolResult carrying both markdown text and structuredContent is a
    # supported return value; the annotation drives the outputSchema.
    return await _run_db(impl)  # type: ignore[arg-type,return-value]


@server.tool(
    description=(
        "Execute read-only SQL queries (SELECT, SHOW, DESCRIBE, EXPLAIN, WITH, "
        "USE). Use SHOW for metadata (TABLES/PIPES/TASKS/STREAMS/GRANTS/"
        "PROCEDURES/FUNCTIONS), INFORMATION_SCHEMA for object details, "
        "ACCOUNT_USAGE for audit data."
    ),
    annotations=_READ_ONLY,
)
async def execute_query(
    query: Annotated[
        str,
        Field(
            description=(
                "SQL query to execute. Supports: SELECT, SHOW commands "
                "(TABLES/PIPES/TASKS/STREAMS/GRANTS/PROCEDURES/FUNCTIONS), "
                "INFORMATION_SCHEMA queries, ACCOUNT_USAGE queries, USE statements"
            )
        ),
    ],
    database: Annotated[
        Optional[str], Field(description="The database to use (optional)")
    ] = None,
    schema: Annotated[
        Optional[str], Field(description="The schema to use (optional)")
    ] = None,
    limit: Annotated[
        int, Field(description="Maximum number of rows to return", ge=1)
    ] = 100,
) -> QueryResult:
    """Tool handler to execute read-only SQL queries against Snowflake."""
    # Validate that the query is read-only before touching the database
    # (raises ReadOnlyViolation, surfaced to the client as a tool error).
    assert_read_only(query)

    def impl(conn: SnowflakeConnection) -> CallToolResult:
        sql = query
        db = validate_identifier(database, "database") if database else None
        sch = validate_identifier(schema, "schema") if schema else None

        # Switch context for this call only; restored when the block exits
        with _session_context(conn, db, sch) as (current_db, current_schema):
            # Only add a LIMIT clause for row-returning queries (SELECT/WITH)
            needs_limit = first_statement_verb(sql) in {"select", "with"}
            if needs_limit and "LIMIT " not in sql.upper():
                # Remove any trailing semicolon before adding the LIMIT clause
                sql = sql.rstrip().rstrip(";")
                sql = f"{sql} LIMIT {limit};"

            # Execute the query
            with conn.cursor() as cursor:
                cursor.execute(sql)
                column_names = (
                    [col[0] for col in cursor.description] if cursor.description else []
                )
                rows = cursor.fetchmany(limit)

        if not rows:
            markdown = (
                f"Query executed successfully in {current_db}.{current_schema}, "
                "but returned no results."
            )
        else:
            row_count = len(rows)
            markdown = (
                f"## Query Results "
                f"(Database: {current_db}, Schema: {current_schema})\n\n"
            )
            markdown += f"Showing {row_count} row{'s' if row_count != 1 else ''}\n\n"
            markdown += f"```sql\n{sql}\n```\n\n"
            markdown += _markdown_table(column_names, rows)

        structured = _query_result(
            column_names, rows, limit, database=current_db, schema_name=current_schema
        )
        return _hybrid_result(markdown, structured)

    # A CallToolResult carrying both markdown text and structuredContent is a
    # supported return value; the annotation drives the outputSchema.
    return await _run_db(impl)  # type: ignore[arg-type,return-value]


# Function to run the server with stdio interface
def run_stdio_server() -> None:
    """Run the MCP server using stdin/stdout for communication."""
    server.run("stdio")
