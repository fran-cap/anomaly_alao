"""I-049 microbench: 353 per-binder actor_on_update closures vs one shared walker.

Protocol is `lab/docs/beam-ideas.md` section 2, same as tools/microbench.py:
fresh LuaRuntime per (arm, mode), `jit.off(chunk, true)` for the interpreted
mode (a global jit.off does not touch loaded chunks), `collectgarbage('collect')`
immediately before every timed run, a warm-up call, best of 9, `_G.__sink` so
nothing is dead-code eliminated, and a self-check that the interpreted arm
really is much slower than the compiled one.

What is timed: one whole `make_callback("actor_on_update")` pass through the
LIVE dispatch path -- the real `hspairs` min-heap out of the live
`_g_patches.script` and the real `make_callback` out of the live
`axr_main.script` -- with K anomaly listeners plus `--others` unrelated no-op
listeners on the same callback (the save profiled in gen-3 had ~425
`actor_on_update` listeners in total, ~353 of them anomalies).

Steady state is "nothing is due": every binder's `on_update_time` is pushed
past the end of the run, because that is what a binder does on all but ~1 frame
in 21 (a 100 ms throttle at 210 fps) and it is exactly the work the patch
removes. The ~12 bodies per frame that ARE due run identical code in both arms
and are deliberately not in the measurement.

What the engine stub hides: `time_global()` is a Lua function returning an
upvalue, where the real one is a C closure into the engine. The original arm
calls it K times per frame and the patched arm once, so a real `time_global()`
being MORE expensive than the stub makes this bench an under-estimate of the
saving, not an over-estimate. Nothing else in the timed region touches the
engine.

    py -3.12 lab/tools/i049_bench.py --sweep 50,150,353,700 [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import i049_model as model  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "coord"))
import i049_drx_da_patch as patcher  # noqa: E402

import lupa.luajit20 as luajit  # noqa: E402

REPS = 9
WARMUP_CALLS = 2
FRAMES = 2000          # timed frames per run
JITTER_LIMIT = 1.15    # median/best above this = busy machine


HARNESS = """
-- every binder parked far in the future: the throttle-check steady state
function setup(k, others, far)
  for i = 1, others do
    local f = function() _G.__others = (_G.__others or 0) + 1 end
    RegisterScriptCallback("actor_on_update", f)
  end
  for i = 1, k do
    local b = spawn_binder("anom_" .. i, i, far)
    b.on_update_time = far
  end
end

function bench(frames)
  local mk = make_callback
  local t0 = os.clock()
  for f = 1, frames do
    CLOCK = CLOCK + 16
    mk("actor_on_update")
  end
  local t1 = os.clock()
  _G.__sink = (_G.__others or 0) + TRACE_N
  return t1 - t0
end
"""


def _arm_src(patched_text, k, others):
    return model.arm_lua(patched_text) + HARNESS


def time_arm(src: str, k: int, others: int, mode: str, reps: int,
             frames: int) -> dict:
    rt = luajit.LuaRuntime(unpack_returned_tuples=False)
    loaded = rt.eval("loadstring")(src, "i049")
    f = loaded[0] if isinstance(loaded, tuple) else loaded
    if f is None:
        raise RuntimeError(f"LuaJIT refused the chunk: {loaded}")
    if mode == "jit_off":
        rt.eval("jit.off")(f, True)
    f()
    rt.eval("setup")(k, others, 10 ** 9)
    bench = rt.eval("bench")
    collect = rt.eval("function() collectgarbage('collect') end")

    for _ in range(WARMUP_CALLS):
        bench(50)

    runs = []
    for _ in range(reps):
        collect()
        t0 = time.perf_counter()
        bench(frames)
        runs.append(time.perf_counter() - t0)
    assert int(rt.eval("TRACE_N")) == 0, "a body fired; the steady state is wrong"
    return {
        "best_s": min(runs),
        "median_s": statistics.median(runs),
        "runs": runs,
        "jit": bool(rt.eval("jit.status")()),
        "listeners": int(rt.eval('listener_count("actor_on_update")')),
    }


SELF_CHECK = """
local D = {}
for i = 1, 64 do D[i] = i * 0.5 end
function spin(n)
  local s = 0
  for i = 1, n do s = s + D[(i % 64) + 1] * 1.000001 end
  _G.__sink = s
  return s
end
"""


def self_check() -> float:
    out = {}
    for mode in ("jit_on", "jit_off"):
        rt = luajit.LuaRuntime(unpack_returned_tuples=False)
        loaded = rt.eval("loadstring")(SELF_CHECK, "selfcheck")
        f = loaded[0] if isinstance(loaded, tuple) else loaded
        if mode == "jit_off":
            rt.eval("jit.off")(f, True)
        f()
        spin = rt.eval("spin")
        spin(10_000)
        best = min(_timeit(spin, 2_000_000) for _ in range(5))
        out[mode] = best
    return out["jit_off"] / out["jit_on"]


def _timeit(fn, *a):
    t0 = time.perf_counter()
    fn(*a)
    return time.perf_counter() - t0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sweep", default="50,150,353,700")
    ap.add_argument("--others", type=int, default=72,
                    help="non-anomaly actor_on_update listeners (425 - 353)")
    ap.add_argument("--frames", type=int, default=FRAMES)
    ap.add_argument("--reps", type=int, default=REPS)
    ap.add_argument("--json", type=Path)
    a = ap.parse_args()

    original = model._read(model.DRX)
    patched = patcher.patch(original)
    ratio = self_check()
    print(f"self-check: interpreted / compiled = {ratio:.1f}x "
          f"({'ok' if ratio > 8 else 'SUSPECT - jit.off may not have taken'})")

    rows, out = [], {"self_check_ratio": ratio, "others": a.others,
                     "frames": a.frames, "reps": a.reps, "cases": []}
    for k in [int(x) for x in a.sweep.split(",")]:
        case = {"k": k}
        for mode in ("jit_on", "jit_off"):
            arm_a = time_arm(_arm_src(None, k, a.others), k, a.others, mode,
                             a.reps, a.frames)
            arm_b = time_arm(_arm_src(patched, k, a.others), k, a.others, mode,
                             a.reps, a.frames)
            us_a = arm_a["best_s"] / a.frames * 1e6
            us_b = arm_b["best_s"] / a.frames * 1e6
            jitter = max(arm_a["median_s"] / arm_a["best_s"],
                         arm_b["median_s"] / arm_b["best_s"])
            case[mode] = {
                "us_per_frame_original": us_a,
                "us_per_frame_patched": us_b,
                "us_saved_per_frame": us_a - us_b,
                "speedup": us_a / us_b if us_b else float("inf"),
                "jitter": jitter,
                "listeners_original": arm_a["listeners"],
                "listeners_patched": arm_b["listeners"],
                "arm_original": arm_a, "arm_patched": arm_b,
            }
            rows.append((k, mode, us_a, us_b, us_a - us_b, us_a / us_b, jitter))
        out["cases"].append(case)

    print()
    print(f"| K | mode | original us/frame | patched us/frame | saved us/frame | x | jitter |")
    print(f"|---:|---|---:|---:|---:|---:|---:|")
    for k, mode, ua, ub, saved, sp, j in rows:
        warn = " !" if j > JITTER_LIMIT else ""
        print(f"| {k} | {mode} | {ua:.2f} | {ub:.2f} | {saved:.2f} | {sp:.1f}x | {j:.2f}{warn} |")
    print()
    print(f"N = {a.frames} dispatch passes per timed run, warm-up {WARMUP_CALLS}x50, "
          f"best of {a.reps}, collectgarbage before each, fresh LuaRuntime per arm/mode, "
          f"{a.others} unrelated listeners on the same callback.")

    if a.json:
        a.json.write_text(json.dumps(out, indent=2), encoding="utf-8")
        print(f"wrote {a.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
