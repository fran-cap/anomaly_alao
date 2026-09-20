"""I-062: build the two walkout-profiler overlays out of `lab/profiler-walkout`.

    alao-profiler-walkout                 callback level + the bnd/eng/evt axes
                                          + the frame recorder. WRAP_LISTENERS off,
                                          so it is the cheap one.
    alao-profiler-walkout-listeners-inv   the same plus per-subscriber attribution,
                                          `invalidate()` on the I-051 dispatch mod
                                          after the listener keys are swapped, and
                                          I-063's per-call trace of the inventory
                                          open - because the user gets ONE attended
                                          session and both questions have to fit in it.

Both go in BESIDE every existing `alao-profiler*` overlay and this script
refuses to write any of them: gen-3, gen-4 and gen-5 measurements were all taken
with those, so they are evidence, not source.

    py -3.12 lab/tools/i062_build_overlays.py [--out <overlays dir>]
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

LAB = Path(__file__).resolve().parent.parent
SRC = LAB / "profiler-walkout"
DEFAULT_OUT = Path(r"C:\code\GIT\anomaly_alao\lab\coord\overlays")

LISTENERS_OFF = "local WRAP_LISTENERS  = false"
LISTENERS_ON = "local WRAP_LISTENERS  = true"
TRACE_OFF = "local TRACE_LISTENERS   = nil"
TRACE_ON = ('local TRACE_LISTENERS   = '
            '{"ActorMenu_on_before_init_mode#ui_inventory.script"}')

# Never written by this script.
PROTECTED = ("alao-profiler", "alao-profiler-listeners", "alao-profiler-listeners-inv",
             "alao-profiler-hitch", "alao-profiler-hitch-listeners-inv",
             "alao-profiler-hitch-trace-inv")

BUILDS = (
    ("alao-profiler-walkout", False, False),
    ("alao-profiler-walkout-listeners-inv", True, True),
)


def compiles(src: str) -> bool:
    from lupa import luajit20 as lupa
    rt = lupa.LuaRuntime()
    return bool(rt.eval("function(s) return loadstring(s) ~= nil end")(src))


def build(out_root: Path) -> list:
    src = (SRC / "gamedata" / "scripts" / "zzz_alao_profiler.script").read_text(encoding="utf-8")
    for needle in (LISTENERS_OFF, TRACE_OFF):
        if src.count(needle) != 1:
            raise SystemExit(f"! switch moved or duplicated in {SRC}: {needle!r}")
    for marker in ("ALAOPROF|1|frm|", "ALAOPROF|1|wdr|", "ALAOPROF|1|trace|"):
        if marker not in src:
            raise SystemExit(f"! that is not the walkout build (no {marker} emitter)")
    meta = (SRC / "meta.ini").read_text(encoding="utf-8")

    made = []
    for name, listeners, trace in BUILDS:
        if name in PROTECTED:
            raise SystemExit(f"! {name} is a locked overlay; refusing to write it")
        body = src
        if listeners:
            body = body.replace(LISTENERS_OFF, LISTENERS_ON, 1)
        if trace:
            body = body.replace(TRACE_OFF, TRACE_ON, 1)
        if not compiles(body):
            raise SystemExit(f"! the patched profiler for {name} does not compile under LuaJIT 2.0")
        dest = out_root / name
        if dest.exists():
            shutil.rmtree(dest)
        (dest / "gamedata" / "scripts").mkdir(parents=True)
        (dest / "gamedata" / "scripts" / "zzz_alao_profiler.script").write_text(
            body, encoding="utf-8", newline="\n")
        (dest / "meta.ini").write_text(
            meta.replace("idea I-048",
                         "idea I-048 + I-058 hitch tail + I-062 walkout (binder/global/"
                         "time-event axes, per-frame recorder)"
                         + (", per-listener" if listeners else "")
                         + (" + I-063 inventory trace" if trace else "")),
            encoding="utf-8", newline="\n")
        made.append((dest, listeners, trace))
        print(f"built {dest}  listeners={'on' if listeners else 'off'}  "
              f"trace={'on' if trace else 'off'}")
    return made


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    a = ap.parse_args(argv)
    for name in PROTECTED:
        if (a.out / name).exists():
            print(f"(leaving {name} alone - locked measurements were taken with it)")
    build(a.out)
    print("\nread it back with: py -3.12 lab/tools/profile_report.py --queue <id> "
          "--frames --hitch --listeners --trace")
    return 0


if __name__ == "__main__":
    sys.exit(main())
