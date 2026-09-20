"""I-050a microbench: the Lua half of checkLedgeGrabbing, original vs patched.

Protocol (beam-ideas.md section 2): fresh LuaRuntime per arm per mode, JIT on
AND off (jit.off() before anything is loaded, because jit.off(true,true) does
not reach chunks that are already loaded), a self-check that the interpreter
really is much slower, collectgarbage() before every timed run, 2 warm-up runs,
best-of-9, N frames per run stated below.

WHAT THIS CAN SEE: the Lua-side work - the guard, the vector algebra, the table
and object allocations, the per-step geometry_ray construction, the loop.
WHAT IT CANNOT SEE: the engine. ray_pick():query(), device(), vector() and
:position() are Lua tables here and C calls in the game, and the ray query is
almost certainly the biggest single item in the real 142 us. So the numbers
below are a LOWER bound on what the patch saves, and the honest figure for the
headline is the profiler's own 142 us/frame, not this.

NOT RUN CLEANLY YET. The one run taken during the I-050a session is void: its
self-check failed (jit.off() instead of jit.off(true,true)) and agent-I052 held
the corpus lock at the end of it. The bug is fixed below; the run is not
repeated because the lock was still held. Check `coord.py board` before AND
after, and refuse the numbers if either check shows a game or corpus lock.

    py -3.12 lab/tools/i050a_bench.py [--frames 200] [--best-of 9] [--json out]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "tests"))
sys.path.insert(0, str(HERE.parent / "coord"))

from lupa import luajit20 as lupa  # noqa: E402

import i050a_harness as H  # noqa: E402
import i050a_ledge_patch as P  # noqa: E402

ALAO = P.ALAO if P.ALAO.is_file() else P.LIVE

# Two scenes, both driven entirely inside Lua so the Python<->Lua boundary is
# not what we are timing.
#   frozen: the gammabaseline scene - standing still on flat ground.
#   moving: walking forward, the camera moving every frame.
DRIVER = r"""
function(mode, frames)
    local ns = demonized_ledge_grabbing
    local f = ns.checkLedgeGrabbing
    local S = STATE
    if mode == "frozen" then
        S.cam:set(0, 1.70, 0)
        S.dir:set(0, 0, 1)
        S.actor:set(0, 0, 0)
        for i = 1, frames do
            S.tg = 1000 + i * 16
            f()
        end
    else
        for i = 1, frames do
            local z = i * 0.01
            S.cam:set(0, 1.70 + 0.002 * (i % 3), z)
            S.dir:set(0.01 * (i % 5), 0, 1)
            S.actor:set(0, 0, z)
            S.tg = 1000 + i * 16
            f()
        end
    end
end
"""

SELFCHECK = r"""
function(n)
    local s = 0
    for i = 1, n do s = s + i * 0.5 end
    return s
end
"""


class Bench:
    def __init__(self, script: Path, jit_on: bool):
        self.lua = lupa.LuaRuntime(unpack_returned_tuples=True)
        if not jit_on:
            # jit.off() with no arguments turns the JIT off for the CALLING
            # function only - the first run of this bench used it and the
            # self-check duly reported 5.6x instead of ~20x. jit.off(true,true)
            # is the global form, and it has to come before anything is loaded
            # because it does not reach chunks that already exist.
            self.lua.execute("jit.off(true, true)")
        self.lua.execute(H.PRELUDE)
        load = self.lua.eval(H.LOADER)
        load(H.MCM.read_text(encoding="utf-8"), "demonized_ledge_grabbing_mcm")
        ns = load(script.read_text(encoding="utf-8") + H.PROBE, "demonized_ledge_grabbing")
        state = self.lua.globals().STATE
        defaults = ns.load_defaults()
        for k in defaults:
            state.mcm[k] = defaults[k]
        ns.load_settings()
        ns.actor_on_first_update()
        # logging would dominate; turn it into a no-op for the timed runs
        self.lua.execute("function log_entry() end")
        self.lua.execute("_G.log = function() end")
        self.driver = self.lua.eval(DRIVER)
        self.selfcheck = self.lua.eval(SELFCHECK)

    def run(self, mode, frames):
        self.lua.execute("collectgarbage()")
        t0 = time.perf_counter()
        self.driver(mode, frames)
        return time.perf_counter() - t0

    def check(self, n=2_000_000):
        self.lua.execute("collectgarbage()")
        t0 = time.perf_counter()
        self.selfcheck(n)
        return time.perf_counter() - t0


def best_of(script, jit_on, mode, frames, k, warmup=2):
    b = Bench(script, jit_on)
    for _ in range(warmup):
        b.run(mode, frames)
    times = [b.run(mode, frames) for _ in range(k)]
    return min(times), b


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames", type=int, default=200)
    ap.add_argument("--best-of", type=int, default=9, dest="k")
    ap.add_argument("--json", type=Path)
    a = ap.parse_args(argv)

    patched = Path(
        __import__("tempfile").mkdtemp(prefix="i050a-")) / "patched.script"
    patched.write_bytes(
        P.patch(ALAO.read_bytes().decode("utf-8")).encode("utf-8"))

    # protocol self-check: the interpreted runtime must really be interpreted
    on = Bench(ALAO, True).check()
    off = Bench(ALAO, False).check()
    ratio = off / on if on else 0.0
    print(f"self-check: JIT-off / JIT-on on a scalar loop = {ratio:.1f}x "
          f"({'OK' if ratio > 8 else 'SUSPECT - jit.off may not have taken'})")

    rows = []
    for mode in ("frozen", "moving"):
        for jit_on in (True, False):
            t_o, _ = best_of(ALAO, jit_on, mode, a.frames, a.k)
            t_p, _ = best_of(patched, jit_on, mode, a.frames, a.k)
            us_o = t_o / a.frames * 1e6
            us_p = t_p / a.frames * 1e6
            rows.append(dict(scene=mode, jit="on" if jit_on else "off",
                             orig_us=us_o, patched_us=us_p,
                             saved_us=us_o - us_p,
                             speedup=(us_o / us_p) if us_p else None))

    print()
    print("| scene  | JIT | original us/frame | patched us/frame | saved | x |")
    print("|--------|-----|------------------:|-----------------:|------:|--:|")
    for r in rows:
        print(f"| {r['scene']:6s} | {r['jit']:3s} | {r['orig_us']:17.2f} | "
              f"{r['patched_us']:16.2f} | {r['saved_us']:5.2f} | {r['speedup']:.2f} |")
    print()
    print(f"N = {a.frames} frames/run, best of {a.k}, 2 warm-up runs, "
          f"collectgarbage() before each run, fresh LuaRuntime per arm/mode.")
    print("Stubbed engine: these are LUA-SIDE costs only and are a lower bound "
          "on the in-game saving (see the module docstring).")

    if a.json:
        a.json.write_text(json.dumps(
            dict(frames=a.frames, best_of=a.k, selfcheck_ratio=ratio, rows=rows),
            indent=2), encoding="utf-8")
        print(f"wrote {a.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
