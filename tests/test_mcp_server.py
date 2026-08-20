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

    def __init__(self, script: dict[str, list[tuple[Any, ...]]]) -> None:
        self._script = script
        self._rows: list[tuple[Any, ...]] = []
        self.description: Optional[list[tuple[str, ...]]] = None

    def execute(self, sql: str) -> None:
        sql_upper = sql.strip().upper()
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

    def cursor(self) -> FakeCursor:
        return FakeCursor(self._script)


@pytest.fixture
def patched_manager(monkeypatch: pytest.MonkeyPatch) -> Callable[..., None]:
    """Neutralize the connection manager; tests choose the scripted results."""
    monkeypatch.setattr(main.connection_manager, "initialize", lambda config: None)
    monkeypatch.setattr(main.connection_manager, "close", lambda: None)

    def set_script(script: dict[str, list[tuple[Any, ...]]]) -> None:
        fake = FakeConnection(script)

        def run_with_connection(
            operation: Callable[[SnowflakeConnection], str],
        ) -> str:
            return operation(fake)  # type: ignore[arg-type]

        monkeypatch.setattr(
            main.connection_manager, "run_with_connection", run_with_connection
        )

    return set_script


def _text(content: Sequence[Any]) -> str:
    return "\n".join(c.text for c in content if getattr(c, "text", None))


@pytest.mark.anyio
async def test_handshake_and_tool_list(patched_manager: Callable[..., None]) -> None:
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
    patched_manager: Callable[..., None],
) -> None:
    patched_manager({"SHOW DATABASES": [("x", "DB_ONE"), ("x", "DB_TWO")]})

    async with Client(main.server) as client:
        result = await client.call_tool("list_databases", {})

    assert not result.is_error
    text = _text(result.content)
    assert "DB_ONE" in text and "DB_TWO" in text


@pytest.mark.anyio
async def test_execute_query_happy_path(
    patched_manager: Callable[..., None],
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


@pytest.mark.anyio
async def test_execute_query_rejects_writes(
    patched_manager: Callable[..., None],
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
    patched_manager: Callable[..., None],
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
    patched_manager: Callable[..., None],
) -> None:
    patched_manager({})

    async with Client(main.server) as client:
        result = await client.call_tool(
            "query_view",
            {"database": "my_db; DROP TABLE x", "view_name": "v"},
        )

    assert result.is_error
    assert "invalid database" in _text(result.content).lower()
