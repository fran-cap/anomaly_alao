#!/usr/bin/env python
"""Report whether a PresentMon CLI is available for real frame capture.

    py -3.12 tools/presentmon_check.py

Without PresentMon the framework still runs: it falls back to a psutil sampler
that records CPU% and RSS at 1 Hz and reports ``fps_avg = null``.  Frame pacing
metrics need PresentMon.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "framework"))

from aalo import metrics as _metrics  # noqa: E402

INSTALL_HINT = """\
PresentMon was not found.

  Install the Intel build:      winget install Intel.PresentMon
  or download the CLI:          https://github.com/GameTechDev/PresentMon/releases

Then either put PresentMon.exe on PATH, or point the PRESENTMON environment
variable at the executable.  Capturing frame data needs an elevated shell, the
same as launching G.A.M.M.A. through Mod Organizer 2.
"""


def probe_version(exe: Path) -> str:
    for flag in ("--version", "-version", "/?"):
        try:
            out = subprocess.run(
                [str(exe), flag], capture_output=True, text=True, timeout=20
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        text = (out.stdout or out.stderr or "").strip()
        if text:
            return text.splitlines()[0]
    return "version unknown"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="locate the PresentMon CLI")
    ap.add_argument("--quiet", action="store_true", help="print the path only")
    args = ap.parse_args(argv)

    exe = _metrics.find_presentmon()
    if exe is None:
        if not args.quiet:
            print(INSTALL_HINT, file=sys.stderr)
        return 1
    if args.quiet:
        print(exe)
        return 0
    print(f"found   {exe}")
    print(f"version {probe_version(exe)}")
    print("backend presentmon (per-frame data available)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
