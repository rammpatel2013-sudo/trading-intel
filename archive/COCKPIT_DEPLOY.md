# Live Dealer-Positioning Cockpit — deploy (Convex-fed DB reader)

A phone-friendly SPX + SPY cockpit that reads the **Convex-fed snapshot tables**
the scheduler already fills — through the same MCP read functions
(`get_gamma_history`, `get_gex_term`, `get_straddle`, `get_skew_history`,
`get_intraday_flow`). It adds **zero vendor calls**, so the Convex 10/min cap
stays reserved for the regime engine (rule 1). CVForge is historical and is not
used. Reachable from your phone over **Tailscale** to the NAS.

> **Freshness:** near-live = as fresh as the scheduler's snapshot cadence
> (minutes during RTH), *not* tick-live. True tick-live would need Convex
> headroom you don't have under the 10/min cap — a plan decision, not code.

## Files

```
trading_intel/api/
  app.py                 FastAPI: /positioning/{symbol}, /healthz, /  (opens a DB session per request, 20s cache)
  positioning.py         DB reader → cockpit JSON (validated on real captured samples)
  static/cockpit.html    live mobile page (SPX/SPY toggle, 30s refresh, pending states)
trading_intel/greeks/
  delta_flip.py          zero-DEX / delta-flip descriptor (already in the repo)
```

Everything on the page is a DB read — regime + gamma flip, Net GEX/DEX,
GEX-by-DTE (buckets sum to the headline), expected move, skew — from
`greeks_snapshots` + the rolling term/straddle/skew tables. Two cells wait on a
scheduler tweak (below): the **delta flip** and the **flow cards**.

## 1 · Dependency add

`fastapi` and `uvicorn` aren't in the stack yet. Add to `pyproject.toml`
`[project].dependencies`, then `pip install -e .` (laptop) / rebuild (NAS):

```toml
"fastapi>=0.111",
"uvicorn[standard]>=0.30",
```

## 2 · Two scheduler tweaks that light up the last two cells

**(a) Persist the delta flip** (so `dex.flip` isn't `pending`). The cockpit can't
compute it — no live chain in DB-reader mode — so `greeks_snapshot` computes it
during its Convex pull, where the chain exists:

1. In `clients/convex.py` and `clients/cvforge.py`, inside `exposures()`, right
   after `result["gex_flip"] = ...`:
   ```python
   from trading_intel.greeks.delta_flip import dex_flip
   result["dex_flip"] = dex_flip(df, spot)
   ```
2. Alembic migration: add a nullable `dex_flip` float column to `greeks_snapshots`.
3. In `scheduler/jobs/greeks_snapshot.py`, add `dex_flip=exposures.get("dex_flip")`
   to the insert; add `dex_flip` to the `get_gamma_history` row projection.

**(b) Re-enable the index flow** (so the Put/Call + Δ-notional cards fill). In
`.env`:
```
INTRADAY_SYMBOLS=SPX,SPY
```
and make sure the `intraday_flow` job is scheduled (DSM task, 5-min RTH). Cost:
~0.4 Convex calls/min for the two symbols — comfortably under the 10/min cap.
Until this runs, the cockpit shows those two cards as **pending** (it will not
fake or reuse stale June data).

## 3 · Run it (on the NAS — it needs the Postgres the scheduler writes)

```bash
# laptop smoke test:
uvicorn trading_intel.api.app:app --host 0.0.0.0 --port 8600
# http://localhost:8600  ·  /positioning/SPX  ·  /healthz
```

NAS docker service (add to `docker-compose.yml`; sudo on the DS923+):
```yaml
  cockpit:
    build: .
    container_name: trading_intel_cockpit
    env_file: .env
    command: uvicorn trading_intel.api.app:app --host 0.0.0.0 --port 8600
    ports: ["8600:8600"]
    restart: unless-stopped
```
```bash
sudo docker compose build cockpit && sudo docker compose up -d cockpit
curl -s localhost:8600/healthz
```

## 4 · Phone access (Tailscale)

NAS is on your Tailnet; the service binds `0.0.0.0:8600`:
```
http://<nas-magicdns-name>:8600/     e.g. http://mithil-nas:8600/
```
Optional HTTPS + clean URL: `tailscale serve https / http://localhost:8600`.

## Notes

- 20s server cache → many phone refreshes cost only DB reads, no vendor calls at all.
- Bug found in passing: `dashboard/gamma_regime_data.latest_spx_gamma_regime`
  reads `oi_chain_eod`, which excludes SPX (`CHAIN_EXCLUDE_ROOTS`) → returns None.
  Separate Streamlit fix; the cockpit's `get_gamma_history` path is unaffected.
