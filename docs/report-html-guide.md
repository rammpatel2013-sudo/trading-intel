# How to build a trading-intel HTML report

Canonical pattern for the self-contained HTML reports that live in `reports/`
(e.g. `ORCL_flow_2026-07-16.html`, `ORCL_full_analysis_2026-07-16.html`,
`DVN_tas_flow_report.html`). Start from **`reports/_report_template.html`** — it
is a working skeleton (renders with placeholder data) carrying the CSS + the
inline-SVG chart engine.

---

## Two report families

| | **Cowork live artifact** | **Standalone browser report** |
|---|---|---|
| Made with | `create_artifact` / `update_artifact` | Python script → `.html`, or written directly |
| Theme | **light mode (mandatory)** | dark house style (`#0f1216` bg) or light |
| Data | `window.cowork.callMcpTool(...)` on open, re-pulls on Reload | baked in at generation time |
| Examples | `orcl-flow-intelligence` artifact | `DVN_tas_flow_report`, `credit_income_*`, `eod_vol_*` |

The **structure and chart engine are identical**; only the palette and the data
bootstrap differ. The template ships light; a commented dark palette is at the
bottom of its `<style>` for standalone reports.

---

## Hard rules

1. **Self-contained.** Inline all CSS and JS. No external `<script>`/`<link>`, no CDN, images as `data:` URLs.
2. **Inline SVG for charts, not Chart.js.** Chart.js from CDN *silently fails to paint inside Cowork artifacts* (SRI/`display:none`-at-init resize bug). The template's `barChartSVG` / `donutSVG` have no dependencies and always render. This was the fix when "all tiles were empty."
3. **Light mode for artifacts.** Keep `:root{color-scheme:light}`, light bg, dark text — the artifact renders inside Cowork's light UI.
4. **One DATA object.** Drive KPIs and charts from a single object so the static and live variants differ by *only* the bootstrap line.
5. **FlashAlpha rule 4.** Any flow/Greeks/positioning report must say "Descriptive …, not a signal." Personal stock verdicts add "not investment advice."
6. **Sources block** at the bottom (primary source links + the MCP tool used).

## Shared skeleton (top → bottom)

Header (`kicker` + `h1` with `.tkr` + `byline` = as-of date · source · generated ts)
→ optional **banner** (`.info` verdict / `.ok` health-good / `.warn` gap-found)
→ **KPI grid** (`#kpis`, 4-up, `.val` colored red/green/amber/slate)
→ **content sections** (prose + `.chips` + `ul.tight`, and/or tables, and/or the 2×2 chart grid)
→ **callout/bridge** tying the data to the thesis
→ **disclaimer** (rule 4) → **Sources**.

## Chart engine API

```
money(n)                       "$5.00M" / "$217k" / "-$3.55M"
pct(n, d)                      "61.4%"
barChartSVG(items, total)      items = [{label, value, color}]  → vertical bars, value + % labels
donutSVG(a, b, labelA, labelB) two-arc donut via stroke-dasharray (center = b's %)
```
Palette: bull `#16a34a`/`#4ade80`, bear `#dc2626`/`#f97316`; size ramp `#cbd5e1→#60a5fa→#1e3a8a`; DTE ramp `#fde68a→#fbbf24→#f97316→#b91c1c`.

## Live-artifact bootstrap

```js
const r = await window.cowork.callMcpTool('mcp__trading-intel__get_flow_intelligence', {symbol:'ORCL'});
let d = r.structuredContent ?? JSON.parse(r.content[0].text);   // probe shape in chat first
render(d);
```
Wrap in try/catch → show an error line. Show a spinner until data lands. Don't
init charts inside a `display:none` container (SVG is injected via `innerHTML`,
so this is a non-issue here — but keep the dash visible when you populate it).

## Build → verify → ship

1. Write the file from the template; swap in real content + DATA.
2. **Verify in node** before shipping: buckets reconcile to the stated total; `donut` dasharray sums to the circumference; every chart has the expected bar count; `money`/`pct` outputs look right. (See the verify snippet used for the ORCL build.)
3. Save as `reports/<SYM>_<what>_<YYYY-MM-DD>.html`.
4. Standalone → `present_files`. Live → `create_artifact` (kebab id) or `update_artifact`; list the MCP tools in `mcp_tools`.

## Checklist

- [ ] Self-contained (no CDN), light mode if it's an artifact
- [ ] Charts are inline SVG, render with zero dependencies
- [ ] KPIs + charts driven by one DATA object
- [ ] Numbers reconcile (verified in node)
- [ ] Rule-4 disclaimer + Sources present
- [ ] Saved to `reports/` with dated filename, then presented / registered

_Last updated: 2026-07-16._
