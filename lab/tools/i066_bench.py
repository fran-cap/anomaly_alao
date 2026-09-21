"""I-066: what do Stealth Overhaul's MCM reads cost per get_visible_value call,
and what does alao-vmm-cache leave of it?

Two levels, both through the real scripts on the stub engine of
`lab/tests/i066_vmm_harness.py`:

  accessor   one stealth_mcm.get_config(key), stock vs patched
  function   one visual_memory_manager.get_visible_value(...) called by name the
             way the engine does, stock vs patched, for the copy the game loads
             (the ALAO rewrite in the overlay) and the unrewritten live winner

WHAT THIS BENCH CANNOT SEE.  Every engine call here is a Lua closure that bumps a
counter, so the timed number is the LUA side only.  Crossings are COUNTED
(deterministic, lock-independent) and priced at --crossing-us, 0.25 us by
default, the team's pessimistic price for a trivial getter.  `r_string` hashes a
section and a key and allocates a Lua string, so 0.25 is conservative for it.
The stub's own cost (a closure call and an increment) is inside the Lua time of
BOTH arms for the non-MCM crossings and only in the stock arm for the MCM ones,
so it flatters the saving by roughly one closure call per removed crossing; the
'stub floor' row measures that and the table subtracts it.

Protocol (beam-ideas s.2, gen-4 correction): fresh LuaRuntime per arm per mode,
`jit.off()` with no arguments BEFORE any chunk is loaded for the interpreted
arm, a self-check that the interpreter really is slower, `collectgarbage()`
before every timed run, best of 9, N stated, results into _G.__sink.

    py -3.12 lab/tools/i066_bench.py [--iters 200000] [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "tests"))

import i066_vmm_harness as H  # noqa: E402

BEST_OF = 9

DRIVERS = r"""
NPC  = new_obj(11, "stalker", true, 0.25)
NPC2 = new_obj(12, "stalker", true, 0.6)
db.storage[11] = {active_scheme = "walker"}
NPC._sees[0] = true
function drive_get(n, key)
    local g, s = stealth_mcm.get_config, 0
    for i = 1, n do
        -- by name every time, like the callers do
        if stealth_mcm.get_config(key) then s = s + 1 else s = s + 2 end
    end
    _G.__sink = s
end
function drive_vis(n, who_is_actor)
    local who = who_is_actor and ACTOR or NPC2
    local s = 0
    for i = 1, n do
        s = s + engine_call(NPC, who, 0.016, 0.001, 0.4, 0.5, 0, 60 + (i % 7), 30, 2)
    end
    _G.__sink = s
end
-- the price of one stub crossing in THIS harness: closure call + two increments
function drive_floor(n)
    local c = axr_main.config
    local s = 0
    for i = 1, n do if c:line_exist("mcm", "stealth/memory") then s = s + 1 end end
    _G.__sink = s
end
"""


def self_check():
    from lupa import luajit20 as lupa

    def run(on):
        lua = lupa.LuaRuntime()
        if not on:
            lua.execute("if jit then jit.off() end")
        f = lua.eval("function(n) local s=0 for i=1,n do s=s+i*0.5 end return s end")
        best = 1e9
        for _ in range(5):
            lua.execute("collectgarbage()")
            t = time.perf_counter()
            f(3_000_000)
            best = min(best, time.perf_counter() - t)
        return best

    return run(True), run(False)


def timed(lua, fn, iters, *args):
    f = lua.globals()[fn]
    f(2000, *args)                                   # warm-up (fills the cache too)
    best = 1e9
    for _ in range(BEST_OF):
        lua.execute("collectgarbage()")
        t = time.perf_counter()
        f(iters, *args)
        best = min(best, time.perf_counter() - t)
    g = lua.globals()
    g["__cross"], g["__cross_mcm"] = 0, 0
    f(1000, *args)
    return best / iters * 1e6, float(g["__cross"]) / 1000.0, float(g["__cross_mcm"]) / 1000.0


def runtime(vmm_src, patched, jit_on):
    lua = H.build(vmm_src, patched=patched, jit=jit_on)
    # the install / fill lines are printf into a Lua table; keep them out of the loop
    lua.execute("function printf() end")
    lua.execute(DRIVERS)
    return lua


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--iters", type=int, default=200_000)
    ap.add_argument("--crossing-us", type=float, default=0.25)
    ap.add_argument("--calls-per-frame", type=float, default=1.68,
                    help="get_visible_value calls per frame standing still on gammabaseline (I-062)")
    ap.add_argument("--json")
    a = ap.parse_args(argv)

    if not H.STEALTH_MCM.is_file():
        print("needs the extracted GAMMA corpus", file=sys.stderr)
        return 2

    on, off = self_check()
    ratio = off / on
    print("JIT self-check: compiled %.2f ms, interpreted %.2f ms (%.1fx) -- %s"
          % (on * 1e3, off * 1e3, ratio, "OK" if ratio > 3 else "SUSPECT, jit.off did not take"))
    out = {"iters": a.iters, "best_of": BEST_OF, "crossing_us": a.crossing_us,
           "calls_per_frame": a.calls_per_frame,
           "self_check": {"jit_ms": on * 1e3, "interp_ms": off * 1e3, "ratio": ratio}, "rows": []}

    floor = {}
    for mode, jit_on in (("JIT on", True), ("JIT off", False)):
        lua = runtime(None, False, jit_on)
        floor[mode], _, _ = timed(lua, "drive_floor", a.iters)
    print("stub floor (one counted stub crossing, Lua time): JIT on %.4f us, JIT off %.4f us"
          % (floor["JIT on"], floor["JIT off"]))
    out["stub_floor_us"] = floor

    print("\n### accessor: one stealth_mcm.get_config(key)\n")
    print("| key | mode | arm | lua us | crossings | lua minus stubs | + engine @%.2f | total us | saved |"
          % a.crossing_us)
    print("|---|---|---|---:|---:|---:|---:|---:|---:|")
    for key in ("memory", "debugx"):
        for mode, jit_on in (("JIT on", True), ("JIT off", False)):
            base = None
            for arm, patched in (("stock", False), ("patched", True)):
                lua = runtime(None, patched, jit_on)
                us, cross, _ = timed(lua, "drive_get", a.iters, key)
                net = us - cross * floor[mode]
                tot = net + cross * a.crossing_us
                base = tot if base is None else base
                print("| %s | %s | %s | %.4f | %.0f | %.4f | %.4f | %.4f | %.4f |"
                      % (key, mode, arm, us, cross, net, cross * a.crossing_us, tot, base - tot))
                out["rows"].append({"level": "accessor", "key": key, "mode": mode, "arm": arm,
                                    "lua_us": us, "crossings": cross, "lua_net_us": net,
                                    "total_us": tot, "saved_us": base - tot})

    print("\n### function: one get_visible_value, stalker NPC evaluating `who`, standing, not crouched\n")
    print("| vmm copy | who | mode | arm | lua us | crossings (mcm) | lua minus stubs | total us @%.2f | saved/call | saved/frame @%.2f calls |"
          % (a.crossing_us, a.calls_per_frame))
    print("|---|---|---|---|---:|---:|---:|---:|---:|---:|")
    for copy in ("overlay-alao", "live-atmospherics"):
        path = H.VMM_COPIES[copy]
        if not path.is_file():
            print("| %s | missing | | | | | | | | |" % copy)
            continue
        src = H.read_script(path)
        for who, is_actor in (("actor", True), ("npc", False)):
            for mode, jit_on in (("JIT on", True), ("JIT off", False)):
                base = None
                for arm, patched in (("stock", False), ("patched", True)):
                    lua = runtime(src, patched, jit_on)
                    us, cross, mcm = timed(lua, "drive_vis", a.iters // 4, is_actor)
                    net = us - cross * floor[mode]
                    tot = net + cross * a.crossing_us
                    base = tot if base is None else base
                    print("| %s | %s | %s | %s | %.3f | %.0f (%.0f) | %.3f | %.3f | %.3f | %.2f |"
                          % (copy, who, mode, arm, us, cross, mcm, net, tot, base - tot,
                             (base - tot) * a.calls_per_frame))
                    out["rows"].append({"level": "function", "copy": copy, "who": who, "mode": mode,
                                        "arm": arm, "lua_us": us, "crossings": cross,
                                        "crossings_mcm": mcm, "lua_net_us": net, "total_us": tot,
                                        "saved_us_per_call": base - tot,
                                        "saved_us_per_frame": (base - tot) * a.calls_per_frame})

    print("\nN = %d reads / %d calls per run, warm-up 2000, best of %d, collectgarbage() before each "
          "run, fresh LuaRuntime per arm per mode." % (a.iters, a.iters // 4, BEST_OF))
    print("'crossings' are counted, not timed. 'lua minus stubs' removes the harness's own "
          "stub cost (crossings x stub floor) so a removed crossing is not credited twice.")
    if a.json:
        Path(a.json).write_text(json.dumps(out, indent=1), encoding="utf-8")
        print("wrote %s" % a.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
