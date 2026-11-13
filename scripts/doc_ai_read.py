"""doc_ai_read.py (2026-11-13) - DSM entry for the morning Doc AI read (run_script.sh doc_ai_read).

Weekdays 08:00 ET: waits for today's Doc report, has the laptop's local AI read it, writes
scripts/cvxmap/data/doc_ai_YYYYMMDD.json, stops by 09:30. The work is in scripts/cvxmap/doc_ai.py.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "cvxmap"))
import doc_ai  # noqa: E402

if __name__ == "__main__":
    sys.exit(doc_ai.main(sys.argv[1:]))
