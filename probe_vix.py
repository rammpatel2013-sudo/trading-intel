from trading_intel.config import get_settings
from trading_intel.clients.convex import ConvexClient
from trading_intel.errors import DataSourceError

c = ConvexClient(get_settings())
for sym in ("VIX", "_VIX", "^VIX", "$VIX", "VIXW", "_VIXW", "VX", "SPX:VIX"):
    try:
        df = c.chain(sym, exps=(1, 2, 3, 4), strike_range=2.0)
        if df.empty:
            print(f"{sym:10s} -> 0 rows (empty)")
        else:
            first_sym = str(df.iloc[0].get("symbol", "")) if "symbol" in df.columns else "n/a"
            print(f"{sym:10s} -> {len(df)} rows, first symbol: {first_sym}")
    except DataSourceError as e:
        print(f"{sym:10s} -> ERROR: {str(e)[:120]}")
    except Exception as e:
        print(f"{sym:10s} -> {type(e).__name__}: {str(e)[:120]}")
