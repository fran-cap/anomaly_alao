"""I-063: build ONE extra hitch-profiler overlay that traces the inventory open.

    alao-profiler-hitch-trace-inv   WRAP_LISTENERS on, invalidate()s the I-051
                                    dispatch mod, and TRACE_LISTENERS pointed at
                                    ActorMenu_on_before_init_mode#ui_inventory.script

Why a third overlay: the hitch build keeps max / first / a log2 histogram per
listener, which answers "is this a hitch" and cannot answer "why is this call
19 ms and that one 4".  `ui_inventory.script:93` is bimodal at 4-20 ms with no
reliable relationship to being the first open (run 20260920-171714-I-063-ef2deb:
baseline capture 1 max 5.19 with the first open at 5.19; baseline capture 3 max
19.26 with the first open at only 8.30), so the summary has to be replaced by a
per-call log line with covariates.

It is a SEPARATE overlay on purpose.  `alao-profiler`, `alao-profiler-listeners*`
carry every locked gen-3/gen-4 measurement and `alao-profiler-hitch*` carry the
gen-5/gen-6 hitch numbers; this script writes neither, and unlike
`i058_build_overlays.py` it never rebuilds them.

    py -3.12 lab/tools/i063_build_trace_overlay.py [--out <overlays dir>]
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

LAB = Path(__file__).resolve().parent.parent
SRC = LAB / "profiler-hitch"
DEFAULT_OUT = Path(r"C:\code\GIT\anomaly_alao\lab\coord\overlays")
NAME = "alao-profiler-hitch-trace-inv"

LISTENERS_OFF = "local WRAP_LISTENERS  = false"
LISTENERS_ON = "local WRAP_LISTENERS  = true"
TRACE_OFF = "local TRACE_LISTENERS   = nil"
TRACE_ON = ('local TRACE_LISTENERS   = '
            '{"ActorMenu_on_before_init_mode#ui_inventory.script"}')

# The overlays this script must never write.
PROTECTED = ("alao-profiler", "alao-profiler-listeners", "alao-profiler-listeners-inv",
             "alao-profiler-hitch", "alao-profiler-hitch-listeners-inv")


def compiles(src: str) -> bool:
    from lupa import luajit20 as lupa
    rt = lupa.LuaRuntime()
    return bool(rt.eval("function(s) return loadstring(s) ~= nil end")(src))


def build(out_root: Path) -> Path:
    assert NAME not in PROTECTED, "the new overlay must not shadow a locked one"
    src = (SRC / "gamedata" / "scripts" / "zzz_alao_profiler.script").read_text(encoding="utf-8")
    for needle in (LISTENERS_OFF, TRACE_OFF):
        if src.count(needle) != 1:
            raise SystemExit(f"! switch moved or duplicated in {SRC}: {needle!r}")
    if "hitch=%s" not in src:
        raise SystemExit("! that is not the hitch build (no hitch= field in the header)")
    if "ALAOPROF|1|trace|" not in src:
        raise SystemExit("! the source has no trace emitter; nothing to build")

    body = src.replace(LISTENERS_OFF, LISTENERS_ON, 1).replace(TRACE_OFF, TRACE_ON, 1)
    if not compiles(body):
        raise SystemExit("! the patched profiler does not compile under LuaJIT 2.0")

    dest = out_root / NAME
    if dest.exists():
        shutil.rmtree(dest)
    (dest / "gamedata" / "scripts").mkdir(parents=True)
    (dest / "gamedata" / "scripts" / "zzz_alao_profiler.script").write_text(
        body, encoding="utf-8", newline="\n")
    meta = (SRC / "meta.ini").read_text(encoding="utf-8")
    (dest / "meta.ini").write_text(
        meta.replace("idea I-048",
                     "idea I-048 + I-058 hitch tail, per-listener, "
                     "+ I-063 per-call trace of the inventory open"),
        encoding="utf-8", newline="\n")
    return dest


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    a = ap.parse_args(argv)
    for name in PROTECTED:
        if (a.out / name).exists():
            print(f"(leaving {name} alone - measurements were taken with it)")
    dest = build(a.out)
    print(f"built {dest}  listeners=on  trace=ActorMenu_on_before_init_mode#ui_inventory.script")
    print("read the lines back with: "
          r'Select-String -Path <run>\xray.log -Pattern "ALAOPROF\|1\|trace\|"')
    return 0


if __name__ == "__main__":
    sys.exit(main())
