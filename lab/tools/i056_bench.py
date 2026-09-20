"""I-056: what does one option read cost on the Lua side, and what would a
cache buy?

Four arms, all driven through the real accessor chain the game uses
(`ui_options.get` / `ui_mcm.get` -> `axr_main.config:r_value`):

  live      GAMMA's live class (`new_ini_file_ex` from the loose
            `_g_patches.script`): NO cache, `line_exist` + `r_string` on every
            read, whatever the value is.
  vanilla   the `_g.script` class I-056 is about: memoizes into
            `self.cache[s.."&"..k]`, tests the hit with `if (cache_result)`, so
            a cached `false` re-reads and an absent key is never cached.
  fixed     the same class with the sentinel fix (cache a `false`/`nil` box and
            test the box, not the value).
  cached    a perfect Lua-side memo: one table lookup, no crossing at all.

WHAT THIS BENCH CANNOT SEE. `line_exist` and `r_string` are luabind calls into
the engine's CInifile.  Here they are Lua closures over a Lua table, so the
measured delta is the LUA side only and the crossing price is an ASSUMPTION.
Per I-050b (beam-ideas s.11, "crossings are not equally priced") the pessimistic
read for a trivial getter is ~0.25 us; `r_string` is heavier than a trivial
getter - it hashes a section and a key and hands back a freshly allocated Lua
string - but 0.25 us is the number the team agreed to price crossings at when
the alternative is guessing upward, so 2 crossings = ~0.5 us is the
CONSERVATIVE-FOR-THE-IDEA figure and `--crossing-us` sweeps it.

Protocol (beam-ideas s.2, with the gen-4 correction): fresh LuaRuntime per arm
per mode, `jit.off()` with NO arguments before the chunk is loaded for the
interpreted arm, a self-check that the interpreter really is much slower,
`collectgarbage()` before every timed run, best of 9.

    py -3.12 lab/tools/i056_bench.py [--iters 200000] [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

BEST_OF = 9

# The engine side, stubbed.  `__cross` is bumped once per simulated crossing so
# the arithmetic can price them separately from the Lua time.
PRELUDE = r"""
__cross = 0
local DATA = {
  ["mcm&stealth/icon"]                        = "false",
  ["mcm&body_health_system/TEXT_BASED_PATCH"] = "false",
  ["mcm&alife/general/excl_dist"]             = "75",
}
-- stand-in for CInifile::line_exist / r_string coming back through luabind
local function line_exist(s, k)
  __cross = __cross + 1
  return DATA[s .. "&" .. k] ~= nil
end
local function r_string(s, k)
  __cross = __cross + 1
  local v = DATA[s .. "&" .. k]
  -- the engine hands back a fresh Lua string every call
  return v and (v .. "")
end
_G.__line_exist, _G.__r_string = line_exist, r_string
"""

# --- arm bodies: each defines cfg:r_value(s,k,typ,def) ---------------------

LIVE = r"""
-- _g_patches.script new_ini_file_ex:r_value, verbatim shape
cfg = {}
function cfg:r_value(s, k, typ, def)
  if not __line_exist(s, k) then return def end
  local v = __r_string(s, k)
  if v == nil then
    if typ == 1 then return false else return def end
  end
  if typ == 1 then
    return v == "true" or v == "1" or v == "yes" or v == "on"
  elseif typ == 2 then
    return tonumber(v) or def
  end
  return v
end
"""

VANILLA = r"""
-- _g.script ini_file_ex:r_value, verbatim shape (the I-056 defect)
cfg = { cache = {} }
function cfg:r_value(s, k, typ, def)
  local cache_result = self.cache[s .. "&" .. k]
  if (cache_result) then return cache_result end      -- <-- false/nil miss here
  if not __line_exist(s, k) then return def end
  local v = __r_string(s, k)
  if (typ == 1) then
    v = v == nil and def or v == "true" or false
  elseif (typ == 2) then
    v = tonumber(v) or def
  end
  self.cache[s .. "&" .. k] = v
  return v == nil and def or v
end
"""

FIXED = r"""
-- the I-056 fix: box the cached value so false/nil are real hits
local NEG = {}
cfg = { cache = {} }
function cfg:r_value(s, k, typ, def)
  local key = s .. "&" .. k
  local box = self.cache[key]
  if box ~= nil then
    if box == NEG then return def end
    return box[1]
  end
  if not __line_exist(s, k) then
    self.cache[key] = NEG
    return def
  end
  local v = __r_string(s, k)
  if (typ == 1) then
    v = v == nil and def or v == "true" or v == "1" or false
  elseif (typ == 2) then
    v = tonumber(v) or def
  end
  self.cache[key] = { v }
  return v
end
"""

CACHED = r"""
-- ceiling: a perfect Lua-side memo, no crossing, no boxing
cfg = { cache = {} }
function cfg:r_value(s, k, typ, def)
  local v = self.cache[s .. "&" .. k]
  if v ~= nil then return v end
  return def
end
"""

# ui_options.get / ui_mcm.get, trimmed to what runs on the hot path.
ACCESSOR = r"""
local opt_val = {
  ["stealth/icon"] = 1,
  ["body_health_system/TEXT_BASED_PATCH"] = 1,
  ["control/general/aim_toggle"] = 1,
}
function ui_get(id)
  local value = cfg:r_value("mcm", id, opt_val[id])
  if value ~= nil then return value end
  return nil
end
function driver(n, id)
  local s = 0
  for i = 1, n do
    if ui_get(id) then s = s + 1 else s = s + 2 end
  end
  _G.__sink = s
  return s
end
"""

ARMS = (("live", LIVE), ("vanilla", VANILLA), ("fixed", FIXED), ("cached", CACHED))

# The three keys actually read every frame on the live stack.
KEYS = (
    ("stealth/icon", "false-valued (light_gem, 1/frame)"),
    ("body_health_system/TEXT_BASED_PATCH", "false-valued (injuries, 1/frame)"),
    ("control/general/aim_toggle", "ABSENT key (fluid_aim, 1/frame)"),
)


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


def measure(body, key, iters, jit_on):
    from lupa import luajit20 as lupa
    lua = lupa.LuaRuntime()
    if not jit_on:
        lua.execute("if jit then jit.off() end")
    lua.execute(PRELUDE)
    lua.execute(body)
    lua.execute(ACCESSOR)
    driver = lua.globals()["driver"]
    driver(1000, key)                      # warm-up
    best = 1e9
    for _ in range(BEST_OF):
        lua.execute("collectgarbage()")
        t = time.perf_counter()
        driver(iters, key)
        best = min(best, time.perf_counter() - t)
    lua.globals()["__cross"] = 0
    driver(1000, key)
    crossings = float(lua.globals()["__cross"]) / 1000.0
    return best / iters * 1e6, crossings     # us per read, crossings per read


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--iters", type=int, default=300_000)
    ap.add_argument("--crossing-us", type=float, default=0.25,
                    help="assumed price of ONE Lua->C crossing (I-050b pessimistic read)")
    ap.add_argument("--json")
    a = ap.parse_args(argv)

    on, off = self_check()
    ratio = off / on
    print("JIT self-check: compiled %.2f ms, interpreted %.2f ms (%.1fx) -- %s"
          % (on * 1e3, off * 1e3, ratio,
             "OK" if ratio > 3 else "SUSPECT, jit.off did not take"))

    out = {"iters": a.iters, "best_of": BEST_OF, "crossing_us": a.crossing_us,
           "self_check": {"jit_ms": on * 1e3, "interp_ms": off * 1e3, "ratio": ratio},
           "rows": []}

    for key, what in KEYS:
        print("\n== %s  [%s]" % (key, what))
        print("%-9s %14s %14s %10s %14s %14s"
              % ("arm", "lua us/read", "crossings", "+engine", "total us/read",
                 "saved vs live"))
        live_tot = {}
        for mode, jit_on in (("JIT on", True), ("JIT off", False)):
            print("  -- %s --" % mode)
            for name, body in ARMS:
                lua_us, cross = measure(body, key, a.iters, jit_on)
                eng = cross * a.crossing_us
                tot = lua_us + eng
                if name == "live":
                    live_tot[mode] = tot
                print("%-9s %14.4f %14.2f %10.4f %14.4f %14.4f"
                      % (name, lua_us, cross, eng, tot, live_tot[mode] - tot))
                out["rows"].append({
                    "key": key, "class": what, "mode": mode, "arm": name,
                    "lua_us_per_read": lua_us, "crossings_per_read": cross,
                    "engine_us": eng, "total_us_per_read": tot,
                    "saved_vs_live_us": live_tot[mode] - tot,
                })

    print("\nN = %d reads/run, best of %d, collectgarbage() before each, fresh "
          "LuaRuntime per arm per mode." % (a.iters, BEST_OF))
    print("'crossings' is counted, not timed; '+engine' prices each at %.2f us "
          "(assumption, see the module docstring)." % a.crossing_us)

    if a.json:
        Path(a.json).write_text(json.dumps(out, indent=1), encoding="utf-8")
        print("wrote %s" % a.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
