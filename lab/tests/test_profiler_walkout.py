"""Offline validation of the I-062 walkout build of the script profiler.

The walkout question is "what is in the 25-44 ms frames when the player leaves
the start area", and the I-048 profiler cannot answer it because it watches one
door into Lua (`axr_main.make_callback`) and the engine has several.  This build
opens three more - object binders (`bnd`), engine-called globals (`eng`),
deferred bodies (`evt`) - and adds a per-FRAME line that says how much of a slow
frame was script at all.

Everything here runs under the same LuaJIT 2.0 the game uses (`lupa.luajit20`)
against stubbed engine globals, like `test_profiler.py` and
`test_profiler_hitch.py`.  Nothing touches the game.  The stub `profile_timer`
counts microseconds, so a callback that injects 8000 fake microseconds is an
8 ms call, and `time_global()` advances by whatever the driver says, so a 40 ms
frame is exact rather than machine dependent.
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

WALK_LUA = LAB / "profiler-walkout" / "gamedata" / "scripts" / "zzz_alao_profiler.script"
HITCH_LUA = LAB / "profiler-hitch" / "gamedata" / "scripts" / "zzz_alao_profiler.script"


def _src(path=WALK_LUA, listeners=False, **swaps) -> str:
    s = path.read_text(encoding="utf-8")
    if listeners:
        out = s.replace("local WRAP_LISTENERS  = false",
                        "local WRAP_LISTENERS  = true", 1)
        assert out != s, "the WRAP_LISTENERS switch moved"
        s = out
    for old, new in swaps.items():
        assert old.replace("__", " ") or True
    return s


def _swap(src: str, old: str, new: str) -> str:
    assert old in src, f"switch moved: {old!r}"
    return src.replace(old, new, 1)


# ---------------------------------------------------------------------------
# the stubbed engine
# ---------------------------------------------------------------------------

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
__step = 5
__spike_us = 0

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

-- axr_main, vanilla shape
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
function RegisterScriptCallback(name, f) axr_main.callback_set(name, f) end

-- _g.script's deferred-call machinery, in its vanilla shape
__queue = {}
function CreateTimeEvent(obj_id, ev_id, timer, f, ...)
    __queue[#__queue + 1] = {f = f, args = {...}}
    return true
end
function ProcessEventQueue()
    __extra = __extra + 30
    for i = 1, #__queue do
        local e = __queue[i]
        e.f(unpack(e.args))
    end
end

-- level.add_call and AddUniqueCall in their REAL shape: _g.script's
-- AddUniqueCall builds a bridge closure around the caller's functor and hands
-- THAT to level.add_call.  The first in-game run labelled the bridge
-- (`call_cond:_g.script:456`) instead of the owner, and the bridge being on
-- the same axis swallowed the owner as `nested`.
__lvl = {}
level = {add_call = function(cond, act) __lvl[#__lvl + 1] = {cond, act} end}
function AddUniqueCall(func)
    level.add_call(function()          -- the plumbing closure, _g.script:456
        func()
        return false
    end, function() return true end)
end
function __run_level_calls()
    for i = 1, #__lvl do __lvl[i][1]() end
end

-- Two binder classes the walkout build's target list names.  They are plain
-- tables here; in the game they are luabind class tables, which is why the
-- overlay checks that every assignment actually took instead of assuming.
__spawned, __updated, __destroyed = 0, 0, 0
xr_motivator = {motivator_binder = {}}
function xr_motivator.motivator_binder:net_spawn(se)
    __spawned = __spawned + 1
    __extra = __extra + 4000              -- 4 ms: an NPC coming online
    return true                            -- the engine BELIEVES this
end
function xr_motivator.motivator_binder:net_destroy()
    __destroyed = __destroyed + 1
    __extra = __extra + 100
end
function xr_motivator.motivator_binder:update()
    __updated = __updated + 1
    __extra = __extra + 10
    SendScriptCallback("npc_on_update")    -- a callback INSIDE a binder body
end

bind_monster = {generic_object_binder = {}}
function bind_monster.generic_object_binder:update() __extra = __extra + 5 end

-- an engine-called global that exists from the start
visual_memory_manager = {}
function visual_memory_manager.get_visible_value(a, b)
    __extra = __extra + 40
    return 0.5
end

function __dumped() return table.concat(__log, "\n") end
"""

WIRING = r"""
for _, n in ipairs({"actor_on_update", "npc_on_update"}) do
    axr_main.callback_add(n)
end
RegisterScriptCallback("actor_on_update", function()
    __extra = __extra + 200 + __spike_us
end)
RegisterScriptCallback("npc_on_update", function() __extra = __extra + 15 end)
axr_main.callback_add("cheap_thing")
RegisterScriptCallback("cheap_thing", function() __extra = __extra + 2 end)

__mb = xr_motivator.motivator_binder

function __drive(frames)
    for _ = 1, frames do
        __frame = __frame + 1
        __dev.frame = __frame
        __tg = __tg + __step
        SendScriptCallback("actor_on_update")
        __mb:update()
        bind_monster.generic_object_binder:update()
        visual_memory_manager.get_visible_value(1, 2)
        ProcessEventQueue()
    end
end

-- n frames in a row that are all long enough to cross the floor, so the
-- after_log flag has something to land on
function __slow_run(n, ms)
    for _ = 1, n do
        __frame = __frame + 1
        __dev.frame = __frame
        __tg = __tg + ms
        SendScriptCallback("actor_on_update")
    end
end

-- a wave of net_spawns inside a frame that is then closed as a slow one
function __spawn_spike(n, ms)
    __frame = __frame + 1
    __dev.frame = __frame
    __tg = __tg + __step
    SendScriptCallback("actor_on_update")
    for _ = 1, n do __mb:net_spawn({}) end
    __frame = __frame + 1
    __dev.frame = __frame
    __tg = __tg + ms
    SendScriptCallback("actor_on_update")
end

-- One frame that costs `us` of script and is then closed by a frame boundary
-- `ms` engine-milliseconds later.  The boundary reports the frame that ENDED,
-- so the spike has to be spent before the jump, not after it.
function __spike(ms, us)
    __frame = __frame + 1
    __dev.frame = __frame
    __tg = __tg + __step
    __spike_us = us or 0
    SendScriptCallback("actor_on_update")
    __spike_us = 0
    __frame = __frame + 1
    __dev.frame = __frame
    __tg = __tg + ms
    SendScriptCallback("actor_on_update")
end

function __spawn_wave(n)
    for _ = 1, n do __mb:net_spawn({}) end
end
"""


def _run(src, frames=120, listeners=False, extra=None):
    lua = lupa.LuaRuntime(unpack_returned_tuples=True)
    lua.execute(ENGINE_STUB)
    lua.execute(WIRING)
    if extra:
        lua.execute(extra)
    lua.execute(src)
    lua.eval("on_game_start")()
    if frames:
        lua.eval("__drive")(frames)
    return lua


def _dump_and_parse(lua):
    lua.eval("alao_profiler_dump")()
    return _profiler.parse(lua.eval("__dumped")())


@pytest.fixture(scope="module")
def driven():
    lua = _run(_src(), frames=200)
    lua.eval("__spike")(43, 0)          # engine-only slow frame
    lua.eval("__spawn_spike")(3, 26)    # three NPCs online in one slow frame
    lua.eval("__spike")(31, 9000)       # a slow frame that IS script
    lua.eval("__drive")(50)
    return {"lua": lua, "log": _dump_and_parse(lua)}


# ---------------------------------------------------------------------------
# the Lua side
# ---------------------------------------------------------------------------

def test_walkout_overlay_compiles_under_luajit20():
    lua = lupa.LuaRuntime(unpack_returned_tuples=True)
    ok, err = lua.eval(
        "function(src) local f, e = loadstring(src); if f then return true, '' end return false, e end"
    )(_src())
    assert ok, f"walkout profiler does not compile: {err}"


def test_the_hitch_build_is_left_alone():
    """Locked gen-5 measurements were taken with it; it must stay hitch-only."""
    s = HITCH_LUA.read_text(encoding="utf-8")
    assert "I-062" not in s
    assert "ALAOPROF|1|frm|" not in s


def test_second_header_line_says_what_was_wrapped(driven):
    log = driven["log"]
    assert log.header is not None and log.header.usable
    w = log.walkout
    assert w is not None, "no ALAOPROF wdr line"
    assert w.frames == "on"
    assert w.frame_floor_ms == 12
    assert w.binder_update is True
    wrapped = w.wrapped
    # xr_motivator (3 methods) + bind_monster (1) are the two stubbed classes
    assert wrapped["bnd"] >= 4, w.raw
    assert wrapped["eng"] >= 2, w.raw          # get_visible_value, ProcessEventQueue
    assert wrapped["evt"] >= 2, w.raw          # CreateTimeEvent, AddUniqueCall
    # everything else on the target list is simply not in this stub, and that
    # has to be visible rather than silently swallowed
    assert "bind_stalker.actor_binder" in w.misses


def test_the_callback_ranking_survives_binder_wrapping(driven):
    """The reason the old WRAP_BINDERS mode was unusable, fixed.

    It shared `depth` and the main timer with make_callback, so every callback
    fired inside a wrapped binder body became `nested` and untimed.  Separate
    axes mean npc_on_update - which this stub only ever fires from inside
    motivator_binder:update - is still timed and still ranked.
    """
    rows = {r["name"]: r for r in driven["log"].ranking(top=None, drop_first=0)}
    assert rows["actor_on_update"]["ms_per_frame"] > 0
    assert rows["npc_on_update"]["ms_per_frame"] > 0
    assert rows["npc_on_update"]["calls_per_frame"] > 0.9
    assert rows["npc_on_update"]["nested_per_frame"] == 0


def test_binder_axis_is_attributed_per_class_and_method(driven):
    rows = {r["name"]: r for r in driven["log"].axis_ranking("bnd", top=None, drop_first=0)}
    assert "xr_motivator.motivator_binder.update" in rows
    assert "xr_motivator.motivator_binder.net_spawn" in rows
    assert "bind_monster.generic_object_binder.update" in rows
    # net_spawn injects 4 ms, update 10 us plus the 15 us callback inside it
    assert rows["xr_motivator.motivator_binder.net_spawn"]["us_per_call"] == pytest.approx(4000, rel=0.2)
    assert rows["xr_motivator.motivator_binder.update"]["us_per_call"] == pytest.approx(25, abs=15)


def test_net_spawn_return_value_is_not_swallowed():
    """The engine believes net_spawn's boolean; a wrapper that dropped it would
    break the game rather than mismeasure it.  And the arity stays 1, so a
    caller counting results with select('#') sees what it used to."""
    lua = _run(_src(), frames=5)
    lua.execute("__r1, __r2 = select('#', __mb:net_spawn({})), __mb:net_spawn({})")
    assert lua.eval("__r1") == 1
    assert lua.eval("__r2") is True


def test_engine_called_globals_are_timed_and_still_return(driven):
    rows = {r["name"]: r for r in driven["log"].axis_ranking("eng", top=None, drop_first=0)}
    assert "visual_memory_manager.get_visible_value" in rows
    assert "ProcessEventQueue" in rows
    assert rows["visual_memory_manager.get_visible_value"]["us_per_call"] == pytest.approx(40, abs=20)
    driven["lua"].execute("__gv = visual_memory_manager.get_visible_value(1, 2)")
    assert driven["lua"].eval("__gv") == 0.5


def test_time_event_bodies_are_attributed_to_their_registration_site():
    lua = _run(_src(), frames=10, extra=r"""
        __te = 0
        function __register()
            CreateTimeEvent("a", "b", 0, function()
                __te = __te + 1
                __extra = __extra + 700
                return true
            end)
        end
    """)
    lua.eval("__register")()
    lua.eval("__drive")(20)
    log = _dump_and_parse(lua)
    rows = {r["name"]: r for r in log.axis_ranking("evt", top=None, drop_first=0)}
    assert rows, "no evt rows"
    label = next(iter(rows))
    assert label.startswith("evt:"), label
    assert ":" in label[4:], "the label should carry a file:line registration site"
    assert lua.eval("__te") > 0, "the time event stopped running"
    assert rows[label]["us_per_call"] == pytest.approx(700, rel=0.3)


def test_a_lazily_loaded_module_is_picked_up_on_a_rescan():
    """`absent at on_game_start` is not `absent`: a module can be pulled in by
    the first reference to it, so the misses are re-tried."""
    src = _swap(_src(), "local RESCAN_EVERY      = 600", "local RESCAN_EVERY      = 20")
    lua = _run(src, frames=10)
    before = lua.eval("alao_profiler_state")()
    assert before["pending"] > 0, "nothing was left pending, so nothing can be re-tried"
    lua.execute(r"""
        game_relations = {}
        function game_relations.get_npcs_relation(a, b)
            __extra = __extra + 60
            return 1
        end
    """)
    lua.eval("__drive")(60)
    lua.execute("for i = 1, 30 do game_relations.get_npcs_relation(1, 2) end")
    after = lua.eval("alao_profiler_state")()
    assert after["wrapped_eng"] > before["wrapped_eng"], "the rescan never found it"
    log = _dump_and_parse(lua)
    rows = {r["name"]: r for r in log.axis_ranking("eng", top=None, drop_first=0)}
    assert "game_relations.get_npcs_relation" in rows
    lua.execute("__gr = game_relations.get_npcs_relation(1, 2)")
    assert lua.eval("__gr") == 1


# ---------------------------------------------------------------------------
# the frame recorder - the line this whole build exists for
# ---------------------------------------------------------------------------

def test_a_quiet_frame_never_reaches_the_log():
    lua = _run(_src(), frames=400)             # 5 ms frames, floor is 12
    log = _dump_and_parse(lua)
    assert log.frames == []


def test_an_engine_only_slow_frame_reports_almost_no_script(driven):
    """The 43 ms frame with nothing but the ordinary per-frame script in it.

    This is the shape the walkout frames are expected to have, and the number
    that matters is `invisible_ms`: frame ms minus the union of top-level
    script regions.
    """
    rows = driven["log"].frame_rows()
    assert rows, "no frm rows"
    worst = rows[0]
    assert worst["ms"] == pytest.approx(43, abs=1)
    assert worst["script_ms"] < 2.0
    assert worst["script_pct"] < 10
    assert worst["invisible_ms"] > 40


def test_a_slow_frame_that_is_script_is_attributed(driven):
    rows = [r for r in driven["log"].frame_rows() if 25 <= r["ms"] <= 35]
    assert rows, [r["ms"] for r in driven["log"].frame_rows()]
    r = rows[0]
    assert r["script_ms"] == pytest.approx(9.2, abs=1.0)
    assert r["script_pct"] > 25
    assert r["top"][0][0] == "actor_on_update"
    assert r["top"][0][1] == pytest.approx(9.2, abs=1.0)


def test_the_union_never_double_counts_a_callback_inside_a_binder(driven):
    """cb + bnd + eng + evt is more than the frame; u_top is the union.

    motivator_binder:update contains npc_on_update and ProcessEventQueue
    contains its time-event bodies, so the per-axis numbers overlap by
    construction.  Only regions entered with nothing else running go into
    u_top, which is why u_top can be quoted against the frame's wall time and
    the per-axis sums cannot.
    """
    for r in driven["log"].frame_rows():
        parts = (r["cb_ms"] or 0) + (r["bnd_ms"] or 0) + (r["eng_ms"] or 0) + (r["evt_ms"] or 0)
        assert r["script_ms"] <= parts + 0.01
        assert r["script_ms"] <= r["ms"] + 0.5


def test_net_spawns_are_counted_per_frame(driven):
    rows = [r for r in driven["log"].frame_rows() if r["spawn"] > 0]
    assert rows, "the spawn wave produced no frm row"
    assert sum(r["spawn"] for r in rows) >= 3
    assert driven["lua"].eval("__spawned") == 3


def test_gc_is_sampled_at_the_frame_boundaries(driven):
    """collectgarbage("count") at both ends of every frame, so a frame the
    collector ran in is identifiable instead of merely suspicious."""
    frames = driven["log"].frames
    assert frames
    for f in frames:
        assert f.gc0 > 0 and f.gc1 > 0
        assert isinstance(f.gc_delta_kb, float)
    # and a frame that really does allocate shows it
    lua = _run(_src(), frames=20)
    lua.execute("""
        function __alloc_spike(ms)
            __frame = __frame + 1; __dev.frame = __frame; __tg = __tg + 5
            SendScriptCallback("actor_on_update")
            __junk = {}
            for i = 1, 200000 do __junk[i] = {i} end
            __frame = __frame + 1; __dev.frame = __frame; __tg = __tg + ms
            SendScriptCallback("actor_on_update")
        end
    """)
    lua.eval("__alloc_spike")(30)
    log = _dump_and_parse(lua)
    assert any(f.gc_delta_kb > 1000 for f in log.frames), [f.gc_delta_kb for f in log.frames]


def test_both_clocks_are_printed_so_neither_is_assumed(driven):
    r = driven["log"].frame_rows()[0]
    assert r["dt_dev"] == pytest.approx(5.0)     # the stub's device().time_delta
    assert r["ms"] != r["dt_dev"], "ms must be the time_global delta, not time_delta"


def test_the_frame_log_is_bounded():
    """A bad scene must not be able to flood the engine log."""
    src = _swap(_src(), "local FRM_MAX_LINES     = 400", "local FRM_MAX_LINES     = 5")
    lua = _run(src, frames=20)
    for _ in range(40):
        lua.eval("__spike")(30, 0)
    state = lua.eval("alao_profiler_state")()
    assert state["frm_lines"] == 5
    assert state["frm_suppressed"] >= 30
    log = _dump_and_parse(lua)
    assert len(log.frames) == 5


def test_the_frame_after_a_log_write_is_flagged():
    """The printf lands in the next frame; that frame is an artefact, and the
    report drops it rather than pretending it is a hitch."""
    lua = _run(_src(), frames=20)
    lua.eval("__slow_run")(6, 30)       # six slow frames back to back
    log = _dump_and_parse(lua)
    assert len(log.frames) >= 4
    assert log.frames[0].after_log == 0
    assert any(f.after_log == 1 for f in log.frames[1:])
    kept = log.slow_frames(exclude_after_log=True)
    assert 0 < len(kept) < len(log.frames)


def test_state_reports_no_wedged_axis(driven):
    state = driven["lua"].eval("alao_profiler_state")()
    assert state["active"] == 0 and state["depth"] == 0
    assert state["frames"] > 200


def test_listener_mode_still_works_on_the_walkout_build():
    lua = _run(_src(listeners=True), frames=60)
    log = _dump_and_parse(lua)
    # the flat stub keeps `intercepts` on the module table, so the upvalue hunt
    # fails by design here - what must NOT happen is a broken dispatch
    assert lua.eval("__updated") == 60
    assert log.errors == []


# ---------------------------------------------------------------------------
# I-063's per-call trace, carried by this build too (one attended session)
# ---------------------------------------------------------------------------

def test_the_trace_switch_is_present_and_off_by_default():
    s = _src()
    assert "local TRACE_LISTENERS   = nil" in s
    assert "ALAOPROF|1|trace|" in s


# ---------------------------------------------------------------------------
# the parser, on hand-written lines
# ---------------------------------------------------------------------------

HAND = """
ALAOPROF|1|hdr|ts=1|timer=profile_timer|units_per_ms=1000.000000|calib_ms=250|calib_units=250000|overhead_ns=200|make_callback=true|binders=off|listeners=off|dump_ms=30000|hitch=on|hitch_floor_ms=0.1000|hitch_buckets=14
ALAOPROF|1|wdr|ts=1|walkout=bnd=140+update,eng=5,evt=3|binder_update=true|frames=on|frame_floor_ms=12|frame_max=400|rescan_every=600|pending=2|misses=se_heli.se_heli
ALAOPROF|1|win|seq=1|t0=0|t1=30000|span_ms=30000|frames=6000|total_units=600000|calls=6000|nested=0|names=1
ALAOPROF|1|cb|seq=1|name=thing|calls=6000|units=600000.000|nested=0
ALAOPROF|1|axs|seq=1|axis=bnd|name=xr_motivator.motivator_binder.update|calls=12000|units=240000.000|nested=0
ALAOPROF|1|axs|seq=1|axis=eng|name=visual_memory_manager.get_visible_value|calls=30000|units=90000.000|nested=10
ALAOPROF|1|frm|n=1|frame=8123|t=41000|ms=43|dt_dev=0.0430|u_top=1500.000|n_top=42|u_cb=900.000|u_bnd=1200.000|u_eng=300.000|u_evt=50.000|spawn=4|destroy=1|gc0=51200.0|gc1=50100.0|after_log=0|top=xr_motivator.motivator_binder.net_spawn~800.000,actor_on_update~400.000,-~0.000
ALAOPROF|1|frm|n=2|frame=8124|t=41043|ms=14|dt_dev=0.0140|u_top=200.000|n_top=8|u_cb=200.000|u_bnd=0.000|u_eng=0.000|u_evt=0.000|spawn=0|destroy=0|gc0=50100.0|gc1=50200.0|after_log=1|top=actor_on_update~200.000,-~0.000,-~0.000
ALAOPROF|1|frs|seq=1|lines=2|suppressed=0|floor_ms=12|wrapped_bnd=140|wrapped_eng=5|wrapped_evt=3|pending=2
ALAOPROF|1|trace|n=1|name=ActorMenu_on_before_init_mode#ui_inventory.script:93|units=18500|frame=900|t=45000|pre=cells=120,grid=40,idxer=120|post=cells=260,grid=88,idxer=260
ALAOPROF|1|trace|n=2|name=ActorMenu_on_before_init_mode#ui_inventory.script:93|units=4200|frame=1500|t=48000|pre=cells=260,grid=88,idxer=260|post=cells=260,grid=88,idxer=260
ALAOPROF|1|eow|seq=1|frames_total=6000
"""


def test_parser_reads_the_walkout_header():
    log = _profiler.parse(HAND)
    w = log.walkout
    assert w.frames == "on" and w.binder_update is True
    assert w.wrapped == {"bnd": 140, "eng": 5, "evt": 3}
    assert w.pending == 2 and "se_heli" in w.misses


def test_parser_reads_frame_lines_and_works_out_the_script_share():
    log = _profiler.parse(HAND)
    assert len(log.frames) == 2
    rows = log.frame_rows()                 # after_log frames dropped
    assert len(rows) == 1
    r = rows[0]
    assert r["ms"] == 43
    assert r["script_ms"] == pytest.approx(1.5)
    assert r["script_pct"] == pytest.approx(3.49, abs=0.1)
    assert r["invisible_ms"] == pytest.approx(41.5)
    assert r["spawn"] == 4 and r["destroy"] == 1
    assert r["gc_delta_kb"] == pytest.approx(-1100.0)
    assert r["top"][0] == ("xr_motivator.motivator_binder.net_spawn", pytest.approx(0.8))
    # and the artefact frame is reachable when you ask for it
    assert len(log.slow_frames(exclude_after_log=False)) == 2


def test_frame_summary_ranks_the_scopes_of_the_slow_frames():
    s = _profiler.parse(HAND).frame_summary()
    assert s["n"] == 1
    assert s["worst_ms"] == 43
    assert s["gc_frames"] == 1
    names = [a["name"] for a in s["top_scopes"]]
    assert names[0] == "xr_motivator.motivator_binder.net_spawn"
    assert "-" not in names


def test_parser_keeps_the_axes_out_of_the_callback_ranking():
    log = _profiler.parse(HAND)
    assert [r["name"] for r in log.ranking(drop_first=0)] == ["thing"]
    bnd = log.axis_ranking("bnd", drop_first=0)
    assert bnd[0]["name"] == "xr_motivator.motivator_binder.update"
    assert bnd[0]["calls_per_frame"] == pytest.approx(2.0)
    assert bnd[0]["us_per_call"] == pytest.approx(20.0)
    assert log.axis_ranking("eng", drop_first=0)[0]["nested_per_frame"] > 0
    assert log.axis_ranking("nope", drop_first=0) == []


def test_parser_reads_the_trace_lines_and_the_growth():
    rows = _profiler.parse(HAND).trace_rows()
    assert len(rows) == 2
    assert rows[0]["ms"] == pytest.approx(18.5)
    assert rows[0]["grew"] == {"cells": 140, "grid": 48}
    assert rows[1]["ms"] == pytest.approx(4.2)
    assert rows[1]["grew"] == {"cells": 0, "grid": 0}
    assert _profiler.parse(HAND).trace_rows("nothing") == []


def test_an_old_log_still_parses_and_says_so():
    """Every I-048 / I-058 log predates all of this and must keep working."""
    from test_profiler_hitch import HAND as OLD_HAND
    log = _profiler.parse(OLD_HAND)
    assert log.walkout is None
    assert log.frames == [] and log.traces == []
    assert log.frame_rows() == [] and log.frame_summary()["n"] == 0
    assert log.axis_ranking("bnd") == []
    assert log.windows[0].entries["thing"].calls == 6000
    assert log.hitch_ranking()[0]["max_ms"] == pytest.approx(9.0)


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


def _cheap_wall_s(src, frames=40000, rounds=5):
    """Best-of-K wall time for the make_callback hot path only.

    Fresh runtime per arm, a collect before every timed round, best of K - the
    microbench protocol of beam-ideas section 2 applied to the instrument.
    """
    best = None
    for _ in range(rounds):
        lua = lupa.LuaRuntime(unpack_returned_tuples=True)
        lua.execute(ENGINE_STUB)
        lua.execute(WIRING)
        lua.execute(src)
        lua.execute(CHEAP_ONLY)
        lua.eval("on_game_start")()
        lua.eval("__drive_cheap")(2000)
        lua.execute("collectgarbage()")
        t0 = time.perf_counter()
        lua.eval("__drive_cheap")(frames)
        dt = time.perf_counter() - t0
        best = dt if best is None else min(best, dt)
    return best


@pytest.mark.slow
def test_the_callback_hot_path_is_not_much_more_expensive():
    """160k SUB-FLOOR make_callbacks, walkout build vs the I-058 hitch build.

    Sub-floor is the case that matters: the calls are 2 us, under both the
    0.1 ms hitch floor and the 10 us top-3 floor, so each one pays exactly the
    compares and nothing else. The walkout additions on this path are: one
    compare, two increments, one add and the top-3 compare.  The binder / global / event axes are not on it
    at all - they cost only where the engine enters Lua through those doors,
    which is the price this build is deliberately paying.
    """
    base = _cheap_wall_s(HITCH_LUA.read_text(encoding="utf-8"))
    walk = _cheap_wall_s(_src())
    ratio = walk / base
    per_call_ns = (walk - base) * 1e9 / 160000
    print(f"\ncb hot path: hitch {base * 1000:.1f} ms, walkout {walk * 1000:.1f} ms, "
          f"ratio {ratio:.3f}, {per_call_ns:.0f} ns/call added "
          f"(160000 sub-floor calls, best of 5)")
    # The bound is loose on purpose, twice over.  A wall-clock ratio is noisy
    # even best-of-5 and this suite runs it alongside everything else; and the
    # stub drives only FOUR calls per frame, so the once-per-frame half of the
    # recorder (a collectgarbage("count"), six resets and the axis loop) is
    # amortised over four calls here and over hundreds in game - which makes
    # this ratio an over-statement, not a measurement of the game.  What it
    # rules out is someone putting a table lookup or an allocation on the
    # per-call path.
    assert ratio < 1.6, f"walkout build is {ratio:.2f}x the hitch build on the cb path"


@pytest.mark.slow
def test_the_binder_update_axis_is_priced():
    """What wrapping a per-object per-frame method actually costs, per call.

    This is the number the overlay's steady-state cost is argued from: multiply
    it by the number of bound objects that tick in a frame.
    """
    def wall(src, wrapped):
        best = None
        for _ in range(5):
            lua = lupa.LuaRuntime(unpack_returned_tuples=True)
            lua.execute(ENGINE_STUB)
            lua.execute(WIRING)
            lua.execute(src)
            if wrapped:
                lua.eval("on_game_start")()
            lua.execute("""
                function __drive_binders(n)
                    for _ = 1, n do
                        __frame = __frame + 1
                        __dev.frame = __frame
                        __tg = __tg + 5
                        for _ = 1, 40 do bind_monster.generic_object_binder:update() end
                    end
                end
            """)
            lua.eval("__drive_binders")(500)
            lua.execute("collectgarbage()")
            t0 = time.perf_counter()
            lua.eval("__drive_binders")(5000)
            dt = time.perf_counter() - t0
            best = dt if best is None else min(best, dt)
        return best
    off = wall(_src(), wrapped=False)
    on = wall(_src(), wrapped=True)
    per_call_us = (on - off) * 1e6 / (5000 * 40)
    print(f"\nbinder :update wrapping: {per_call_us:.2f} us per call "
          f"(200000 calls, best of 5, stubbed pure-Lua timer)")
    # the stub's profile_timer is pure Lua and therefore an UPPER bound on what
    # the engine's C++ timer costs; anything past this is a coding mistake
    assert per_call_us < 6.0, f"{per_call_us:.2f} us per wrapped binder update"

# ---------------------------------------------------------------------------
# v2: the two faults the first in-game run exposed
# (run 20260920-185607-I-062-a1c78b, three captures, all of them bnd=0)
# ---------------------------------------------------------------------------

# A luabind class object is USERDATA with an __index/__newindex metatable, not
# a table.  `newproxy(true)` is the only way to build one of those in pure
# LuaJIT 2.0, and it reproduces both of the v1 faults exactly: `type(cls)` is
# not "table", and `rawget` cannot read it at all.
#
# Also here: a module that does not exist until something touches `_G`, because
# Anomaly loads script namespaces lazily through the global metatable; and a
# class that lives only as a bare global, which is the other place luabind's
# `class "..."` can leave one.
LUABIND_STUB = r"""
function __luabind_class(store)
    local u = newproxy(true)
    local mt = getmetatable(u)
    mt.__index    = function(_, k) return store[k] end
    mt.__newindex = function(_, k, v) store[k] = v end
    return u
end

__ub_spawned, __ub_updated = 0, 0
local motivator = {}
function motivator:net_spawn(se)
    __ub_spawned = __ub_spawned + 1
    __extra = __extra + 4000
    return true
end
function motivator:update()
    __ub_updated = __ub_updated + 1
    __extra = __extra + 12
end
xr_motivator = {motivator_binder = __luabind_class(motivator)}

-- bind_crow does not exist until _G is asked for it
__lazy_hits = 0
local lazy = {
    bind_crow = function()
        __lazy_hits = __lazy_hits + 1
        local store = {}
        function store:update() __extra = __extra + 7 end
        return {crow_binder = __luabind_class(store)}
    end,
}
setmetatable(_G, {__index = function(t, k)
    local mk = lazy[k]
    if mk then
        local v = mk()
        rawset(t, k, v)
        lazy[k] = nil
        return v
    end
end})

-- se_restrictor exists ONLY as a bare global, with no se_zones module at all
local restr = {}
function restr:on_register() __extra = __extra + 90 end
se_restrictor = __luabind_class(restr)

-- the engine looks a method up on the class and calls it with the instance
function __engine_call(cls, m, inst, arg)
    local f = cls[m]
    return f(inst, arg)
end
"""


def _run_luabind(src, frames=30):
    lua = lupa.LuaRuntime(unpack_returned_tuples=True)
    lua.execute(ENGINE_STUB)
    lua.execute(WIRING)
    lua.execute(LUABIND_STUB)
    lua.execute(src)
    lua.eval("on_game_start")()
    if frames:
        lua.eval("__drive")(frames)
    return lua


@pytest.fixture(scope="module")
def luabind():
    lua = _run_luabind(_src())
    lua.execute("""
        __inst = {}
        for i = 1, 3 do __engine_call(xr_motivator.motivator_binder, "net_spawn", __inst, {}) end
        for i = 1, 5 do __engine_call(xr_motivator.motivator_binder, "update", __inst) end
        __engine_call(bind_crow.crow_binder, "update", __inst)
        __engine_call(se_restrictor, "on_register", __inst)
    """)
    return {"lua": lua, "log": _dump_and_parse(lua)}


def test_a_luabind_userdata_class_gets_wrapped(luabind):
    """v1 bailed on `type(cls) ~= "table"` and wrapped 0 of 32 classes in game."""
    rows = {r["name"]: r for r in luabind["log"].axis_ranking("bnd", top=None, drop_first=0)}
    assert "xr_motivator.motivator_binder.net_spawn" in rows, sorted(rows)
    assert "xr_motivator.motivator_binder.update" in rows
    assert rows["xr_motivator.motivator_binder.net_spawn"]["us_per_call"] == pytest.approx(4000, rel=0.2)
    # and the class still behaves: the body ran, the boolean came back
    assert luabind["lua"].eval("__ub_spawned") == 3
    assert luabind["lua"].eval("__ub_updated") == 5


def test_a_lazily_loaded_binder_module_is_reached_through_the_g_metatable(luabind):
    """Anomaly materialises a script namespace on the first `_G` reference."""
    assert luabind["lua"].eval("__lazy_hits") == 1
    rows = {r["name"] for r in luabind["log"].axis_ranking("bnd", top=None, drop_first=0)}
    assert "bind_crow.crow_binder.update" in rows


def test_a_class_that_lives_only_as_a_bare_global_is_found(luabind):
    rows = {r["name"] for r in luabind["log"].axis_ranking("bnd", top=None, drop_first=0)}
    assert "se_zones.se_restrictor.on_register" in rows, sorted(rows)
    probes = {p.target: p for p in luabind["log"].walkout.probes}
    assert probes["se_zones.se_restrictor"].via == "global"
    assert probes["xr_motivator.motivator_binder"].via == "module"


def test_every_target_gets_a_bnx_line_naming_the_types_it_saw(luabind):
    """The line that turns "bnd=0" from a wasted capture into a diagnosis."""
    probes = luabind["log"].walkout.probes
    assert len(probes) == 32, len(probes)
    hit = {p.target: p for p in probes if p.wrapped > 0}
    assert "xr_motivator.motivator_binder" in hit
    p = hit["xr_motivator.motivator_binder"]
    assert p.cls == "userdata" and p.mod == "table" and p.why in ("ok", "unverified")
    missing = next(p for p in probes if p.target == "bind_car.car_binder")
    assert missing.wrapped == 0 and missing.via == "none"
    assert "not found" in missing.why


def test_the_self_check_shouts_when_nothing_was_wrapped():
    """A zero axis measured NOTHING; it did not measure nothing happening."""
    lua = lupa.LuaRuntime(unpack_returned_tuples=True)
    lua.execute(ENGINE_STUB)
    lua.execute(WIRING)
    lua.execute("xr_motivator = nil; bind_monster = nil")   # no binder class anywhere
    lua.execute(_src())
    lua.eval("on_game_start")()
    log = _dump_and_parse(lua)
    assert log.walkout.selfcheck == "bnd-zero", log.walkout.raw
    assert log.walkout.wrapped["bnd"] == 0
    problems = log.walkout.problems()
    assert problems and "bnd=0" in problems[0]
    assert any("self-check" in e for e in log.errors), log.errors


def test_the_build_carries_a_version(luabind):
    assert luabind["log"].walkout.ver == 2


def test_a_unique_call_is_labelled_by_its_owner_not_the_plumbing():
    """FAULT 2: every capture's worst walk-era frame blamed `_g.script:456`.

    That line is the bridge closure AddUniqueCall builds around the caller's
    functor.  Two things had to change: label by the functor's own definition
    site, and stop level.add_call from wrapping the bridge - one outer region
    on the same axis was swallowing the owner as `nested`.
    """
    lua = _run(_src(), frames=10)
    lua.execute('__owner = loadstring("return function() __extra = __extra + 5000 end", '
                '"@owner_mod.script")()')
    lua.execute("AddUniqueCall(__owner)")
    lua.eval("__run_level_calls")()
    lua.eval("__drive")(20)
    log = _dump_and_parse(lua)
    names = [r["name"] for r in log.axis_ranking("evt", top=None, drop_first=0)]
    owner = [n for n in names if n.startswith("uniq:owner_mod.script:")]
    assert owner, names
    assert not [n for n in names if n.startswith("call_cond:")], names
    rows = {r["name"]: r for r in log.axis_ranking("evt", top=None, drop_first=0)}
    assert rows[owner[0]]["us_per_call"] == pytest.approx(5000, rel=0.3)


def test_a_plain_level_add_call_is_still_wrapped():
    """Only the AddUniqueCall bridge is skipped, not every add_call."""
    lua = _run(_src(), frames=10)
    lua.execute('__c = loadstring("return function() __extra = __extra + 300; return false end", '
                '"@caller_mod.script")()')
    lua.execute("level.add_call(__c, function() return true end)")
    lua.eval("__run_level_calls")()
    lua.eval("__drive")(20)
    names = [r["name"] for r in _dump_and_parse(lua).axis_ranking("evt", top=None, drop_first=0)]
    assert any(n.startswith("call_cond:caller_mod.script:") for n in names), names


def test_a_time_event_label_carries_its_ids_and_its_registration_site():
    lua = _run(_src(), frames=10, extra=r"""
        __te = 0
        function __register()
            CreateTimeEvent("squad_42", "regen|tick", 0, function()
                __te = __te + 1
                __extra = __extra + 700
                return true
            end)
        end
    """)
    lua.eval("__register")()
    lua.eval("__drive")(20)
    names = [r["name"] for r in _dump_and_parse(lua).axis_ranking("evt", top=None, drop_first=0)]
    tagged = [n for n in names if "squad_42.regen_tick" in n]
    assert tagged, names
    # the pipe in the ev_id must not have broken the line grammar
    assert "|" not in tagged[0]
    assert "@" in tagged[0], "no registration site on the label"
    assert lua.eval("__te") > 0


HAND_V2 = """
ALAOPROF|1|hdr|ts=1|timer=profile_timer|units_per_ms=1000.000000|calib_ms=250|calib_units=250000|overhead_ns=200|make_callback=true|binders=off|listeners=off|dump_ms=30000|hitch=on|hitch_floor_ms=0.1000|hitch_buckets=14
ALAOPROF|1|wdr|ver=2|ts=1|walkout=bnd=0+update,eng=12,evt=3|binder_update=true|frames=on|frame_floor_ms=12|frame_max=400|rescan_every=600|pending=40|selfcheck=bnd-zero|misses=xr_motivator.motivator_binder
ALAOPROF|1|bnx|target=xr_motivator.motivator_binder|wrapped=0|mod=table|cls=userdata|via=module|why=class is userdata
ALAOPROF|1|err|I-062 self-check bnd-zero: bnd=0 eng=12 evt=3 - the axes that read zero measured NOTHING.
ALAOPROF|1|win|seq=1|t0=0|t1=30000|span_ms=30000|frames=6000|total_units=600000|calls=6000|nested=0|names=1
ALAOPROF|1|cb|seq=1|name=thing|calls=6000|units=600000.000|nested=0
ALAOPROF|1|eow|seq=1|frames_total=6000
"""


def test_parser_surfaces_a_zero_axis_from_a_real_shaped_log():
    log = _profiler.parse(HAND_V2)
    w = log.walkout
    assert w.ver == 2 and w.selfcheck == "bnd-zero"
    assert w.wrapped == {"bnd": 0, "eng": 12, "evt": 3}
    probs = w.problems()
    assert len(probs) == 2 and "structurally zero" in probs[0]
    assert w.probes[0].cls == "userdata" and w.probes[0].wrapped == 0
    assert log.errors and "self-check" in log.errors[0]
