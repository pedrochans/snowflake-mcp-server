"""In-process protocol tests for the MCP server layer.

These tests drive the real ``MCPServer`` instance through ``mcp.client.Client``
(in-memory transport, no sockets, no real Snowflake): the same code path a
stdio client exercises minus the pipes. They exist to catch regressions the
unit tests cannot see — e.g. an ``mcp`` upgrade that breaks server wiring while
the domain-layer tests stay green.
"""

from collections.abc import Iterator
from typing import Any, Callable, Optional, Sequence

import pytest
from mcp.client import Client
from snowflake.connector import SnowflakeConnection

from snowflake_mcp_server import main

EXPECTED_TOOLS = {
    "list_databases",
    "list_views",
    "describe_view",
    "query_view",
    "execute_query",
}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class FakeCursor:
    """Minimal scripted stand-in for a Snowflake cursor.

    ``script`` maps a SQL prefix (upper-cased) to the rows it should yield.
    ``description`` is derived from the ``__COLUMNS__`` entry when present.
    """

    def __init__(
        self,
        script: dict[str, list[tuple[Any, ...]]],
        executed: Optional[list[str]] = None,
    ) -> None:
        self._script = script
        self._rows: list[tuple[Any, ...]] = []
        self._executed = executed if executed is not None else []
        self.description: Optional[list[tuple[str, ...]]] = None

    def execute(self, sql: str) -> None:
        sql_upper = sql.strip().upper()
        self._executed.append(sql_upper)
        for prefix, rows in self._script.items():
            if sql_upper.startswith(prefix):
                self._rows = rows
                columns = self._script.get("__COLUMNS__")
                if columns:
                    self.description = [(str(c[0]),) for c in columns]
                return
        self._rows = []

    def fetchone(self) -> Optional[tuple[Any, ...]]:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._rows

    def fetchmany(self, size: int) -> list[tuple[Any, ...]]:
        return self._rows[:size]

    def __iter__(self) -> Iterator[tuple[Any, ...]]:
        return iter(self._rows)

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def close(self) -> None:
        return None


class FakeConnection:
    def __init__(self, script: dict[str, list[tuple[Any, ...]]]) -> None:
        self._script = script
        self.executed: list[str] = []

    def cursor(self) -> FakeCursor:
        return FakeCursor(self._script, self.executed)


@pytest.fixture
def patched_manager(monkeypatch: pytest.MonkeyPatch) -> Callable[..., "FakeConnection"]:
    """Neutralize the connection manager; tests choose the scripted results."""
    monkeypatch.setattr(main.connection_manager, "initialize", lambda config: None)
    monkeypatch.setattr(main.connection_manager, "close", lambda: None)

    def set_script(script: dict[str, list[tuple[Any, ...]]]) -> FakeConnection:
        fake = FakeConnection(script)

        def run_with_connection(
            operation: Callable[[SnowflakeConnection], Any],
        ) -> Any:
            return operation(fake)  # type: ignore[arg-type]

        monkeypatch.setattr(
            main.connection_manager, "run_with_connection", run_with_connection
        )
        return fake

    return set_script


def _text(content: Sequence[Any]) -> str:
    return "\n".join(c.text for c in content if getattr(c, "text", None))


@pytest.mark.anyio
async def test_handshake_and_tool_list(
    patched_manager: Callable[..., FakeConnection],
) -> None:
    """The server connects and advertises the five read-only tools."""
    async with Client(main.server) as client:
        tools = await client.list_tools()

    by_name = {t.name: t for t in tools.tools}
    assert set(by_name) == EXPECTED_TOOLS

    for tool in by_name.values():
        assert tool.annotations is not None, tool.name
        assert tool.annotations.read_only_hint is True, tool.name

    # Signature-derived schemas expose required arguments and constraints
    eq_schema = by_name["execute_query"].input_schema
    assert eq_schema["required"] == ["query"]
    assert eq_schema["properties"]["limit"]["minimum"] == 1
    assert by_name["query_view"].input_schema["required"] == [
        "database",
        "view_name",
    ]


@pytest.mark.anyio
async def test_list_databases_returns_names(
    patched_manager: Callable[..., FakeConnection],
) -> None:
    patched_manager({"SHOW DATABASES": [("x", "DB_ONE"), ("x", "DB_TWO")]})

    async with Client(main.server) as client:
        result = await client.call_tool("list_databases", {})

    assert not result.is_error
    text = _text(result.content)
    assert "DB_ONE" in text and "DB_TWO" in text


@pytest.mark.anyio
async def test_execute_query_happy_path(
    patched_manager: Callable[..., FakeConnection],
) -> None:
    patched_manager(
        {
            "__COLUMNS__": [("ID",), ("NAME",)],
            "SELECT CURRENT_DATABASE()": [("MYDB", "MYSCHEMA")],
            "SELECT * FROM T": [(1, "alice"), (2, "bo|b")],
        }
    )

    async with Client(main.server) as client:
        result = await client.call_tool("execute_query", {"query": "SELECT * FROM t"})

    assert not result.is_error
    text = _text(result.content)
    assert "MYDB" in text and "alice" in text
    assert "bo\\|b" in text  # pipe escaping in markdown table

    # Hybrid output: typed structuredContent travels alongside the markdown
    sc = result.structured_content
    assert sc is not None
    assert sc["columns"] == ["ID", "NAME"]
    assert sc["rows"][0] == {"ID": 1, "NAME": "alice"}
    assert sc["row_count"] == 2
    assert sc["truncated"] is False


@pytest.mark.anyio
async def test_execute_query_rejects_writes(
    patched_manager: Callable[..., FakeConnection],
) -> None:
    patched_manager({})

    async with Client(main.server) as client:
        result = await client.call_tool(
            "execute_query", {"query": "DROP TABLE important"}
        )

    assert result.is_error
    assert "read-only" in _text(result.content).lower()


@pytest.mark.anyio
async def test_execute_query_validates_limit(
    patched_manager: Callable[..., FakeConnection],
) -> None:
    patched_manager({})

    async with Client(main.server) as client:
        result = await client.call_tool(
            "execute_query", {"query": "SELECT 1", "limit": 0}
        )

    assert result.is_error
    assert "limit" in _text(result.content).lower()


@pytest.mark.anyio
async def test_query_view_validates_identifier(
    patched_manager: Callable[..., FakeConnection],
) -> None:
    patched_manager({})

    async with Client(main.server) as client:
        result = await client.call_tool(
            "query_view",
            {"database": "my_db; DROP TABLE x", "view_name": "v"},
        )

    assert result.is_error
    assert "invalid database" in _text(result.content).lower()


@pytest.mark.anyio
async def test_execute_query_restores_session_context(
    patched_manager: Callable[..., FakeConnection],
) -> None:
    """A USE issued for one call must not leak into the next call."""
    fake = patched_manager(
        {
            "__COLUMNS__": [("ID",)],
            "SELECT CURRENT_DATABASE()": [("ORIG_DB", "ORIG_SCHEMA")],
            "SELECT 1": [(1,)],
        }
    )

    async with Client(main.server) as client:
        result = await client.call_tool(
            "execute_query", {"query": "SELECT 1", "database": "OTHER_DB"}
        )

    assert not result.is_error
    executed = fake.executed
    switch = executed.index("USE DATABASE OTHER_DB")
    assert "USE DATABASE ORIG_DB" in executed[switch + 1 :]
    assert "USE SCHEMA ORIG_SCHEMA" in executed[switch + 1 :]


@pytest.mark.anyio
async def test_tools_advertise_output_schema(
    patched_manager: Callable[..., FakeConnection],
) -> None:
    async with Client(main.server) as client:
        tools = await client.list_tools()

    by_name = {t.name: t for t in tools.tools}
    for name in ("execute_query", "query_view"):
        schema = by_name[name].output_schema
        assert schema is not None, name
        assert set(schema["required"]) >= {"columns", "rows", "row_count"}
