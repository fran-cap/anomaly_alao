"""Capture scan, driven by the transformer's own edits (I-021's prototype, now G9).

The logic moved to `tools/capture_gate.py` so `tools/corpus_run.py` and the
pytest guard can import it; this stays as the command line front end:

    py -3.12 lab/tools/i021_capture_scan.py <work tree> [<work tree> ...]
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / 'tools'))  # test the checked-out ALAO

from capture_gate import main  # noqa: E402

if __name__ == '__main__':
    raise SystemExit(main())
