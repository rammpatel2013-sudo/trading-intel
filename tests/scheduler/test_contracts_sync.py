"""contracts_sync + symbol_map_sync (2026-11-19, DB wave A2 step 6): parse the optionsdata.ai contract records."""

import csv
import gzip
from datetime import date

from trading_intel.scheduler.jobs import contracts_sync as J
from trading_intel.scheduler.jobs import symbol_map_sync as S

ROW = {"record": "contract", "symbol": "NVDA  261120C00002500", "iid": "1226125", "underlying_iid": "3754",
       "polygon": "O:NVDA261120C00002500", "dxfeed": ".NVDA261120C2.5", "settlement": "pm",
       "exercise": "american", "multiplier": "100.0", "first_seen": "2026-10-12", "root": "NVDA"}


def test_contract_rows_keep_contract_records_only():
    rows, ui = J.contract_rows(
        [ROW, dict(ROW, record="quote"), dict(ROW, symbol="junk"), dict(ROW)], "NVDA", date(2026, 11, 6)
    )
    assert ui == 3754 and len(rows) == 1
    r = rows[0]
    assert r["occ"] == "NVDA  261120C00002500" and r["expiry"] == date(2026, 11, 20) and r["cp"] == "C"
    assert r["strike"] == 2.5 and r["od_iid"] == 1226125 and r["dxfeed"] == ".NVDA261120C2.5"
    assert r["first_seen"] == date(2026, 10, 12) and r["last_seen"] == date(2026, 11, 6)
    # first_seen missing -> the file day
    rows, _ = J.contract_rows([dict(ROW, first_seen="")], "NVDA", date(2026, 11, 6))
    assert rows[0]["first_seen"] == date(2026, 11, 6)


def test_files_newest_day_and_all(tmp_path):
    for d in ("20261105", "20261106"):
        (tmp_path / d).mkdir()
        for name in ("NVDA.csv.gz", "NVDA_underlying.csv.gz", "api_calls.csv.gz"):
            with gzip.open(tmp_path / d / name, "wt", newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=list(ROW))
                w.writeheader()
                w.writerow(ROW)
    (tmp_path / "NVDA_20261106").mkdir()                       # laptop partial layout: ignored
    newest = list(J.files(tmp_path, None, False))
    assert [(str(d), s) for d, s, _ in newest] == [("2026-11-06", "NVDA")]
    assert len(list(J.files(tmp_path, None, True))) == 2
    assert [str(d) for d, _, _ in J.files(tmp_path, "20261105", False)] == ["2026-11-05"]


def test_symbol_map_rows():
    rows = S.rows_for({"SPX": {"is_etf": False, "name": None}, "QQQ": {"is_etf": True, "name": "Invesco"},
                       "BRK-B": {"is_etf": None, "name": None}})
    by = {r["symbol"]: r for r in rows}
    assert by["SPX"]["asset_type"] == "index" and by["SPX"]["cvforge"] == "I:SPX"
    assert by["QQQ"]["asset_type"] == "etf" and by["QQQ"]["name"] == "Invesco"
    assert by["BRK-B"]["finra"] == "BRK/B" and "od_iid" not in by["BRK-B"]       # never overwrites od_iid
