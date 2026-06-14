# Client configuration

This MCP server speaks **stdio**, so it works with any MCP-compatible client.
Every client runs the same underlying command:

```
uv --directory /ABSOLUTE/PATH/TO/snowflake-mcp-server run snowflake-mcp
```

(`snowflake-mcp-stdio` is an equivalent alias.)

Ready-to-copy config files live in [`examples/clients/`](../examples/clients).
Replace `/ABSOLUTE/PATH/TO/snowflake-mcp-server` with the real absolute path on
your machine, and make sure you have created your `.env` first (see the main
[README](../README.md)).

> On first launch a browser window opens for external-browser authentication.
> After the first successful login the SSO token is cached in the OS credential
> store (Keychain / Windows Credential Manager / libsecret), so subsequent
> launches and the periodic refresh do **not** reopen the browser.

---

## Claude Code (CLI)

One command (local scope, only for you):

```bash
claude mcp add snowflake-mcp-server -- \
  uv --directory /ABSOLUTE/PATH/TO/snowflake-mcp-server run snowflake-mcp
```

Project scope (writes a `.mcp.json` that can be shared with the team):

```bash
claude mcp add --scope project snowflake-mcp-server -- \
  uv --directory /ABSOLUTE/PATH/TO/snowflake-mcp-server run snowflake-mcp
```

Or create `.mcp.json` at the repo root manually — see
[`examples/clients/claude-code.mcp.json`](../examples/clients/claude-code.mcp.json).

Verify:

```bash
claude mcp list      # should show: snowflake-mcp-server ✓ Connected
```
Inside a session, `/mcp` lists the server and its tools.

---

## Claude Desktop

Edit `claude_desktop_config.json` and add the `mcpServers` entry from
[`examples/clients/claude_desktop_config.json`](../examples/clients/claude_desktop_config.json),
then restart Claude Desktop.

Config file location:

| OS | Path |
|----|------|
| macOS | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Windows | `%APPDATA%\Claude\claude_desktop_config.json` |
| Linux | `~/.config/Claude/claude_desktop_config.json` |

---

## GitHub Copilot CLI

Either run `/mcp add` inside the CLI and fill the form (Server Type → **Local**,
Tools → `*`), or edit `~/.copilot/mcp-config.json` directly using
[`examples/clients/copilot-mcp-config.json`](../examples/clients/copilot-mcp-config.json).

Note the Copilot-specific fields: `"type": "local"` and `"tools": ["*"]`.

Verify with `/mcp show` (or `/mcp show snowflake-mcp-server`) inside the CLI.

The config dir can be relocated with the `COPILOT_HOME` environment variable.

---

## VS Code (GitHub Copilot, Agent mode)

`Ctrl/Cmd + Shift + P` → **MCP: Open User Configuration**, then add the entry
from [`examples/clients/vscode-mcp.json`](../examples/clients/vscode-mcp.json).

Note VS Code uses the `servers` key (not `mcpServers`). Click **Start** /
**Restart** on the server; when it reports *"Discovered 5 tools"* it is ready.
Enable it in **Agent mode → Configure Tools**.

---

## Cross-platform notes

- **Absolute paths**: always use an absolute path for `--directory`. The cwd a
  client launches the server with is not guaranteed.
- **`uv` on PATH**: the configs assume `uv` is on PATH. If a GUI client cannot
  find it (common on macOS/Windows where GUI apps have a minimal PATH), replace
  `"command": "uv"` with the absolute path to the `uv` binary
  (`which uv` / `where uv`).
- **Windows JSON paths**: use forward slashes (`C:/Users/you/...`) or escaped
  backslashes (`C:\\Users\\you\\...`).
- **Credentials**: `.env` in the project directory is the default. Alternatively
  pass the `SNOWFLAKE_*` variables in an `"env": { ... }` block inside the
  client config (supported by Claude Code, Claude Desktop and Copilot CLI).
- **WSL / headless Linux**: external-browser auth needs a browser to open. On
  WSL install `wslu` and export `BROWSER=wslview` so the Windows browser opens
  for the SSO callback; on a headless server use private-key auth instead.
