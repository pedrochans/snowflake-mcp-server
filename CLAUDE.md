# CLAUDE.md - Snowflake MCP Server (Python)

## Build & Run Commands
- Setup: `uv sync --extra dev`
- Start server (stdio): `uv run snowflake-mcp`

## Test Commands
- Run all tests: `uv run pytest`
- Run single test: `uv run pytest tests/test_file.py::test_function`
- Test coverage: `uv run pytest --cov=snowflake_mcp_server`

## Lint & Format
- Lint: `uv run ruff check .`
- Format code: `uv run ruff format .`
- Type check: `uv run mypy snowflake_mcp_server/ tests/`

## Architecture
- `snowflake_mcp_server/main.py` — MCP layer: `MCPServer` (mcp >= 2.0), tools as
  typed async functions via `@server.tool(...)`; schemas and argument validation
  derive from signatures; exceptions surface to the client as `isError` results
- `snowflake_mcp_server/utils/snowflake_conn.py` — connection manager singleton:
  lazy connect, session-expiry recovery, browser-auth cooldown, refresh thread
- `snowflake_mcp_server/utils/sql_guard.py` — read-only SQL enforcement
  (sqlglot) and identifier validation
- `tests/test_mcp_server.py` — in-process protocol tests via `mcp.client.Client`

## Code Style Guidelines
- Use Python 3.12+ type annotations everywhere (mypy strict-ish config)
- Format with Ruff, line length 88 characters
- Organize imports with Ruff (stdlib, third-party, first-party)
- Blocking Snowflake work runs in a worker thread via `anyio.to_thread` under
  the connection manager's lock — never on the event loop
- Prefer Pydantic models for structured data
- Follow PEP8 naming: snake_case for functions/variables, PascalCase for classes
- Document public functions with docstrings (Google style preferred)
- Never interpolate unvalidated input into SQL: identifiers go through
  `validate_identifier`, queries through `assert_read_only`
- Use environment variables for configuration (loaded via python-dotenv)
