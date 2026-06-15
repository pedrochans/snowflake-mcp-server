# Client configuration

This MCP server speaks **stdio**, so it works with any MCP-compatible client.
Every client runs the same underlying command:

```
uv --directory /ABSOLUTE/PATH/TO/snowflake-mcp-server run snowflake-mcp
```

(`snowflake-mcp-stdio` is an equivalent alias.)

Ready-to-edit config files live in [`examples/clients/`](../examples/clients),
organised per platform:

```
examples/clients/
├── macos/      claude-code.mcp.json · copilot-mcp-config.json · vscode-mcp.json
├── linux/      claude-code.mcp.json · copilot-mcp-config.json · vscode-mcp.json
├── windows/    claude-code.mcp.json · copilot-mcp-config.json · vscode-mcp.json
└── wsl/        claude-code.mcp.json · copilot-mcp-config.json · vscode-mcp.json
```

In every file, replace `USER` and `path/to/snowflake-mcp-server` with your real
absolute path, and create your `.env` first (see the main [README](../README.md)).

> On first launch a browser window opens for external-browser authentication.
> After the first successful login the SSO token is cached in the OS credential
> store (Keychain / Windows Credential Manager / libsecret), so subsequent
> launches and the periodic refresh do **not** reopen the browser.

---

## What differs between platforms

Only two things change across platforms — the rest of the JSON is identical:

| | `command` | `--directory` |
|---|---|---|
| **macOS** | `uv` (CLIs) / `/opt/homebrew/bin/uv` (GUI) | `/Users/USER/...` |
| **Linux** | `uv` (CLIs) / `/home/USER/.local/bin/uv` (GUI) | `/home/USER/...` |
| **Windows** | `uv` (CLIs) / `C:\\Users\\USER\\.local\\bin\\uv.exe` (GUI) | `C:/Users/USER/...` |
| **WSL** | `uv` (CLIs, inside WSL) / `wsl.exe` (VS Code on Windows host) | `/home/USER/...` |

Why two `command` values per OS:

- **CLI clients** (Claude Code, Copilot CLI) run in your terminal and inherit
  your shell `PATH`, so plain `uv` works.
- **GUI clients** (VS Code, Claude Desktop) launch with a minimal `PATH` and
  often can't find `uv` — use the **absolute path** to the `uv` binary. Find it
  with `which uv` (macOS/Linux/WSL), `where uv` (Windows cmd) or
  `(Get-Command uv).Source` (PowerShell).

---

## Per-client notes

### Claude Code (CLI)

One command (local scope):

```bash
claude mcp add snowflake-mcp-server -- \
  uv --directory /ABSOLUTE/PATH/TO/snowflake-mcp-server run snowflake-mcp
```

Project scope (writes a shareable `.mcp.json` at the repo root):

```bash
claude mcp add --scope project snowflake-mcp-server -- \
  uv --directory /ABSOLUTE/PATH/TO/snowflake-mcp-server run snowflake-mcp
```

Or copy the matching `claude-code.mcp.json` to your repo root as `.mcp.json`.
Verify: `claude mcp list` → `✓ Connected`. Inside a session, `/mcp`.

Key/field: top-level `mcpServers`, `"type": "stdio"`.

### GitHub Copilot CLI

Run `/mcp add` in the CLI (Server Type → **Local**, Tools → `*`), or edit
`~/.copilot/mcp-config.json` using the matching `copilot-mcp-config.json`.
Verify with `/mcp show`. Relocate the config dir with `COPILOT_HOME`.

Key/fields: top-level `mcpServers`, **`"type": "local"`** and **`"tools": ["*"]`**.

### VS Code (GitHub Copilot, Agent mode)

`Ctrl/Cmd + Shift + P` → **MCP: Open User Configuration**, then paste the
matching `vscode-mcp.json`. Click **Start** / **Restart**; when it reports
*"Discovered 5 tools"* enable it in **Agent mode → Configure Tools**.

Key/field: top-level **`servers`** (not `mcpServers`), `"type": "stdio"`.

---

## WSL specifics

There are two distinct setups:

1. **Everything inside WSL** (you open the CLI, or VS Code via the *Remote - WSL*
   extension, from within the WSL distro). Treat it exactly like Linux: plain
   `uv`, Linux paths (`/home/USER/...`). Use the `wsl/claude-code.mcp.json` and
   `wsl/copilot-mcp-config.json` examples.

2. **VS Code running on the Windows host**, targeting a server installed in WSL.
   The `uv` binary lives inside WSL, so launch it through `wsl.exe` — see
   [`examples/clients/wsl/vscode-mcp.json`](../examples/clients/wsl/vscode-mcp.json):

   ```json
   {
     "servers": {
       "snowflake-mcp-server": {
         "type": "stdio",
         "command": "wsl.exe",
         "args": ["-d", "Ubuntu", "-e", "bash", "-lic",
                  "uv --directory /home/USER/path/to/snowflake-mcp-server run snowflake-mcp"]
       }
     },
     "inputs": []
   }
   ```

   - `-d Ubuntu`: your distro name (`wsl -l -q` to list).
   - `bash -lic "..."`: a login+interactive shell so `uv` is on `PATH`.
   - Adjust to your setup; the exact wrapper can vary between machines.

Browser auth under WSL needs a browser to open: install `wslu` and
`export BROWSER=wslview` so the Windows browser handles the SSO callback. On a
headless server use private-key auth instead.

---

## Cross-platform notes

- **Absolute paths**: always use an absolute path for `--directory`.
- **Windows JSON paths**: forward slashes (`C:/Users/you/...`) or escaped
  backslashes (`C:\\Users\\you\\...`).
- **Credentials**: `.env` in the project directory is the default. Alternatively
  pass `SNOWFLAKE_*` in an `"env": { ... }` block inside the client config
  (supported by Claude Code, Claude Desktop and Copilot CLI).
