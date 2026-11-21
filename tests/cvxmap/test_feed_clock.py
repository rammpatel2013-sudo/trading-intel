"""One map clock (2026-11-21, Mithil Q3 "both 16:00"): the live feed solves 0DTE IV to the 16:00 cash close - the clock
the map's greeks already ran to - and says so in the payload (iv_end), so the cockpit can re-express older 16:15 logs."""
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "cvxmap"))
import cvx_live as cl  # noqa: E402


def test_feed_iv_clock_is_the_1600_close():
    assert cl.EXPIRY_MIN == 16 * 60


def test_minutes_to_close_runs_to_1600():
    assert cl.minutes_to_close(dt.datetime(2026, 11, 20, 15, 0)) == 60
    assert cl.minutes_to_close(dt.datetime(2026, 11, 20, 16, 5)) == 1      # floored at one minute
    assert abs(cl.t_iv(dt.datetime(2026, 11, 20, 13, 0)) - 180 / 525600.0) < 1e-15


def test_payload_names_the_iv_clock():
    src = (Path(cl.__file__)).read_text(encoding="utf-8")
    assert '"iv_end": EXPIRY_MIN' in src
