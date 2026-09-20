"""I-058: build the two hitch-profiler overlays out of `lab/profiler-hitch`.

Two of them, because listener mode is a different instrument with a different
price tag (~124 us/frame on the live stack) and we want the cheap one available
for a run that only needs the callback-level tail:

    alao-profiler-hitch                 callback level only, WRAP_LISTENERS off
    alao-profiler-hitch-listeners-inv   per subscriber, and invalidate()s the
                                        I-051 dispatch mod's cached arrays after
                                        the listener keys have been swapped

Both go in BESIDE the existing `alao-profiler*` overlays and never touch them:
every locked gen-3 and gen-4 measurement was taken with those, so they are
evidence, not source.

    py -3.12 lab/tools/i058_build_overlays.py [--out <overlays dir>]
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

LAB = Path(__file__).resolve().parent.parent
SRC = LAB / "profiler-hitch"
DEFAULT_OUT = Path(r"C:\code\GIT\anomaly_alao\lab\coord\overlays")

SWITCH_OFF = "local WRAP_LISTENERS  = false"
SWITCH_ON = "local WRAP_LISTENERS  = true"


def build(out_root: Path) -> list:
    src = (SRC / "gamedata" / "scripts" / "zzz_alao_profiler.script").read_text(encoding="utf-8")
    if SWITCH_OFF not in src:
        raise SystemExit(f"! the WRAP_LISTENERS switch moved in {SRC}")
    if "hitch=%s" not in src:
        raise SystemExit("! that is not the hitch build (no hitch= field in the header)")
    meta = (SRC / "meta.ini").read_text(encoding="utf-8")
    made = []
    for name, listeners in (("alao-profiler-hitch", False),
                            ("alao-profiler-hitch-listeners-inv", True)):
        dest = out_root / name
        if dest.exists():
            shutil.rmtree(dest)
        (dest / "gamedata" / "scripts").mkdir(parents=True)
        body = src.replace(SWITCH_OFF, SWITCH_ON, 1) if listeners else src
        (dest / "gamedata" / "scripts" / "zzz_alao_profiler.script").write_text(
            body, encoding="utf-8", newline="\n")
        (dest / "meta.ini").write_text(
            meta.replace("idea I-048",
                         "idea I-048 + I-058 hitch tail"
                         + (", per-listener" if listeners else "")),
            encoding="utf-8", newline="\n")
        made.append((dest, listeners))
        print(f"built {dest}  listeners={'on' if listeners else 'off'}")
    return made


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    a = ap.parse_args(argv)
    for name in ("alao-profiler", "alao-profiler-listeners", "alao-profiler-listeners-inv"):
        p = a.out / name
        if p.exists():
            print(f"(leaving {name} alone - locked measurements were taken with it)")
    build(a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
