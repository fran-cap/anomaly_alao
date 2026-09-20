"""Offline validation of the I-058 hitch build of the script profiler.

The mean is not what the player feels.  `lab/profiler-hitch` is the same I-048
profiler plus, per callback and per listener: the worst single call, a coarse
log2 histogram the parser reads a p99 off, and the time/frame/cost of the first
call that crossed the floor - so "the first inventory open of the session cost
37 ms" separates from "every open costs 5 ms".

Everything here runs under the same LuaJIT 2.0 the game uses (`lupa.luajit20`)
against stubbed engine globals, like `test_profiler.py`.  Nothing touches the
game.  The stub `profile_timer` counts microseconds, so calibration lands on
~1000 units/ms and the 0.1 ms floor lands on ~100 fake microseconds - which
means a fake callback that spends 50 us is BELOW the floor and one that spends
8000 us is a 8 ms hitch.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

LAB = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LAB / "framework"))

from aalo import profiler as _profiler  # noqa: E402

lupa = pytest.importorskip("lupa.luajit20", reason="lupa (LuaJIT 2.0) not installed")

HITCH_LUA = LAB / "profiler-hitch" / "gamedata" / "scripts" / "zzz_alao_profiler.script"
BASE_LUA = LAB / "profiler" / "gamedata" / "scripts" / "zzz_alao_profiler.script"


def _src(path=HITCH_LUA, listeners=False) -> str:
    s = path.read_text(encoding="utf-8")
    if listeners:
        out = s.replace("local WRAP_LISTENERS  = false",
                        "local WRAP_LISTENERS  = true", 1)
        assert out != s, "the WRAP_LISTENERS switch moved"
        return out
    return s


# A frame is 5 ms of engine time.  Costs are fake microseconds injected into the
# stub timer, so they are exact and the test does not depend on machine speed.
#   actor_on_update   200 us every frame          -> above the 100 us floor
#   cheap_thing        20 us every frame          -> below it, the hot-path case
#   menu_open        8000 us on frame 700, then
#                    5000 us every 1200 frames    -> the hitch, first one worst
ENGINE_STUB = r"""
__log = {}
function printf(fmt, ...)
    local ok, s = pcall(string.format, fmt, ...)
    __log[#__log + 1] = ok and s or ("PRINTF-FAILED " .. tostring(fmt))
end
printe, printd = printf, printf
function callstack() end

__extra = 0
__frame = 0
__tg = 100000

local function now_us() return os.clock() * 1000000 + __extra end

function profile_timer()
    local t = {acc = 0, t0 = 0}
    function t:start() self.t0 = now_us() end
    function t:stop()  self.acc = self.acc + (now_us() - self.t0) end
    function t:time()  return self.acc end
    return t
end

__dev = {frame = 0, precache_frame = 0, time_delta = 5}
function device() return __dev end
function time_global() return __tg end
"""

# the flat stand-in dispatcher, for the tests that do not need the real one
VANILLA_AXR = r"""
axr_main = {intercepts = {}}
function axr_main.callback_add(name) axr_main.intercepts[name] = {} end
function axr_main.callback_set(name, f) axr_main.intercepts[name][f] = true end
function axr_main.callback_unset(name, f) axr_main.intercepts[name][f] = nil end
function axr_main.make_callback(name, ...)
    local t = axr_main.intercepts[name]
    if t then
        for f, _ in pairs(t) do
            if type(f) == "function" then f(...) end
        end
    end
end
function SendScriptCallback(name, ...) axr_main.make_callback(name, ...) end
"""

# the workload: registrations, the frame driver, the log sink
WIRING = r"""
function RegisterScriptCallback(name, f) axr_main.callback_set(name, f) end

for _, n in ipairs({"actor_on_update", "cheap_thing", "menu_open"}) do
    axr_main.callback_add(n)
end

RegisterScriptCallback("actor_on_update", function() __extra = __extra + 200 end)
RegisterScriptCallback("cheap_thing",     function() __extra = __extra + 20 end)

__menu_calls = 0
RegisterScriptCallback("menu_open", function()
    __menu_calls = __menu_calls + 1
    __extra = __extra + (__menu_calls == 1 and 8000 or 5000)
end)

function __drive(frames)
    for _ = 1, frames do
        __frame = __frame + 1
        __dev.frame = __frame
        __tg = __tg + 5
        SendScriptCallback("actor_on_update")
        SendScriptCallback("cheap_thing")
        if __frame == 700 or (__frame > 700 and __frame % 1200 == 0) then
            SendScriptCallback("menu_open")
        end
    end
end

function __dumped() return table.concat(__log, "\n") end
"""


# Listener mode needs the REAL axr_main.script: `intercepts` has to be a
# file-local of that module for debug.getupvalue to find it, and the flat stub
# dispatcher above keeps it on the module table.  Same file test_profiler.py
# wraps, and the same _g.script SendScriptCallback around it.
from test_profiler import ENGINE_BITS, REAL_AXR  # noqa: E402

REAL_SEND = r"""
function SendScriptCallback(name, ...)
    axr_main.make_callback(name, ...)
    if (axr_main[name]) then axr_main[name](...) end
end
"""


def _run(src, frames=18500, real_axr=False):
    lua = lupa.LuaRuntime(unpack_returned_tuples=True)
    lua.execute(ENGINE_STUB)
    if real_axr:
        lua.execute(ENGINE_BITS)
        env, err = lua.eval("__load_module")("axr_main", REAL_AXR.read_text(encoding="cp1251"))
        assert env is not None, f"real axr_main.script ({REAL_AXR}) would not load: {err}"
        lua.execute(REAL_SEND)
    else:
        lua.execute(VANILLA_AXR)
    lua.execute(WIRING)
    lua.execute(src)
    lua.eval("on_game_start")()
    lua.eval("__drive")(frames)
    return lua, _profiler.parse(lua.eval("__dumped")())


@pytest.fixture(scope="module")
def driven():
    lua, log = _run(_src())
    return {"lua": lua, "log": log}


@pytest.fixture(scope="module")
def driven_listeners():
    if not REAL_AXR.is_file():
        pytest.skip(f"no axr_main.script to load: {REAL_AXR}")
    lua, log = _run(_src(listeners=True), real_axr=True)
    return {"lua": lua, "log": log}


# ---------------------------------------------------------------------------
# the Lua side
# ---------------------------------------------------------------------------

def test_hitch_overlay_compiles_under_luajit20():
    lua = lupa.LuaRuntime(unpack_returned_tuples=True)
    ok, err = lua.eval(
        "function(src) local f, e = loadstring(src); if f then return true, '' end return false, e end"
    )(_src())
    assert ok, f"hitch profiler does not compile: {err}"


def test_header_announces_the_hitch_half(driven):
    h = driven["log"].header
    assert h is not None and h.usable
    assert h.hitch == "on", h.raw
    assert h.hitch_floor_ms == pytest.approx(0.1)
    assert h.hitch_buckets == 14
    assert 900 < h.units_per_ms < 1100


def test_the_base_profiler_still_says_nothing_about_hitches():
    """The existing overlays are untouched; locked measurements depend on them."""
    _, log = _run(BASE_LUA.read_text(encoding="utf-8"), frames=6500)
    assert log.header.hitch == "off"
    assert log.hitches == {}
    assert log.hitch_ranking() == []


def test_per_frame_numbers_are_unchanged_by_the_hitch_half(driven):
    """Same sums and counts as I-048: the tail is recorded, not substituted."""
    log = driven["log"]
    rows = {r["name"]: r for r in log.ranking(top=None, drop_first=0)}
    assert rows["actor_on_update"]["calls_per_frame"] == pytest.approx(1.0, abs=0.01)
    assert rows["actor_on_update"]["ms_per_frame"] == pytest.approx(0.20, abs=0.02)
    assert rows["cheap_thing"]["ms_per_frame"] == pytest.approx(0.02, abs=0.01)
    assert driven["lua"].eval("__extra") > 0


def test_hitch_line_isolates_the_first_slow_call(driven):
    log = driven["log"]
    rows = {(r["scope"], r["name"]): r for r in log.hitch_ranking()}
    m = rows[("cb", "menu_open")]
    # the first menu_open is 8 ms, every later one 5 ms
    assert m["max_ms"] == pytest.approx(8.0, abs=0.3)
    assert m["first_ms"] == pytest.approx(8.0, abs=0.3)
    assert m["first_frame"] == 700
    assert m["first_t"] == 100000 + 700 * 5
    # every menu_open is milliseconds, so none of them is below the floor. The
    # count is 15 and not the 16 the driver fired: the last one lands after the
    # third dump and a window only reaches the log when it is dumped.
    assert m["calls"] == m["above_floor"] == 15
    assert driven["lua"].eval("__menu_calls") == 16


def test_a_cheap_callback_practically_never_reaches_the_histogram(driven):
    """The 20 us callback is below the 0.1 ms floor: one compare, no bookkeeping.

    "Practically" is the honest word.  Over 18500 frames the OS does schedule us
    out mid-call, and when it does, that stall lands on whichever callback was
    running - which is the correct answer, not a bug.  What the floor buys is
    that the cheap callback pays nothing on the 18499 calls where nothing
    happened.
    """
    rows = {r["name"]: r for r in driven["log"].hitch_ranking()}
    cheap = rows.get("cheap_thing")
    if cheap is not None:
        assert cheap["above_floor"] < 0.001 * cheap["calls"], cheap
    hot = rows["actor_on_update"]          # 200 us of injected cost, every call
    assert hot["above_floor"] == hot["calls"]


def test_hitch_state_counts_only_slow_names(driven):
    state = driven["lua"].eval("alao_profiler_state")()
    assert state["hitch_floor_units"] == pytest.approx(100, rel=0.15)
    # actor_on_update and menu_open always; cheap_thing only if the OS stalled
    assert 2 <= state["hitch_names"] <= 3


def test_buckets_place_each_call_in_the_right_power_of_two(driven):
    log = driven["log"]
    rows = {r["name"]: r for r in log.hitch_ranking()}
    # floor 0.1 ms: bucket 1 = [0.1,0.2), 2 = [0.2,0.4) ... 8 ms is bucket 7
    # ([6.4,12.8)), 5 ms is bucket 6 ([3.2,6.4))
    b = rows["menu_open"]["buckets"]
    assert b[6] == 1, b               # exactly the one 8 ms call, [6.4,12.8)
    assert b[5] == rows["menu_open"]["above_floor"] - 1, b   # the 5 ms ones, [3.2,6.4)
    assert sum(b) == rows["menu_open"]["above_floor"]
    # actor_on_update is 0.2 ms of injected cost every frame -> bucket 2,
    # [0.2,0.4). A handful of calls land elsewhere because the stub timer also
    # counts real os.clock time and the OS does schedule us out.
    a = rows["actor_on_update"]["buckets"]
    assert a[1] > 0.99 * sum(a), a
    assert sum(a) == rows["actor_on_update"]["above_floor"]


def test_p99_of_a_per_frame_callback_is_its_own_bucket(driven):
    rows = {r["name"]: r for r in driven["log"].hitch_ranking(pct=99.0)}
    a = rows["actor_on_update"]
    assert a["p_lo_ms"] == pytest.approx(0.2, abs=0.01)
    assert a["p_hi_ms"] == pytest.approx(0.4, abs=0.01)


def test_p99_of_a_rare_hitch_falls_below_the_floor(driven):
    """15 menu opens in 18500 frames of actor calls: the 99th percentile of the
    NAME is still fast, which is the whole reason max and p99 are both printed."""
    log = driven["log"]
    h = log.hitches[("cb", "menu_open")]
    lo, hi = h.percentile_units(99.0, total_calls=18500)
    assert lo == 0.0 and hi == pytest.approx(h.floor_units)
    # against its own calls only, the p99 is the worst call
    lo2, _ = h.percentile_units(99.0, total_calls=h.above)
    assert (log.to_ms(lo2) or 0) >= 3.2


def test_hitch_lines_are_run_scoped_and_cumulative(driven):
    """Not per window: the first-open hitch lives in the window every report drops."""
    text = "\n".join(l for l in driven["lua"].eval("__dumped")().splitlines()
                     if "|hit|" in l and "name=menu_open" in l)
    seqs = [l for l in text.splitlines()]
    assert len(seqs) >= 2, "expected one hit line per dump window"
    aboves = [int(l.split("above=")[1].split("|")[0]) for l in seqs]
    assert aboves == sorted(aboves) and aboves[0] >= 1 and aboves[-1] > aboves[0]


def test_listener_mode_attributes_the_hitch_to_a_subscriber(driven_listeners):
    log = driven_listeners["log"]
    assert log.header.listeners.endswith(":ok"), log.header.raw
    rows = [r for r in log.hitch_ranking(scope="lst")]
    assert rows, "no per-listener hitch rows"
    top = rows[0]
    assert top["name"].startswith("menu_open#")
    assert top["max_ms"] == pytest.approx(8.0, abs=0.3)
    assert top["first_frame"] == 700
    # the cheap listener is at worst a rounding error in there (see the
    # per-callback test above for why it can appear at all)
    for r in rows:
        if r["name"].startswith("cheap_thing#"):
            assert r["above_floor"] < 0.001 * r["calls"], r


def test_listener_mode_does_not_swallow_calls(driven_listeners):
    lua = driven_listeners["lua"]
    assert lua.eval("__menu_calls") == 16
    state = lua.eval("alao_profiler_state")()
    assert state["depth"] == 0 and state["frames"] == 18500


# ---------------------------------------------------------------------------
# what the instrument costs
# ---------------------------------------------------------------------------

CHEAP_ONLY = r"""
function __drive_cheap(frames)
    for _ = 1, frames do
        __frame = __frame + 1
        __dev.frame = __frame
        __tg = __tg + 5
        SendScriptCallback("cheap_thing")
        SendScriptCallback("cheap_thing")
        SendScriptCallback("cheap_thing")
        SendScriptCallback("cheap_thing")
    end
end
"""


def _cheap_wall_s(src, frames=60000, rounds=5):
    """Best-of-K wall time for sub-floor calls only - the hot path that matters.

    A fresh runtime per arm, a collect before every timed round, best of K:
    the microbench protocol of beam-ideas section 2, applied to the instrument
    rather than to a rewrite.
    """
    best = None
    for _ in range(rounds):
        lua = lupa.LuaRuntime(unpack_returned_tuples=True)
        lua.execute(ENGINE_STUB)
        lua.execute(VANILLA_AXR)
        lua.execute(WIRING)
        lua.execute(src)
        lua.execute(CHEAP_ONLY)
        lua.eval("on_game_start")()
        lua.eval("__drive_cheap")(2000)          # warm the traces
        lua.execute("collectgarbage()")
        t0 = time.perf_counter()
        lua.eval("__drive_cheap")(frames)
        dt = time.perf_counter() - t0
        best = dt if best is None else min(best, dt)
    return best


@pytest.mark.slow
def test_hot_path_cost_is_unchanged_for_sub_floor_calls():
    """240k instrumented calls, none of them slow, hitch build vs I-048 build.

    The hitch half is meant to be one numeric compare on this path.  A wall-clock
    ratio is noisy even best-of-5, so the bound is loose on purpose: what it
    actually rules out is someone adding a table lookup or an allocation per call.
    """
    base = _cheap_wall_s(BASE_LUA.read_text(encoding="utf-8"))
    hit = _cheap_wall_s(_src())
    ratio = hit / base
    print(f"\nhot path: base {base * 1000:.1f} ms, hitch {hit * 1000:.1f} ms, "
          f"ratio {ratio:.3f} (240000 sub-floor calls, best of 5)")
    assert ratio < 1.25, f"hitch build is {ratio:.2f}x the base build on the hot path"


# ---------------------------------------------------------------------------
# the parser, on hand-written lines
# ---------------------------------------------------------------------------

HAND = """
ALAOPROF|1|hdr|ts=1|timer=profile_timer|units_per_ms=1000.000000|calib_ms=250|calib_units=250000|overhead_ns=200|make_callback=true|binders=off|listeners=off|dump_ms=30000|hitch=on|hitch_floor_ms=0.1000|hitch_buckets=14
ALAOPROF|1|win|seq=1|t0=0|t1=30000|span_ms=30000|frames=6000|total_units=600000|calls=6000|nested=0|names=1
ALAOPROF|1|cb|seq=1|name=thing|calls=6000|units=600000.000|nested=0
ALAOPROF|1|hit|seq=1|scope=cb|name=thing|max=9000.000|above=3|first_t=1500|first_frame=300|first_units=9000.000|floor=100.000|b=1,0,0,0,0,1,1,0,0,0,0,0,0,0
ALAOPROF|1|eow|seq=1|frames_total=6000
"""


def test_parser_reads_a_hand_written_hit_line():
    log = _profiler.parse(HAND)
    assert log.header.hitch == "on"
    h = log.hitches[("cb", "thing")]
    assert h.above == 3 and h.first_frame == 300
    assert h.buckets[0] == 1 and h.buckets[5] == 1 and h.buckets[6] == 1
    rows = log.hitch_ranking()
    assert len(rows) == 1
    r = rows[0]
    assert r["max_ms"] == pytest.approx(9.0)
    assert r["calls"] == 6000          # from the cb line, fast calls included
    assert r["above_floor"] == 3
    assert r["floor_ms"] == pytest.approx(0.1)
    # 99th percentile of 6000 calls of which 3 are slow -> below the floor
    assert r["p_lo_ms"] == 0.0


def test_bucket_edges_are_powers_of_two_from_the_floor():
    h = _profiler.parse(HAND).hitches[("cb", "thing")]
    assert h.bucket_floor_units(1) == pytest.approx(100)
    assert h.bucket_floor_units(7) == pytest.approx(6400)
    # the top bucket is open ended
    # 3 slow calls at 0.1-0.2, 3.2-6.4 and 6.4-12.8 ms: the median of those is
    # the middle bucket, and the top bucket is the open-ended one
    lo, hi = h.percentile_units(50.0, total_calls=3)
    assert lo == pytest.approx(3200) and hi == pytest.approx(6400)
    lo, hi = h.percentile_units(99.0, total_calls=3)
    assert lo == pytest.approx(6400) and hi == pytest.approx(12800)


def test_old_parser_fields_survive_a_hitch_log():
    """`hit` is an added line kind, not a changed one - windows still parse."""
    log = _profiler.parse(HAND)
    assert len(log.windows) == 1 and log.windows[0].complete
    assert log.windows[0].entries["thing"].calls == 6000
    assert log.window_ms_per_frame(drop_first=0) == [pytest.approx(0.1)]
