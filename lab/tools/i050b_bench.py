"""I-050b: how much of the zzz_player_injuries frame is Lua, and how much is the
boundary?

The in-game profiler says the listener costs ~74 us/frame.  That number is
Lua work + the cost of ~104 Lua->C crossings.  Offline we can measure the first
term directly (LuaJIT 2.0 under lupa, engine globals replaced by trivial Lua
stubs) and then read the second off by subtraction.  What the bench CANNOT see
is the real cost of a crossing -- a stub call is a Lua call, the real thing
marshals through luabind into C++ -- so the engine term is derived, not
measured, and it is the only place the arithmetic leans on the in-game number.

Protocol (beam-ideas.md section 2): fresh LuaRuntime per arm per mode, jit off
before the chunk is loaded for the interpreted arm (jit.off after the fact does
not touch loaded chunks), a self-check that the interpreter really is much
slower, collectgarbage() before every timed run, best-of-9, N frames per run.

    py -3.12 lab/tools/i050b_bench.py [--iters 2000] [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import i050b_injuries_env as env   # noqa: E402

BEST_OF = 9


def _self_check(jit_on: bool):
    """The 'is the interpreter really off' guard from the beam protocol."""
    from lupa import luajit20 as lupa

    def run(on):
        lua = lupa.LuaRuntime()
        if not on:
            lua.execute("if jit then jit.off(true, true) end")
        f = lua.eval(
            "function(n) local s=0 for i=1,n do s=s+i*0.5 end return s end"
        )
        import time
        best = 1e9
        for _ in range(5):
            lua.execute("collectgarbage()")
            t = time.perf_counter()
            f(3_000_000)
            best = min(best, time.perf_counter() - t)
        return best

    return run(True), run(False)


def measure(path: Path, frames: int, jit_on: bool) -> float:
    """Best-of-9 seconds for `frames` steady-state actor_on_update calls."""
    import time

    lua, M = env.make_runtime(path, jit_off=not jit_on)
    env.boot(lua, M)
    lua.globals()["__trace_on"] = False        # keep the trace out of the timing
    driver = lua.eval(
        """
        function(M, n)
          local f = M.actor_on_update
          for i = 1, n do
            _G.__tg = _G.__tg + 16
            _G.__gametime = _G.__gametime + 0.11
            f()
          end
        end
        """
    )
    driver(M, 200)                              # warm-up (and let the JIT settle)
    best = 1e9
    for _ in range(BEST_OF):
        lua.execute("collectgarbage()")
        t = time.perf_counter()
        driver(M, frames)
        best = min(best, time.perf_counter() - t)
    return best


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--iters", type=int, default=20000, help="frames per timed run")
    ap.add_argument("--json")
    ap.add_argument("--patched", help="patched file (default: build one in a temp dir)")
    a = ap.parse_args(argv)

    if a.patched:
        patched = Path(a.patched)
    else:
        import tempfile
        import i050b_injuries_patch as patcher
        patched = Path(tempfile.mkdtemp(prefix="i050b")) / "zzz_player_injuries.script"
        patcher.main(["patch", str(patched)])

    on, off = _self_check(True)
    ratio = off / on
    print(f"JIT self-check: compiled {on*1e3:.2f} ms, interpreted {off*1e3:.2f} ms "
          f"({ratio:.1f}x) -- {'OK' if ratio > 5 else 'SUSPECT, jit.off did not take'}")

    rows = {}
    for mode, jit_on in (("JIT on", True), ("JIT off", False)):
        base = measure(env.ALAO_LIVE, a.iters, jit_on)
        new = measure(patched, a.iters, jit_on)
        rows[mode] = {
            "baseline_us_per_frame": base / a.iters * 1e6,
            "patched_us_per_frame": new / a.iters * 1e6,
        }

    print(f"\nN = {a.iters} frames/run, best of {BEST_OF}, collectgarbage() before each,")
    print("fresh LuaRuntime per arm per mode, engine globals stubbed in Lua.\n")
    print(f"{'mode':<9} {'baseline us/frame':>18} {'patched us/frame':>18} {'saved':>9}")
    for mode, r in rows.items():
        d = r["baseline_us_per_frame"] - r["patched_us_per_frame"]
        print(f"{mode:<9} {r['baseline_us_per_frame']:>18.3f} "
              f"{r['patched_us_per_frame']:>18.3f} {d:>9.3f}")

    out = {
        "iters": a.iters, "best_of": BEST_OF,
        "jit_self_check_ratio": ratio,
        "modes": rows,
        "crossings": {},
    }
    for label, p in (("baseline", env.ALAO_LIVE), ("patched", patched)):
        lua, M = env.make_runtime(p)
        env.boot(lua, M)
        n, counts, _ = env.frame_profile(lua, 1)
        out["crossings"][label] = {"total": n, "by_name": counts}
    print(f"\nLua->C crossings/frame: baseline {out['crossings']['baseline']['total']}, "
          f"patched {out['crossings']['patched']['total']}")
    if a.json:
        Path(a.json).write_text(json.dumps(out, indent=2), encoding="utf-8")
        print(f"wrote {a.json}")
    return out


if __name__ == "__main__":
    main()
