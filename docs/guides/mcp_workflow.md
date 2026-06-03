# Daily MCP workflow (Claude Desktop)

How to use the `trading-intel` MCP tools day to day, and how to add tickers.

## Where to ask

Ask in plain English in a normal Claude Desktop chat — the tools load there.
They also work inside Cowork. The tools are **read-only** (FlashAlpha rule 4):
there is deliberately no write tool, so mutating the watchlist is a CLI command,
not a chat request.

## Things you can just type

- "What's the latest AM report?" / "AM report for 2026-05-23" / "what report dates do you have?"
- "Show the watchlist regime" / "which names are short gamma right now?"
- "Options flow tilt across the watchlist" / "what's call-heavy today?"
- "NVDA gamma history over the last 10 days"
- "NVDA technicals and any candlestick patterns"
- "Make an HTML chart for NVDA" (writes a file under `reports/`, returns the path)
- "Search my playbooks for gamma flip hedging"

Name one or a few tickers + a short window to keep responses small (cheaper on context).
The tools are local/free; only the chat itself counts toward usage.

## Adding a ticker to the watchlist

Run once in PowerShell on the machine that can reach the NAS:

```powershell
cd C:\Users\drmit\PycharmProjects\trading-intel
.venv\Scripts\activate
python scripts/add_watchlist.py MRVL --rationale "watching"
python scripts/add_watchlist.py --list        # confirm
python scripts/add_watchlist.py MRVL --deactivate   # remove later
```

This writes an active `watchlist_entries` row; `watchlist.effective_symbols`
unions it with the static `.env` `WATCHLIST`. No NAS image rebuild needed — it's
data, read at runtime.

### When the data shows up

The NAS collectors pick the new name up on their next scheduled runs:

| Data | Tool | Available after |
|---|---|---|
| Flow tilt / notional | `get_watchlist_flow` | next RTH session (intraday flow jobs) |
| GEX / flip / regime | `get_watchlist_regime`, `get_gamma_history` | next 06:45 ET `greeks_snapshot` + `chain_snapshot` |
| OHLC / RSI / candlesticks | `get_technicals`, `render_report_html` | next 16:45 ET `quotes_daily` |
| Multi-day gamma trend, ΔGEX 1wk | `get_gamma_history` | builds over the following days |

Practical loop: **add today → ask tomorrow morning** and the name behaves like
any other watchlist symbol.

### Easier-than-CLI options (not built yet)

- A text box on the Research Watchlist Streamlit page (page 5) to add/deactivate from the dashboard UI.
- A Windows `.bat`/shortcut wrapping the script so it's a double-click.
- A scheduled re-ingest already auto-adds names from uploaded research PDFs.

## Related

- Time & sales (the tape) is already available via `ConvexClient.time_and_sales`
  (`/api/data/tas`); `probe_convex_tas.py` dumps the available per-trade fields.
- Setup / reinstall recovery: `docs/guides/mcp_claude_desktop.md`.
