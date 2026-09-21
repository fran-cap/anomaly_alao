"""I-066: alao-vmm-cache must be invisible except in the crossing count.

Diff-execution: the same scripted session runs in two fresh LuaJIT 2.0 runtimes,
one stock and one with zzz_alao_vmm_cache installed, against every copy of
visual_memory_manager.script a GAMMA modlist carries (and the ALAO rewrite the
in-game arms load).  Return values are compared at %.17g, errors by message,
the printf log line for line (ours filtered out).
"""
from __future__ import annotations

import pytest

lupa = pytest.importorskip("lupa.luajit20")

import i066_vmm_harness as H  # noqa: E402

# One scripted session.  Deterministic LCG so both runtimes see the same thing.
SESSION = r"""
local seed = ...
local function rnd(n) seed = (seed * 1103515245 + 12345) % 2147483648 return seed % n end
local function pick(t) return t[rnd(#t) + 1] end

local NPCS = {
    new_obj(11, "stalker", true, 0.25), new_obj(12, "stalker", true, 0.6),
    new_obj(13, "monster", true, 0.1),  new_obj(14, "stalker", false, 0.2),
}
NPCS[1]._torch = setmetatable({_on = true}, getmetatable(ACTOR))
NPCS[2]._torch = setmetatable({_on = false}, getmetatable(ACTOR))
db.storage[11] = {active_scheme = "guard"}
db.storage[12] = {active_scheme = "walker"}
local WHOS = { ACTOR, ACTOR, ACTOR, NPCS[1], NPCS[3], NPCS[4] }

local DIST  = {0, -1, 0.5, 50, 91, 150}
local ODIST = {1, 5, 24.9, 30, 60, 120}
local VEL   = {0, 0, 1.5, 4}
local LUM   = {0, -0.5, 0.05, 0.4, 1}
local TQ    = {0.001, 0.0005, 0.01}
local VF    = {0.5, 0.85}
local MCMV  = {0.1, 0.5, 1, 1.7, 3}
local NUMKEYS = {"memory", "luminocity", "distance", "velocity", "weight", "crouch", "low_crouch"}

local out, n = {}, 0
local function emit(s) n = n + 1 out[n] = s end

for step = 1, 1500 do
    local r = rnd(100)
    if r < 70 then
        local npc = pick(NPCS)
        if rnd(12) == 0 then npc = nil end
        local who = pick(WHOS)
        local ok, v = pcall(engine_call, npc, who, 0.016, pick(TQ), pick(LUM), pick(VF), pick(VEL),
                            pick(DIST), pick(ODIST), 2)
        emit(ok and string.format("%.17g", v) or ("ERR " .. tostring(v)))
    elseif r < 74 then
        STATE.crouch = rnd(2) == 0
        STATE.accel = rnd(2) == 0
    elseif r < 77 then
        STATE.hours = pick({2, 4, 12, 22})
        STATE.tg = STATE.tg + rnd(5000)
    elseif r < 80 then
        STATE.torch_on = rnd(2) == 0
        level_weathers.bLevelUnderground = rnd(3) == 0
        camp_lum.luminocity_inc = pick({0, 0.2})
    elseif r < 83 then
        local a, b = pick(NPCS), pick(WHOS)
        a._sees[b._id] = rnd(2) == 0
        a._memt = STATE.tg - rnd(60000) - 1
    elseif r < 86 then
        -- the player presses Apply in MCM
        mcm_apply({ ["stealth/" .. pick(NUMKEYS)] = pick(MCMV),
                    ["stealth/michiko_patch"] = rnd(2) == 0 })
        emit("apply")
    elseif r < 88 then
        -- some script pokes a value in without the menu
        ui_mcm.set("stealth/" .. pick(NUMKEYS), pick(MCMV))
        emit("set")
    elseif r < 89 then
        ui_mcm.set("other/thing", rnd(9))
    elseif r < 90 then
        -- the vanilla options menu sends the same callback with no argument
        SendScriptCallback("on_option_change")
    elseif r < 92 then
        SendScriptCallback("load_state", {})
    elseif r < 94 then
        -- a key MCM does not know: must complain every single time
        -- (zero return values today, so tostring() of it is an error; keep it one)
        local ok, s = pcall(function() return tostring(stealth_mcm.get_config("no_such_key")) end)
        emit((ok and "OK " or "ERR ") .. tostring(s))
        emit("nret " .. select("#", stealth_mcm.get_config("no_such_key")))
    elseif r < 96 then
        emit(tostring(stealth_mcm.get_config("icon")))
    elseif r < 98 then
        mcm_apply({ ["stealth/debugx"] = rnd(3) == 0 })
        STATE.target = pick({NPCS[1], NPCS[2]})
        if rnd(3) == 0 then STATE.target = nil end
    else
        fire_time_events()
    end
end
return table.concat(out, "\n")
"""


def run_session(vmm_src, patched, seed, jit=True):
    lua = H.build(vmm_src, patched=patched, jit=jit)
    res = lua.execute("return (function(...) %s end)(%d)" % (SESSION, seed))
    g = lua.globals()
    return {"out": str(res), "log": H.log_lines(lua, ours=False), "ours": H.log_lines(lua, ours=True),
            "cross": int(g["__cross"]), "cross_mcm": int(g["__cross_mcm"]), "lua": lua}


needs_corpus = pytest.mark.skipif(not H.STEALTH_MCM.is_file(),
                                  reason="needs the extracted GAMMA corpus (extracted/gamma)")


@needs_corpus
@pytest.mark.parametrize("copy", sorted(H.VMM_COPIES))
@pytest.mark.parametrize("jit", [True, False], ids=["jit", "interp"])
def test_diff_execute_every_vmm_copy(copy, jit):
    path = H.VMM_COPIES[copy]
    if not path.is_file():
        pytest.skip("missing %s" % path)
    src = H.read_script(path)
    for seed in (1, 7, 20260920):
        a = run_session(src, False, seed, jit)
        b = run_session(src, True, seed, jit)
        assert a["out"] == b["out"], "return values diverge for %s seed %d" % (copy, seed)
        assert a["log"] == b["log"], "log diverges for %s seed %d" % (copy, seed)
        assert a["out"].count("\n") > 900
        assert "ERR" in a["out"], "the grid should include the npc/who shapes that error today"
        # the saving is the MCM crossings and nothing else
        assert b["cross"] - b["cross_mcm"] == a["cross"] - a["cross_mcm"]
        assert b["cross_mcm"] < a["cross_mcm"] * 0.25


@needs_corpus
def test_live_copy_does_seven_reads_standing_eight_crouching():
    src = H.read_script(H.VMM_COPIES["live-atmospherics"])
    lua = H.build(src, patched=False)
    lua.execute("""
        NPC = new_obj(11, "stalker", true, 0.25)
        function one() local c = __cross_mcm engine_call(NPC, ACTOR, 0.016, 0.001, 0.4, 0.5, 0, 50, 30, 2) return __cross_mcm - c end
    """)
    one = lua.globals()["one"]
    assert one() == 14                      # 7 reads x (line_exist + r_string)
    lua.execute("STATE.crouch = true")
    assert one() == 16
    lua.execute("STATE.accel = true")
    assert one() == 16                      # low_crouch instead of crouch, short-circuit

    lua = H.build(src, patched=True)
    lua.execute("""
        NPC = new_obj(11, "stalker", true, 0.25)
        function one() local c = __cross_mcm engine_call(NPC, ACTOR, 0.016, 0.001, 0.4, 0.5, 0, 50, 30, 2) return __cross_mcm - c end
    """)
    one = lua.globals()["one"]
    assert one() == 14                      # first call fills
    assert one() == 0
    lua.execute("STATE.crouch = true")
    assert one() == 2                       # crouch seen for the first time
    assert one() == 0


def accessor(patched=True, **kw):
    return H.build(None, patched=patched, stealth_src=H.STEALTH_MCM_SHAPE, **kw)


def test_install_log_line_names_the_function():
    lua = accessor()
    ours = H.log_lines(lua, ours=True)
    assert len(ours) == 1
    assert "patched stealth_mcm.get_config" in ours[0]
    assert "ui_mcm.set wrapped for stealth/ ids" in ours[0]
    st = lua.eval("zzz_alao_vmm_cache.stats()")
    assert st["installed"] and st["set_hooked"]


def test_false_is_cached_nil_is_not():
    lua = accessor()
    get = lua.eval("stealth_mcm.get_config")
    cross = lambda: int(lua.globals()["__cross_mcm"])  # noqa: E731
    assert get("michiko_patch") is False
    c = cross()
    assert get("michiko_patch") is False
    assert cross() == c, "a false value must be a cache hit (the I-056 trap)"
    before = len(H.log_lines(lua, ours=False))
    assert get("nope") is None and get("nope") is None
    assert lua.eval('select("#", stealth_mcm.get_config("nope"))') == 0, "bare return = zero values, like ui_mcm.get"
    assert len(H.log_lines(lua, ours=False)) == before + 3, "bad path must complain every time"


def test_apply_takes_effect_on_the_next_read_and_logs_one_refresh():
    lua = accessor()
    get = lua.eval("stealth_mcm.get_config")
    assert get("memory") == 1
    lua.execute('mcm_apply({["stealth/memory"] = 2.5})')
    assert get("memory") == 2.5
    ours = H.log_lines(lua, ours=True)
    assert sum("cache refresh #1 (on_option_change)" in s for s in ours) == 1
    assert sum("cached stealth/memory = 2.5" in s for s in ours) == 1


def test_ui_mcm_set_refreshes_only_for_stealth_ids():
    lua = accessor()
    get = lua.eval("stealth_mcm.get_config")
    assert get("weight") == 1
    lua.execute('ui_mcm.set("other/thing", 5)')
    assert lua.eval("zzz_alao_vmm_cache.stats().refreshes") == 0
    assert lua.eval('INI["other/thing"]') == "5", "the wrapped set must still write"
    lua.execute('ui_mcm.set("stealth/weight", 3)')
    assert lua.eval("zzz_alao_vmm_cache.stats().refreshes") == 1
    assert get("weight") == 3


def test_missing_ini_line_writes_the_default_once_like_today():
    lua = accessor()
    lua.execute('INI["stealth/memory"] = nil')
    get = lua.eval("stealth_mcm.get_config")
    assert get("memory") == 1
    assert lua.eval('INI["stealth/memory"]') == "1"
    assert get("memory") == 1


def test_without_mcm_the_defaults_come_back():
    a = accessor(patched=False, with_ui_mcm=False)
    b = accessor(patched=True, with_ui_mcm=False)
    for key in ("memory", "crouch", "icon", "michiko_patch", "nope"):
        assert a.eval("stealth_mcm.get_config")(key) == b.eval("stealth_mcm.get_config")(key)
    assert b.eval("zzz_alao_vmm_cache.stats().set_hooked") is False


def test_install_twice_never_wraps_the_wrapper():
    lua = accessor()
    lua.execute("FIRST = stealth_mcm.get_config")
    assert lua.eval("zzz_alao_vmm_cache.install()") is True
    assert lua.eval("rawequal(stealth_mcm.get_config, FIRST)")
    # a second copy of the script (somebody shipping it under another name)
    lua.globals()["load_script"]("zzz_alao_vmm_cache_copy", H.read_script(H.MOD_SCRIPT))
    assert lua.eval("zzz_alao_vmm_cache_copy.install()") is False
    assert lua.eval("rawequal(stealth_mcm.get_config, FIRST)")
    assert any("already carries" in s for s in H.log_lines(lua, ours=True))


def test_kill_switch():
    src = H.read_script(H.MOD_SCRIPT).replace("local ENABLED = true", "local ENABLED = false")
    assert "local ENABLED = false" in src
    lua = H.build(None, patched=True, stealth_src=H.STEALTH_MCM_SHAPE, mod_src=src)
    assert lua.eval("zzz_alao_vmm_cache.stats().installed") is False
    assert lua.eval('rawget(stealth_mcm, "alao_vmm_cache")') is None
    assert any("kill switch is off" in s for s in H.log_lines(lua, ours=True))


def test_no_stealth_overhaul_is_a_log_line_not_a_crash():
    lua = lupa.LuaRuntime()
    lua.execute(H.PRELUDE)
    lua.globals()["load_script"]("zzz_alao_vmm_cache", H.read_script(H.MOD_SCRIPT))
    lua.execute("zzz_alao_vmm_cache.on_game_start()")
    assert any("stealth_mcm.get_config not found" in s for s in H.log_lines(lua, ours=True))


def test_error_during_gathering_still_propagates_on_a_miss():
    lua = accessor()
    lua.execute("ui_mcm.__gathering(true)")
    ok = lua.eval('pcall(stealth_mcm.get_config, "memory")')
    assert ok is False or ok[0] is False


def test_script_compiles_and_stays_under_the_upvalue_limit():
    src = H.read_script(H.MOD_SCRIPT)
    lua = lupa.LuaRuntime()
    assert lua.eval("function(s) return loadstring(s) ~= nil end")(src)
    assert MOD_CRLF(), "files in this repo are CRLF"


def MOD_CRLF():
    raw = H.MOD_SCRIPT.read_bytes()
    return raw.count(b"\r\n") == raw.count(b"\n")
