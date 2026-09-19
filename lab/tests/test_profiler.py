"""Offline validation of the I-048 script-side profiler.

Two halves:

1. the Lua overlay itself, executed under the same LuaJIT 2.0 the game runs
   (``lupa.luajit20``) against stubbed engine globals - it has to compile, wrap
   ``axr_main.make_callback``, calibrate its timer units against ``os.clock``,
   survive nested callbacks and print dumps we can parse back;
2. :mod:`aalo.profiler`, the parser - on the dump the Lua half actually
   produced, on a hand-written dump, and on a run directory.

Nothing here touches the game.  The stubbed ``profile_timer`` counts
microseconds (real ``os.clock`` plus an injectable ``__extra`` the fake
callbacks add), so a callback that bumps ``__extra`` by 300 must come back as
~300 units and the calibration must land on 1000 units per millisecond.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

LAB = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LAB / "framework"))

from aalo import profiler as _profiler  # noqa: E402

PROFILER_LUA = LAB / "profiler" / "gamedata" / "scripts" / "zzz_alao_profiler.script"

lupa = pytest.importorskip("lupa.luajit20", reason="lupa (LuaJIT 2.0) not installed")


# ---------------------------------------------------------------------------
# the Lua side
# ---------------------------------------------------------------------------

# Enough of the engine to run the profiler: a frame counter, a millisecond
# clock, printf into a sink, a profile_timer that counts microseconds, and
# axr_main's dispatcher in its vanilla shape (pairs over intercepts[name]).
STUB_PRELUDE = r"""
__log = {}
function printf(fmt, ...)
    local ok, s = pcall(string.format, fmt, ...)
    __log[#__log + 1] = ok and s or ("PRINTF-FAILED " .. tostring(fmt))
end
printe, printd = printf, printf
function callstack() end

__extra = 0          -- fake microseconds the fake callbacks "spend"
__frame = 0
__tg = 100000        -- time_global(), engine milliseconds

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

for _, n in ipairs({"actor_on_update", "npc_on_update", "nested_thing", "on_key_press"}) do
    axr_main.callback_add(n)
end

-- costs, in fake microseconds
RegisterScriptCallback("actor_on_update", function()
    __extra = __extra + 200
    SendScriptCallback("nested_thing")      -- exercises the depth guard
end)
RegisterScriptCallback("nested_thing", function() __extra = __extra + 50 end)
RegisterScriptCallback("npc_on_update", function() __extra = __extra + 100 end)

function __drive(frames)
    for _ = 1, frames do
        __frame = __frame + 1
        __dev.frame = __frame
        __tg = __tg + 5                     -- 5 ms per frame => 200 fps
        SendScriptCallback("actor_on_update")
        SendScriptCallback("npc_on_update")
        SendScriptCallback("npc_on_update")
    end
end

function __dumped() return table.concat(__log, "\n") end
"""


def _lua_source() -> str:
    return PROFILER_LUA.read_text(encoding="utf-8")


def test_profiler_compiles_under_luajit20():
    """The overlay must compile under the exact runtime the game uses."""
    lua = lupa.LuaRuntime(unpack_returned_tuples=True)
    ok, err = lua.eval(
        "function(src) local f, e = loadstring(src); if f then return true, '' end return false, e end"
    )(_lua_source())
    assert ok, f"profiler script does not compile: {err}"


@pytest.fixture(scope="module")
def driven():
    """Install the profiler in a stubbed runtime, run frames, return the log.

    Three dump windows: DUMP_EVERY_MS is 30000 and a stub frame is 5 ms, so
    6000 frames make one window.  18500 frames give three complete windows plus
    a partial fourth that must be dropped (no `eow`).
    """
    lua = lupa.LuaRuntime(unpack_returned_tuples=True)
    lua.execute(STUB_PRELUDE)
    lua.execute(_lua_source())
    lua.eval("on_game_start")()
    state = lua.eval("alao_profiler_state")()
    assert state["installed"] is True
    lua.eval("__drive")(18500)
    text = lua.eval("__dumped")()
    return {"text": text, "lua": lua, "state": lua.eval("alao_profiler_state")()}


def test_install_reports_a_usable_header(driven):
    log = _profiler.parse(driven["text"])
    assert log.errors == [], log.errors
    h = log.header
    assert h is not None, "no ALAOPROF hdr line"
    assert h.timer == "profile_timer"
    assert h.make_callback is True
    assert h.binders == "off"
    assert h.usable
    # the stub timer counts microseconds, so calibration must find ~1000 u/ms
    assert 900 < h.units_per_ms < 1100, h.raw
    assert h.calib_ms >= 200        # the configured 250 ms busy loop, give or take
    assert 0 < h.overhead_ns < 100000


def test_dump_windows_parse_and_are_complete(driven):
    log = _profiler.parse(driven["text"])
    complete = [w for w in log.windows if w.complete]
    assert len(complete) == 3, [(w.seq, w.complete, w.frames) for w in log.windows]
    # a window only exists once it has been dumped, so the 500 frames after the
    # last dump are simply lost. That is the cost of the design: a run gives up
    # to DUMP_EVERY_MS of tail. Nothing half-written ever reaches the log.
    assert len(log.windows) == 3
    for w in complete:
        assert w.frames == 6000
        assert w.span_ms == 30000
        assert w.names == len(w.entries)
        assert abs(w.sum_units - w.total_units) < 1.0


def test_script_ms_per_frame_matches_the_injected_cost(driven):
    """200 us (actor, inclusive of its 50 us nested call) + 2x100 us (npc)."""
    log = _profiler.parse(driven["text"])
    per_frame = log.window_ms_per_frame(drop_first=0)
    assert len(per_frame) >= 3
    for v in per_frame:
        assert 0.45 <= v <= 0.50, per_frame       # 0.45 ms injected + real time
    # windows of a stubbed run are identical by construction, so this is a
    # self-check on the arithmetic, not a claim about the game
    s = _profiler.spread(per_frame)
    assert s["cv_pct"] < 1.0
    assert 195 < _profiler.spread(log.fps_from_windows(drop_first=0))["mean"] < 205


def test_ranking_attributes_inclusive_top_level_time(driven):
    log = _profiler.parse(driven["text"])
    rows = {r["name"]: r for r in log.ranking(top=None, drop_first=0)}
    assert set(rows) >= {"actor_on_update", "npc_on_update", "nested_thing"}
    # actor_on_update: 1 call/frame at 200+50 us; npc_on_update: 2 at 100 us
    assert rows["actor_on_update"]["calls_per_frame"] == pytest.approx(1.0, abs=0.01)
    assert rows["npc_on_update"]["calls_per_frame"] == pytest.approx(2.0, abs=0.01)
    assert rows["actor_on_update"]["ms_per_frame"] == pytest.approx(0.25, abs=0.02)
    assert rows["npc_on_update"]["ms_per_frame"] == pytest.approx(0.20, abs=0.02)
    # nested calls are counted but not timed - that is the depth guard
    assert rows["nested_thing"]["ms_per_frame"] == 0.0
    assert rows["nested_thing"]["nested_per_frame"] == pytest.approx(1.0, abs=0.01)
    assert rows["actor_on_update"]["us_per_call"] == pytest.approx(250, abs=20)


def test_profiler_does_not_break_the_dispatch(driven):
    """Wrapping must not swallow calls: every listener still ran every frame."""
    lua = driven["lua"]
    # 18500 frames x (200 + 50 + 2x100) fake microseconds
    assert lua.eval("__extra") == pytest.approx(18500 * 450, rel=1e-9)
    assert driven["state"]["frames"] == 18500
    assert driven["state"]["depth"] == 0


def test_overhead_is_reported_and_small(driven):
    log = _profiler.parse(driven["text"])
    ov = log.overhead_ms_per_frame(drop_first=0)
    assert ov is not None and ov > 0
    # 4 timed + 1 nested call per frame; the stub timer is pure Lua so this is
    # an upper bound on what the engine timer costs, not a prediction
    assert ov < 0.5


# ---------------------------------------------------------------------------
# against the real winner axr_main.script
# ---------------------------------------------------------------------------

# Which copy of axr_main.script does the game actually load? Priority is:
# highest-priority ENABLED mod, else GAMMA's loose in-place patch in
# Anomaly/gamedata/scripts, else the .db archive. No enabled mod ships it (the
# one copy under GAMMA/mods belongs to a disabled mod), but GAMMA *does* patch
# it loose - and the loose one dispatches through
# `spairs(intercepts[name], sort_func_values_ascend)` (the min-heap `hspairs`
# from _g_patches.script), not the db copy's bare `pairs`. That loose file is
# the one the profiler has to wrap, so it is the one this test loads.
LOOSE_AXR = Path(r"D:\GOG_Games\Gamma\S.T.A.L.K.E.R. GAMMA\Anomaly\gamedata\scripts\axr_main.script")
DB_AXR = Path(r"C:\code\GIT\anomaly_alao\extracted\vanilla_db\raw\scripts\axr_main.script")
REAL_AXR = LOOSE_AXR if LOOSE_AXR.is_file() else DB_AXR

# what axr_main.script touches at module level, plus the two globals the loose
# copy's dispatcher needs (_g.script's spairs and _g_patches' comparator)
ENGINE_BITS = """
function spairs(t, order)
    local keys, n = {}, 0
    for k in pairs(t) do n = n + 1; keys[n] = k end
    if order then table.sort(keys, function(a, b) return order(t, a, b) end)
    else table.sort(keys) end
    local i = 0
    return function()
        i = i + 1
        if keys[i] ~= nil then return keys[i], t[keys[i]] end
    end
end
function sort_func_values_ascend(t, a, b) return t[a] < t[b] end
function ini_file_ex(name, rw)
    local o = {}
    function o:section_exist(s) return true end
    function o:w_value(a, b, c) end
    function o:r_value(a, b, c, d) return d end
    function o:save() end
    return o
end
function getFS() local f = {} function f:update_path(a, b) return "" end return f end
function __load_module(name, src)
    local env = setmetatable({}, {__index = _G})
    local chunk, err = loadstring(src, name)
    if not chunk then return nil, err end
    setfenv(chunk, env)
    local ok, e = pcall(chunk)
    if not ok then return nil, e end
    _G[name] = env
    return env, ""
end
"""

# _g.script's SendScriptCallback, verbatim, plus two listeners: one plain
# function and one userdata-style table, which are the dispatcher's two branches
REAL_WIRING = r"""
function SendScriptCallback(name, ...)
    axr_main.make_callback(name, ...)
    if (axr_main[name]) then axr_main[name](...) end
end
function RegisterScriptCallback(name, f) axr_main.callback_set(name, f) end
for _, n in ipairs({"actor_on_update", "npc_on_update", "nested_thing"}) do
    axr_main.callback_add(n)
end
RegisterScriptCallback("actor_on_update", function()
    __extra = __extra + 200
    SendScriptCallback("nested_thing")
end)
RegisterScriptCallback("nested_thing", function() __extra = __extra + 50 end)
RegisterScriptCallback("npc_on_update", function() __extra = __extra + 100 end)
local listener = {}
listener.npc_on_update = function(self) __extra = __extra + 25 end
RegisterScriptCallback("npc_on_update", listener)

function __drive(frames)
    for _ = 1, frames do
        __frame = __frame + 1
        __dev.frame = __frame
        __tg = __tg + 5
        SendScriptCallback("actor_on_update")
        SendScriptCallback("npc_on_update")
    end
end
function __dumped() return table.concat(__log, "\n") end
"""


def _real_axr_runtime(profiler_src=None):
    lua = lupa.LuaRuntime(unpack_returned_tuples=True)
    lua.execute(STUB_PRELUDE.split("-- axr_main, vanilla shape")[0])
    lua.execute(ENGINE_BITS)
    env, err = lua.eval("__load_module")("axr_main", REAL_AXR.read_text(encoding="cp1251"))
    assert env is not None, f"real axr_main.script ({REAL_AXR}) would not load: {err}"
    lua.execute(REAL_WIRING)
    lua.execute(profiler_src or _lua_source())
    return lua


def test_wraps_the_real_axr_main_without_changing_dispatch():
    if not REAL_AXR.is_file():
        pytest.skip(f"no axr_main.script to load: {REAL_AXR}")
    lua = _real_axr_runtime()
    lua.eval("on_game_start")()
    lua.eval("__drive")(12500)

    log = _profiler.parse(lua.eval("__dumped")())
    assert log.errors == []
    assert log.header.make_callback is True
    rows = {r["name"]: r for r in log.ranking(top=None, drop_first=0)}
    # 200 us + a 50 us nested call, inclusive
    assert rows["actor_on_update"]["ms_per_frame"] == pytest.approx(0.25, abs=0.02)
    # both dispatcher branches ran: 100 us function + 25 us userdata listener
    assert rows["npc_on_update"]["ms_per_frame"] == pytest.approx(0.125, abs=0.02)
    assert rows["nested_thing"]["nested_per_frame"] == pytest.approx(1.0, abs=0.01)
    # and nothing was swallowed
    assert lua.eval("__extra") == pytest.approx(12500 * 375, rel=1e-9)
    assert log.header.listeners == "off"


def test_wrap_listeners_reaches_the_intercepts_upvalue():
    """WRAP_LISTENERS: per-subscriber attribution, the v2 mode.

    `intercepts` is a file-local in axr_main.script, so the only way to the
    individual listeners is `debug.getupvalue` on make_callback.  This proves
    that works on the real file, that the swapped keys still dispatch (both
    listener shapes), that callback_unset still finds a wrapped listener, and
    that the per-listener times add up to the per-name ones.
    """
    if not REAL_AXR.is_file():
        pytest.skip(f"no axr_main.script to load: {REAL_AXR}")
    src = _lua_source().replace(
        "local WRAP_LISTENERS  = false", "local WRAP_LISTENERS  = true", 1)
    assert "local WRAP_LISTENERS  = true" in src, "the WRAP_LISTENERS switch moved"
    lua = _real_axr_runtime(src)
    lua.eval("on_game_start")()
    lua.eval("__drive")(12500)

    log = _profiler.parse(lua.eval("__dumped")())
    assert log.errors == []
    # "4:ok" - three listeners on the two update callbacks plus nested_thing's
    assert log.header.listeners.endswith(":ok"), log.header.raw
    assert int(log.header.listeners.split(":")[0]) == 4

    rows = {r["name"]: r for r in log.ranking(top=None, drop_first=0, listeners=True)}
    by_cb = {}
    for label, r in rows.items():
        by_cb.setdefault(label.split("#")[0], 0.0)
        by_cb[label.split("#")[0]] += r["ms_per_frame"]
    # inclusive and top-level, exactly like the name level: actor_on_update's
    # listener carries its own 200 us plus the 50 us of the nested_thing
    # dispatch it triggers, and that nested listener is not timed again
    assert by_cb["actor_on_update"] == pytest.approx(0.250, abs=0.02)
    assert "nested_thing" not in by_cb   # only ever reached nested, so never timed
    assert by_cb["npc_on_update"] == pytest.approx(0.125, abs=0.02)
    # two distinct subscribers on npc_on_update, separately attributed
    assert len([k for k in rows if k.startswith("npc_on_update#")]) == 2
    # nothing swallowed, still
    assert lua.eval("__extra") == pytest.approx(12500 * 375, rel=1e-9)


def test_wrap_listeners_keeps_callback_unset_working():
    if not REAL_AXR.is_file():
        pytest.skip(f"no axr_main.script to load: {REAL_AXR}")
    src = _lua_source().replace(
        "local WRAP_LISTENERS  = false", "local WRAP_LISTENERS  = true", 1)
    lua = _real_axr_runtime(src)
    lua.eval("on_game_start")()
    # register, drive, unregister with the ORIGINAL function, drive again
    lua.execute("""
        __late = 0
        __late_fn = function() __late = __late + 1; __extra = __extra + 10 end
        RegisterScriptCallback("npc_on_update", __late_fn)
    """)
    lua.eval("__drive")(100)
    assert lua.eval("__late") == 100
    lua.execute('axr_main.callback_unset("npc_on_update", __late_fn)')
    lua.eval("__drive")(100)
    assert lua.eval("__late") == 100, "callback_unset did not find the wrapped listener"


# ---------------------------------------------------------------------------
# the parser on its own
# ---------------------------------------------------------------------------

HAND_DUMP = """
* [x-ray]: starting
ALAOPROF|1|hdr|ts=100000|timer=profile_timer|units_per_ms=2500.000000|calib_ms=250.000|calib_units=625000.000|calib_spin=12|overhead_ns=180.0|make_callback=true|binders=off|dump_ms=30000
~ some unrelated engine warning
ALAOPROF|1|win|seq=1|t0=100000|t1=130000|span_ms=30000|frames=6000|total_units=3000000.000|calls=18000|nested=6000|names=2
ALAOPROF|1|cb|seq=1|name=actor_on_update|calls=6000|units=2000000.000|nested=6000
ALAOPROF|1|cb|seq=1|name=npc_on_update|calls=12000|units=1000000.000|nested=0
ALAOPROF|1|eow|seq=1|frames_total=6000
ALAOPROF|1|win|seq=2|t0=130000|t1=160000|span_ms=30000|frames=6000|total_units=3060000.000|calls=18000|nested=6000|names=2
ALAOPROF|1|cb|seq=2|name=actor_on_update|calls=6000|units=2040000.000|nested=6000
ALAOPROF|1|cb|seq=2|name=npc_on_update|calls=12000|units=1020000.000|nested=0
ALAOPROF|1|eow|seq=2|frames_total=12000
ALAOPROF|1|win|seq=3|t0=160000|t1=190000|span_ms=30000|frames=6000|total_units=2940000.000|calls=18000|nested=6000|names=2
ALAOPROF|1|cb|seq=3|name=actor_on_update|calls=6000|units=1960000.000|nested=6000
ALAOPROF|1|cb|seq=3|name=npc_on_update|calls=12000|units=980000.000|nested=0
ALAOPROF|1|eow|seq=3|frames_total=18000
"""


def test_parser_ignores_engine_noise_and_converts_units():
    log = _profiler.parse(HAND_DUMP)
    assert log.header.units_per_ms == 2500.0
    assert len(log.windows) == 3
    # 3_000_000 units / 2500 = 1200 ms over 6000 frames = 0.2 ms/frame
    assert log.window_ms_per_frame(drop_first=0) == pytest.approx([0.2, 0.204, 0.196])
    assert log.window_ms_per_frame() == pytest.approx([0.204, 0.196])   # first dropped
    rows = log.ranking(drop_first=0)
    assert [r["name"] for r in rows] == ["actor_on_update", "npc_on_update"]
    assert rows[0]["share_pct"] == pytest.approx(66.67, abs=0.1)


def test_parser_drops_incomplete_and_short_windows():
    text = HAND_DUMP + (
        "ALAOPROF|1|win|seq=4|t0=190000|t1=192000|span_ms=2000|frames=400|total_units=200000.000"
        "|calls=1200|nested=400|names=1\n"
        "ALAOPROF|1|cb|seq=4|name=actor_on_update|calls=400|units=200000.000|nested=400\n"
    )
    log = _profiler.parse(text)
    assert len(log.windows) == 4
    assert [w.seq for w in log.good_windows(drop_first=0)] == [1, 2, 3]


def test_parser_records_install_failure():
    log = _profiler.parse("ALAOPROF|1|err|install failed: attempt to index a nil value\n")
    assert log.errors and "nil value" in log.errors[0]
    assert log.header is None
    assert log.summary()["usable"] is False


def test_header_without_units_refuses_milliseconds():
    log = _profiler.parse(
        "ALAOPROF|1|hdr|ts=1|timer=os_clock_shim|units_per_ms=nil|overhead_ns=1.0|make_callback=true|binders=off\n"
        "ALAOPROF|1|win|seq=1|t0=1|t1=2|span_ms=30000|frames=6000|total_units=5.0|calls=1|nested=0|names=0\n"
        "ALAOPROF|1|eow|seq=1|frames_total=6000\n"
    )
    assert log.header.units_per_ms is None
    assert log.header.usable is False
    assert log.window_ms_per_frame(drop_first=0) == []
    assert log.to_ms(1234) is None


def test_spread_of_a_constant_series_is_zero():
    assert _profiler.spread([2.0, 2.0, 2.0])["cv_pct"] == 0.0
    assert _profiler.spread([])["n"] == 0
    s = _profiler.spread([1.0, 1.1, 0.9])
    assert s["cv_pct"] == pytest.approx(10.0, abs=0.1)
    assert s["range_pct"] == pytest.approx(20.0, abs=0.1)


def test_compare_runs_over_run_dirs(tmp_path):
    dirs = []
    for i, scale in enumerate((1.0, 1.02, 0.99)):
        d = tmp_path / f"run{i}"
        d.mkdir()
        text = HAND_DUMP.replace("units_per_ms=2500.000000", f"units_per_ms={2500 / scale:.6f}")
        (d / "xray.log").write_text(text, encoding="utf-8")
        (d / "manifest.json").write_text(json.dumps({"arm": "baseline"}), encoding="utf-8")
        dirs.append(d)
    rep = _profiler.compare_runs(dirs, drop_first=1)
    assert rep["n_runs"] == 3
    assert rep["frames"] == 3 * 12000
    assert rep["script_ms_per_frame"]["mean"] == pytest.approx(0.2, abs=0.01)
    assert rep["script_ms_per_frame"]["cv_pct"] < 5.0
    assert rep["ranking"][0]["name"] == "actor_on_update"
    assert rep["ranking"][0]["runs"] == 3


def test_load_run_returns_none_without_a_log(tmp_path):
    assert _profiler.load_run(tmp_path) is None
