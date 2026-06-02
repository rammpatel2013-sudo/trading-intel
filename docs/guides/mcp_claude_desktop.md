# MCP server ↔ Claude Desktop setup

How to (re-)register the `trading-intel` MCP server with Claude Desktop. Needed
after a Claude Desktop reinstall, which wipes `claude_desktop_config.json`.

## The config

Claude Desktop → **Settings → Developer → Edit Config**. This opens
`%APPDATA%\Claude\claude_desktop_config.json`. Paste (or merge into the existing
`mcpServers` block):

```json
{
  "mcpServers": {
    "trading-intel": {
      "command": "C:\\Users\\drmit\\PycharmProjects\\trading-intel\\.venv\\Scripts\\python.exe",
      "args": ["-m", "trading_intel.mcp.server"],
      "cwd": "C:\\Users\\drmit\\PycharmProjects\\trading-intel"
    }
  }
}
```

Then fully quit and reopen Claude Desktop (tray → Quit, not just close the
window) so it re-reads the config and registers the tools.

Notes:
- We launch the **venv python** with `-m trading_intel.mcp.server` rather than the
  `trading-intel-mcp.exe` shim — avoids PATH surprises and guarantees the right
  interpreter/deps.
- `cwd` is set to the repo root. As of the `config.py` fix that anchors `.env`
  at the repo root via an absolute path, the server finds `.env` regardless of
  cwd — but keeping `cwd` here is harmless belt-and-suspenders.
- **No secrets in this file** (rule 2). All credentials stay in `.env`.

## Verify it works before involving Claude Desktop

Run the server by hand from the repo root — it should start and sit waiting on
STDIO (no traceback). Ctrl-C to stop:

```powershell
cd C:\Users\drmit\PycharmProjects\trading-intel
.venv\Scripts\activate
python -m trading_intel.mcp.server
```

If it exits immediately with a `pydantic ValidationError` about missing fields
(`CONVEX_EMAIL`, `DATABASE_URL`, …), the server can't find `.env` — re-check the
`config.py` absolute-`.env` fix and that `.env` exists at the repo root.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Tools don't appear in Claude Desktop | Config not saved or app not fully restarted (Quit from tray). Check the JSON parses. |
| Server exits with ValidationError on launch | `.env` not found. The `config.py` fix anchors `.env` at the repo root; confirm `.env` is present there. |
| `ModuleNotFoundError: trading_intel` | Wrong interpreter — point `command` at the **venv** `python.exe`, not a system Python. |
| Tool calls error but server starts | Runtime deps (NAS Postgres reachable? Ollama running for `rebuild_am_summary` / `search_knowledge`?). Startup is fine; these are call-time. |
