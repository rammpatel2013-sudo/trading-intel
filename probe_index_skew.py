import traceback
from datetime import date
from trading_intel.config import get_settings
from trading_intel.memory.db import make_session_factory
from trading_intel.clients.cboe import CboeClient
from trading_intel.scheduler.jobs.index_skew import build_row, _upsert

s = get_settings()
f = make_session_factory(s)
cboe = CboeClient()

try:
    with f() as sess:
        print("session opened")
        record = build_row(sess, cboe, as_of=date.today())
        print("build_row returned:", record)
        if record:
            _upsert(sess, record)
            sess.commit()
            print("committed")
except Exception as e:
    print("EXCEPTION:", type(e).__name__, e)
    traceback.print_exc()
