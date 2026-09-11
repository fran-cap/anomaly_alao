#!/usr/bin/env python
"""Follow the newest Anomaly engine log, like ``tail -f``.

    py -3.12 tools/tail_xray_log.py            # follow the newest xray_*.log
    py -3.12 tools/tail_xray_log.py --errors   # only warnings, errors, crashes
    py -3.12 tools/tail_xray_log.py FILE       # follow a specific file

Switches to a newer log automatically when the engine starts a fresh one, so it
can be left running across a restart of the game.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "framework"))

from aalo import config as _config  # noqa: E402
from aalo import xraylog as _xraylog  # noqa: E402

INTERESTING = ("!", "~")


def interesting(line: str) -> bool:
    s = line.lstrip()
    return s.startswith(INTERESTING) or "FATAL ERROR" in line.upper() or "stack trace" in line.lower()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="follow the newest Anomaly log")
    ap.add_argument("file", nargs="?", help="log file (default: newest in the logs dir)")
    ap.add_argument("--errors", action="store_true", help="only warnings and errors")
    ap.add_argument("--lines", type=int, default=20, help="backlog lines to print first")
    ap.add_argument("--poll", type=float, default=0.5, help="poll interval in seconds")
    args = ap.parse_args(argv)

    cfg = _config.get()
    path = Path(args.file) if args.file else _xraylog.newest_log(cfg=cfg)
    if path is None:
        print(f"no log found in {cfg.logs_dir}", file=sys.stderr)
        print("the engine writes one only after the game has run at least once", file=sys.stderr)
        return 2

    print(f"--- following {path} ---", file=sys.stderr)
    fh = open(path, "r", encoding="utf-8", errors="replace")
    backlog = fh.readlines()[-args.lines :]
    for line in backlog:
        if not args.errors or interesting(line):
            sys.stdout.write(line)
    sys.stdout.flush()

    try:
        while True:
            line = fh.readline()
            if line:
                if not args.errors or interesting(line):
                    sys.stdout.write(line)
                    sys.stdout.flush()
                continue
            time.sleep(args.poll)
            newest = _xraylog.newest_log(cfg=cfg) if not args.file else None
            if newest and newest != path:
                print(f"\n--- switching to {newest} ---", file=sys.stderr)
                fh.close()
                path = newest
                fh = open(path, "r", encoding="utf-8", errors="replace")
    except KeyboardInterrupt:
        return 0
    finally:
        fh.close()


if __name__ == "__main__":
    raise SystemExit(main())
