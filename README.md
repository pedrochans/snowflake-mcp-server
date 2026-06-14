# Snowflake MCP Server

[![Python](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![MCP](https://img.shields.io/badge/MCP-compatible-7B5CFF.svg)](https://modelcontextprotocol.io)
[![Platforms](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey.svg)](#requirements)

A [Model Context Protocol](https://modelcontextprotocol.io) (MCP) server that gives
AI assistants **read-only** access to Snowflake. Ask questions in natural language;
the assistant explores your databases, views and data — without ever modifying
anything.

Works with **Claude Code**, **Claude Desktop**, **GitHub Copilot CLI**, **VS Code**
and any other MCP client, on **Windows, macOS and Linux**.

> Fork of [dynamike/snowflake-mcp-server](https://github.com/dynamike/snowflake-mcp-server)
> by Michael Kania, hardened for cross-platform and corporate (VPN / SSO) use.

---

## Features

- 🔐 **Read-only by design** — only `SELECT`, `WITH`, `SHOW`, `DESCRIBE`, `EXPLAIN`
  and `USE` are accepted; everything else is rejected before it reaches Snowflake.
- 🛡️ **Injection-safe** — object identifiers are validated against an allow-list
  before being used in queries.
- 🔑 **Flexible auth** — external-browser SSO or service-account key-pair.
- 💾 **SSO token caching** — after the first login the token is stored in the OS
  credential store, so reconnects don't reopen a browser.
- 🌐 **Corporate SSL / VPN friendly** — uses the operating system trust store on
  every platform (Windows cert store, macOS Keychain, Linux CA bundle).
- ♻️ **Connection pooling** with automatic background refresh.
- ⚡ **Non-blocking** — queries run off the event loop so the server stays
  responsive.

## Available tools

| Tool | Description | Required args | Optional args |
|------|-------------|---------------|---------------|
| `list_databases` | List accessible databases | — | — |
| `list_views` | List views in a database/schema | `database` | `schema` |
| `describe_view` | Columns + SQL definition of a view | `database`, `view_name` | `schema` |
| `query_view` | Sample rows from a view | `database`, `view_name` | `schema`, `limit` (10) |
| `execute_query` | Run a read-only SQL query | `query` | `database`, `schema`, `limit` (100) |

`execute_query` also supports `SHOW` (TABLES/PIPES/TASKS/STREAMS/GRANTS/…),
`INFORMATION_SCHEMA` and `SNOWFLAKE.ACCOUNT_USAGE` queries. A `LIMIT` is added
automatically to row-returning queries when you don't provide one.

---

## Requirements

- **Python 3.12+**
- **[uv](https://docs.astral.sh/uv/)** (Python package/venv manager)
- **Windows only:** [Microsoft Visual C++ Build Tools](https://visualstudio.microsoft.com/visual-cpp-build-tools/)
  ("Desktop development with C++") for building native dependencies. macOS and
  Linux install from prebuilt wheels with no extra tooling.

<details>
<summary>Installing Python & uv per platform</summary>

```bash
# macOS (Homebrew)
brew install python@3.12 uv

# Linux
curl -LsSf https://astral.sh/uv/install.sh | sh   # installs uv
# use your distro package manager for Python 3.12+

# Windows (PowerShell)
winget install Python.Python.3.12
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

On Windows, after installing Python or the Build Tools, **restart your terminal**
(or the machine) so `PATH` changes take effect.
</details>

## Installation

```bash
# 1. Clone
git clone https://github.com/pedrochans/snowflake-mcp-server.git
cd snowflake-mcp-server

# 2. Create the virtual environment (Python 3.12)
uv venv --python 3.12

# 3. Activate it
source .venv/bin/activate        # macOS / Linux
# .venv\Scripts\activate         # Windows (cmd/PowerShell)

# 4. Install the package
uv pip install -e .
```

Verify: `uv pip list` should show `snowflake-mcp-server`.

## Configuration

Copy the example that matches your authentication method and edit it:

```bash
cp .env.browser.example .env        # external browser (SSO)
# cp .env.private_key.example .env  # service account key-pair
```

### External browser (SSO)

```dotenv
SNOWFLAKE_AUTH_TYPE=external_browser
SNOWFLAKE_ACCOUNT='ORG-ACCOUNT'
SNOWFLAKE_USER='you@company.com'
SNOWFLAKE_WAREHOUSE='YOUR_WH'
SNOWFLAKE_DATABASE='YOUR_DB'
SNOWFLAKE_SCHEMA='YOUR_SCHEMA'
SNOWFLAKE_ROLE='YOUR_ROLE'
SNOWFLAKE_CONN_REFRESH_HOURS=8
```

A browser window opens on first launch to complete the login. The token is then
cached, so you won't be prompted again until it expires.

### Private key (service account)

```dotenv
SNOWFLAKE_AUTH_TYPE=private_key
SNOWFLAKE_ACCOUNT='ORG-ACCOUNT'
SNOWFLAKE_USER='service_account'
SNOWFLAKE_PRIVATE_KEY_PATH=/absolute/path/to/rsa_key.p8
SNOWFLAKE_WAREHOUSE='YOUR_WH'
SNOWFLAKE_DATABASE='YOUR_DB'
SNOWFLAKE_SCHEMA='YOUR_SCHEMA'
SNOWFLAKE_ROLE='YOUR_ROLE'
```

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `SNOWFLAKE_AUTH_TYPE` | yes | `private_key` | `external_browser` or `private_key` |
| `SNOWFLAKE_ACCOUNT` | yes | — | Account identifier (e.g. `ORG-ACCOUNT`) |
| `SNOWFLAKE_USER` | yes | — | Username / email |
| `SNOWFLAKE_PRIVATE_KEY_PATH` | key auth | — | Absolute path to the `.p8` key |
| `SNOWFLAKE_WAREHOUSE` | no | — | Warehouse |
| `SNOWFLAKE_DATABASE` | no | — | Default database |
| `SNOWFLAKE_SCHEMA` | no | — | Default schema |
| `SNOWFLAKE_ROLE` | no | — | Role |
| `SNOWFLAKE_CONN_REFRESH_HOURS` | no | `8` | Hours between connection refreshes |

> Find your account details in Snowsight → Profile → **View Account Details**.
>
> ⚠️ Never commit `.env` — it is already in `.gitignore`.

---

## Connect your client

The server runs the same way for every client:

```bash
uv --directory /ABSOLUTE/PATH/TO/snowflake-mcp-server run snowflake-mcp
```

**Quickstart for Claude Code:**

```bash
claude mcp add snowflake-mcp-server -- \
  uv --directory /ABSOLUTE/PATH/TO/snowflake-mcp-server run snowflake-mcp
claude mcp list      # snowflake-mcp-server ✓ Connected
```

Full, copy-pasteable setup for **Claude Code, Claude Desktop, GitHub Copilot CLI
and VS Code** — plus ready-made config files — is in
**[docs/clients.md](docs/clients.md)** and **[examples/clients/](examples/clients)**.

### Example prompts

- "List all databases in my Snowflake account."
- "List the views in the `FINANCE` database."
- "Describe the `CUSTOMER_ANALYTICS` view in `SALES`."
- "Show me 20 rows from the `REVENUE_BY_REGION` view in `FINANCE`."
- "Run: `SELECT region, SUM(total) FROM SALES.ORDERS GROUP BY region ORDER BY 2 DESC`."
- "Show all tables in the `ETL` schema."

---

## Security

- Enforces read-only statements; identifiers are validated to prevent injection.
- Adds `LIMIT` clauses automatically to cap result sizes.
- Effective permissions are still bounded by the Snowflake **role** you configure —
  grant it the minimum it needs.
- Credentials live only in your local `.env` (or your client's `env` block).

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Client can't find `uv` | Use the absolute path to `uv` (`which uv` / `where uv`) as the `command` in the client config. GUI apps often have a minimal `PATH`. |
| Browser doesn't open on **WSL** | Install `wslu` and `export BROWSER=wslview`, or use private-key auth. |
| Headless Linux server | No browser available — use private-key auth. |
| `pip`/build errors on Windows | Install the Visual C++ Build Tools and restart. |
| Corporate TLS / VPN errors | The server already uses the OS trust store; make sure your corporate root CA is installed there. |
| Repeated SSO popups | Ensure `keyring` is installed (it is, via the `secure-local-storage` extra) so the token can be cached. |

## Development

```bash
uv pip install -e ".[dev]"
pytest            # tests
ruff check .      # lint
ruff format .     # format
mypy snowflake_mcp_server/   # type check
```

Want to add a tool? See `snowflake_mcp_server/utils/template.py` for starter
templates and register the handler in `snowflake_mcp_server/main.py`.

## Tech stack

[snowflake-connector-python](https://docs.snowflake.com/en/developer-guide/python-connector/python-connector) ·
[MCP](https://modelcontextprotocol.io) ·
[Pydantic](https://docs.pydantic.dev/) ·
[sqlglot](https://github.com/tobymao/sqlglot) ·
[pip-system-certs](https://pypi.org/project/pip-system-certs/) ·
[python-dotenv](https://github.com/theskumar/python-dotenv)

## Contributing

Issues and pull requests are welcome. Run the checks above before opening a PR.

## License

[MIT](LICENSE)
