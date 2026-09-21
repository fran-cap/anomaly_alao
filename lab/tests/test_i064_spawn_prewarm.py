"""I-064: the spawn prewarm mod (lab/mods/alao-spawn-prewarm).

Differential tests against a stub engine.  The stub models the one engine
behaviour the mod exists for, as read out of the profiler logs of the eight
hitch captures: creating a stalker whose character_profile is a class walks
every <specific_character>, and each one that was never loaded this session
fires DXML's on_specific_character_init once.  5251 of them in the hitch
window, 0 in every other window.

No wall-clock number is asserted: a stub cannot price the engine's xml walk.
What is counted is WHERE the cold loads land (behind the loading screen or in
play), which is deterministic, and that the world ends up the same.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

lupa = pytest.importorskip("lupa.luajit20")

REPO = Path(__file__).resolve().parents[2]
MOD_ROOT = REPO / "lab" / "mods" / "alao-spawn-prewarm"
MOD = MOD_ROOT / "gamedata" / "scripts" / "zzz_alao_spawn_prewarm.script"

N_CHARACTERS = 5303
LOADED_AT_LOAD = 52

PRELUDE = r"""
LOG = {}
function printf(fmt, ...)
    local ok, s = pcall(string.format, fmt, ...)
    LOG[#LOG + 1] = ok and s or fmt
end
printe = printf

-- script callbacks ---------------------------------------------------------
local cbs = {
    on_specific_character_init = {}, actor_on_first_update = {}, on_try_respawn = {},
    squad_on_npc_creation = {}, se_stalker_on_spawn = {}, actor_on_update = {},
}
REGISTERED = {}
function RegisterScriptCallback(name, f)
    REGISTERED[#REGISTERED + 1] = name
    if cbs[name] then table.insert(cbs[name], f) end
end
function SendScriptCallback(name, ...)
    for _, f in ipairs(cbs[name] or {}) do f(...) end
end
TIME_EVENTS = 0
function CreateTimeEvent() TIME_EVENTS = TIME_EVENTS + 1 end

-- configs ------------------------------------------------------------------
SECTIONS = {
    stalker_sim_squad_novice = {npc_random = "sim_default_stalker_0, sim_default_stalker_0, sim_default_stalker_1", faction = "stalker"},
    sim_default_stalker_0 = {character_profile = "sim_default_stalker_0", class = "AI_STL_S"},
    sim_default_stalker_1 = {character_profile = "sim_default_stalker_1", class = "AI_STL_S"},
    esc_story_guy = {character_profile = "esc_story_guy", class = "AI_STL_S", custom_data = "scripts\\esc\\guy.ltx"},
    dog_weak = {class = "SM_DOG_S"},
}
ini_sys = {}
function ini_sys:section_exist(s) return SECTIONS[s] ~= nil end
function ini_sys:r_string_ex(s, k) return SECTIONS[s] and SECTIONS[s][k] or nil end
function parse_names(s)
    local t = {}
    for w in string.gmatch(s, "([%w_%-%.\\]+)") do t[#t + 1] = w end
    return t
end

-- the engine ---------------------------------------------------------------
local N_CHAR, AT_LOAD = ...
CHAR_LOADED = AT_LOAD        -- characters already in the engine's cache
COLD_IN_PLAY = 0             -- cold loads that happened after the loading screen went
IN_PLAY = false
OBJECTS = {}                 -- id -> se_obj
NEXT_ID = 1000
CREATED = {}                 -- every section ever created, in order
ONLINE_EVER = {}             -- id -> true if it ever came online
ALIFE_CNT = 0
FAIL_CREATE = false
ALIFE_FULL = false

local se_mt = {}
se_mt.__index = se_mt
function se_mt:name() return self._name end
function se_mt:section_name() return self._sec end

local sim = {}
function sim:actor() return ACTOR_SE end
function sim:object(id) return OBJECTS[id] end
function sim:create(sec, pos, lvid, gvid)
    if FAIL_CREATE then return nil end
    local se = setmetatable({id = NEXT_ID, _sec = sec, _name = sec .. NEXT_ID, position = pos,
        m_level_vertex_id = lvid, m_game_vertex_id = gvid, online = false, group_id = 65535,
        can_online = true}, se_mt)
    NEXT_ID = NEXT_ID + 1
    OBJECTS[se.id] = se
    CREATED[#CREATED + 1] = sec
    local prof = SECTIONS[sec] and SECTIONS[sec].character_profile
    if prof and string.find(prof, "sim_default_", 1, true) == 1 then
        -- random pick: the engine walks ALL characters, loading the cold ones
        local cold = N_CHAR - CHAR_LOADED
        CHAR_LOADED = N_CHAR
        if IN_PLAY then COLD_IN_PLAY = COLD_IN_PLAY + cold end
        for i = 1, cold do SendScriptCallback("on_specific_character_init", "char" .. i, {}) end
    elseif prof then
        if CHAR_LOADED < N_CHAR then
            CHAR_LOADED = CHAR_LOADED + 1
            if IN_PLAY then COLD_IN_PLAY = COLD_IN_PLAY + 1 end
            SendScriptCallback("on_specific_character_init", prof, {})
        end
    end
    if prof then SendScriptCallback("se_stalker_on_spawn", se) end
    return se
end
function sim:release(se) OBJECTS[se.id] = nil end
function sim:set_switch_online(id, v) OBJECTS[id].can_online = v end
function sim:set_switch_offline(id, v) OBJECTS[id].can_offline = v end
function alife() return sim end
function alife_object(id) return OBJECTS[id] end
function alife_on_limit() return ALIFE_FULL end
function alife_record(se, state) ALIFE_CNT = ALIFE_CNT + (state and 1 or -1) end
function alife_create(sec, pos, lvid, gvid)
    local se = sim:create(sec, pos, lvid, gvid)
    if se then alife_record(se, true) end
    return se
end

ACTOR_SE = {id = 0, position = {x = 1, y = 2, z = 3}, m_level_vertex_id = 11, m_game_vertex_id = 22}
db = {actor = {}, storage = {}}

-- safe_release_manager, the live logic minus the online branch's engine call
safe_release_manager = {}
local to_release = {}
function safe_release_manager.release(se) to_release[se.id] = true end
function safe_release_manager.tick()
    for id in pairs(to_release) do
        local se = OBJECTS[id]
        if se and not se.online then
            alife_record(se, false)
            sim:release(se, true)
        end
        to_release[id] = nil
    end
end

-- one alife tick: anything near the actor that may go online does
function alife_tick()
    for id, se in pairs(OBJECTS) do
        if se.can_online and se.near_actor then se.online = true ONLINE_EVER[id] = true end
    end
    safe_release_manager.tick()
end

-- what try_respawn -> create_squad does, as far as the mod can see it
function respawn(smart, squad_sec, n)
    local flags = {disabled = false}
    SendScriptCallback("on_try_respawn", smart, flags)
    FLAGS_AFTER = flags.disabled
    local squad = setmetatable({id = NEXT_ID, _sec = squad_sec, _name = squad_sec .. NEXT_ID}, se_mt)
    NEXT_ID = NEXT_ID + 1
    OBJECTS[squad.id] = squad
    local members = {}
    local names = parse_names(SECTIONS[squad_sec].npc_random)
    for i = 1, n do
        local se = alife_create(names[(i - 1) % #names + 1], smart.position, 5, 6)
        se.group_id = squad.id
        members[#members + 1] = se
    end
    for _, se in ipairs(members) do SendScriptCallback("squad_on_npc_creation", squad, se, smart) end
    return squad, members
end

function make_smart(id, name)
    return setmetatable({id = id, _name = name, _sec = "smart_terrain", position = {x = 9, y = 9, z = 9}}, se_mt)
end
"""

LOADER = r"""
function(src, name)
    local env = setmetatable({}, {__index = _G})
    local chunk = assert(loadstring(src, "@" .. name))
    setfenv(chunk, env)
    _G[name] = env
    chunk()
    return env
end
"""


def mod_source(**switches) -> str:
    src = MOD.read_bytes().decode("utf-8")
    for k, v in switches.items():
        src, n = re.subn(rf"(?m)^local {k}(\s*)= \S+", lambda m: f"local {k}{m.group(1)}= {v}", src, count=1)
        assert n == 1, k
    return src


class Arm:
    def __init__(self, with_mod: bool, **switches):
        self.lua = lupa.LuaRuntime(unpack_returned_tuples=True)
        self.lua.eval("function(src, ...) return assert(loadstring(src))(...) end")(
            PRELUDE, N_CHARACTERS, LOADED_AT_LOAD)
        self.g = self.lua.globals()
        self.mod = None
        if with_mod:
            self.mod = self.lua.eval(LOADER)(mod_source(**switches), "zzz_alao_spawn_prewarm")
            self.mod.on_game_start()

    def first_update(self):
        self.lua.execute('SendScriptCallback("actor_on_first_update")')
        self.lua.execute("alife_tick()")

    def play(self):
        self.g.IN_PLAY = True

    def respawn(self, n=3, smart_id=77, smart="esc_smart_terrain_5_7"):
        self.lua.execute(f'LAST_SQUAD, LAST_MEMBERS = respawn(make_smart({smart_id}, "{smart}"), "stalker_sim_squad_novice", {n})')
        self.lua.execute("alife_tick()")

    def log(self):
        return [str(x) for x in self.g.LOG.values()]

    def spawn_lines(self):
        return [l for l in self.log() if l.startswith("[alao_spawn")]

    def live_sections(self):
        return sorted(str(o._sec) for o in self.g.OBJECTS.values())


def boot(with_mod: bool, **sw) -> Arm:
    a = Arm(with_mod, **sw)
    a.first_update()
    a.play()
    return a


# ---------------------------------------------------------------------------
# the mod is self-contained
# ---------------------------------------------------------------------------

def test_mod_adds_one_new_script_and_replaces_nothing():
    files = sorted(p.relative_to(MOD_ROOT).as_posix() for p in MOD_ROOT.rglob("*") if p.is_file())
    assert files == ["gamedata/scripts/zzz_alao_spawn_prewarm.script", "meta.ini"]


def test_mod_compiles_under_luajit():
    # also the 60-upvalue check: that limit is a load-time error
    rt = lupa.LuaRuntime()
    ok = rt.eval("function(s) local f, e = loadstring(s) return f ~= nil, e end")(mod_source())
    assert ok[0], ok[1]


def test_mod_wraps_and_replaces_no_game_function():
    src = mod_source()
    code = "\n".join(l.split("--", 1)[0] for l in src.splitlines())
    for owner in ("SIMBOARD", "smart_terrain", "sim_board", "smr_pop", "smr_civil_war",
                  "sim_squad_scripted", "simulation_board", "se_smart_terrain", "_G"):
        assert not re.search(rf"\b{owner}\s*[.:]\s*\w+\s*=[^=]", code), owner
    assert "CreateTimeEvent" not in code      # time events end up in the save
    assert "save_state" not in code and "load_state" not in code


# ---------------------------------------------------------------------------
# what moves
# ---------------------------------------------------------------------------

def test_without_the_mod_the_first_squad_in_play_pays_every_cold_load():
    a = boot(False)
    a.respawn(3)
    assert int(a.g.COLD_IN_PLAY) == N_CHARACTERS - LOADED_AT_LOAD == 5251


def test_with_the_mod_the_cold_loads_land_behind_the_loading_screen():
    a = boot(True)
    assert int(a.g.CHAR_LOADED) == N_CHARACTERS, "prewarm must have walked the list at first update"
    a.respawn(3)
    assert int(a.g.COLD_IN_PLAY) == 0


def test_second_squad_is_warm_either_way():
    for with_mod in (False, True):
        a = boot(with_mod)
        a.respawn(2)
        before = int(a.g.COLD_IN_PLAY)
        a.respawn(4, smart_id=78, smart="esc_smart_terrain_6_8")
        assert int(a.g.COLD_IN_PLAY) == before


# ---------------------------------------------------------------------------
# what must not move
# ---------------------------------------------------------------------------

def test_world_is_the_same_with_and_without_the_mod():
    a, b = boot(False), boot(True)
    a.respawn(3)
    b.respawn(3)
    assert a.live_sections() == b.live_sections()
    # the squads the game asked for got the members the game asked for
    assert [str(m._sec) for m in a.g.LAST_MEMBERS.values()] == [str(m._sec) for m in b.g.LAST_MEMBERS.values()]
    assert int(a.g.ALIFE_CNT) == int(b.g.ALIFE_CNT)


def test_throwaway_is_pinned_offline_before_any_tick_and_released_on_the_next():
    a = Arm(True)
    a.lua.execute('SendScriptCallback("actor_on_first_update")')
    st = a.mod._alao_state()
    tid = int(st.last.id)
    se = a.g.OBJECTS[tid]
    assert se is not None and se.can_online is False and se.can_offline is True
    se.near_actor = True                     # it is created at the actor's feet
    a.lua.execute("alife_tick()")
    assert a.g.OBJECTS[tid] is None, "safe_release_manager must have taken it"
    assert a.g.ONLINE_EVER[tid] is None, "the throwaway must never come online"
    assert int(a.g.ALIFE_CNT) == 0


def test_on_try_respawn_flags_are_left_alone():
    a = boot(True)
    a.respawn(2)
    assert a.g.FLAGS_AFTER is False


def test_no_per_frame_listener_and_no_time_events():
    a = boot(True)
    a.respawn(3)
    names = [str(n) for n in a.g.REGISTERED.values()]
    assert sorted(names) == ["actor_on_first_update", "on_specific_character_init",
                             "on_try_respawn", "squad_on_npc_creation"]
    assert int(a.g.TIME_EVENTS) == 0


# ---------------------------------------------------------------------------
# the log lines: read them before crediting the mod
# ---------------------------------------------------------------------------

def test_install_and_prewarm_lines():
    a = boot(True)
    lines = a.spawn_lines()
    assert lines[0] == "[alao_spawn 1.0] installed: prewarm=true report=true"
    assert re.fullmatch(
        r"\[alao_spawn\] prewarm: created and released sim_default_stalker_0 \(id \d+\) in \d+ ms "
        r"behind the loading screen, 5251 cold character loads absorbed", lines[1]), lines[1]


def test_report_line_per_squad_says_zero_cold_loads_with_the_prewarm():
    a = boot(True)
    a.respawn(3)
    a.respawn(1, smart_id=78, smart="esc_smart_terrain_6_8")   # the next try_respawn flushes the report
    rep = [l for l in a.spawn_lines() if l.startswith("[alao_spawn] squad ")]
    assert len(rep) == 1
    assert re.fullmatch(r"\[alao_spawn\] squad stalker_sim_squad_novice\d+ at smart esc_smart_terrain_5_7: "
                        r"3 npcs, \d+ ms from try_respawn to the last member, 0 cold character loads", rep[0]), rep[0]


def test_report_line_shouts_when_a_spawn_was_cold():
    a = boot(True, PREWARM_CHARACTERS="false")
    assert not any("prewarm:" in l for l in a.spawn_lines())
    a.respawn(3)
    a.mod._alao_flush()
    rep = [l for l in a.spawn_lines() if l.startswith("[alao_spawn] squad ")]
    assert "5251 cold character loads (COLD: the prewarm did not cover this spawn)" in rep[0]


def test_warm_prewarm_says_not_to_credit_it():
    # level change in the same session: the engine cache is already full
    a = boot(True)
    a.first_update()
    pre = [l for l in a.spawn_lines() if "prewarm:" in l]
    assert len(pre) == 2
    assert "0 cold character loads absorbed (already warm" in pre[1]
    assert "do NOT credit" in pre[1]


def test_reports_are_capped():
    a = boot(True, MAX_REPORTS="2")
    for i in range(6):
        a.respawn(1, smart_id=100 + i, smart=f"smart_{i}")
    rep = [l for l in a.spawn_lines() if l.startswith("[alao_spawn] squad ")]
    assert len(rep) == 2


def test_no_report_while_there_is_no_actor():
    # new game: fill_start_position creates hundreds of squads before the actor exists
    a = Arm(True)
    a.lua.execute("db.actor = nil")
    a.respawn(3)
    a.mod._alao_flush()
    assert not [l for l in a.spawn_lines() if l.startswith("[alao_spawn] squad ")]


# ---------------------------------------------------------------------------
# kill switch and containment
# ---------------------------------------------------------------------------

def test_kill_switch_registers_nothing():
    a = boot(True, ENABLED="false")
    assert [str(n) for n in a.g.REGISTERED.values()] == []
    assert a.spawn_lines() == ["[alao_spawn 1.0] installed but DISABLED by its kill switch"]
    assert [str(s) for s in a.g.CREATED.values()] == []


def test_report_switch_off_keeps_the_prewarm():
    a = boot(True, REPORT_SPAWNS="false")
    assert int(a.g.CHAR_LOADED) == N_CHARACTERS
    a.respawn(3)
    a.mod._alao_flush()
    assert not [l for l in a.spawn_lines() if l.startswith("[alao_spawn] squad ")]


def test_candidate_must_be_a_class_profile_without_custom_data():
    a = Arm(True)
    a.lua.execute("""
        SECTIONS.stalker_sim_squad_novice.npc_random = "esc_story_guy, dog_weak, missing_section, sim_default_stalker_1"
    """)
    a.first_update()
    assert [str(s) for s in a.g.CREATED.values()] == ["sim_default_stalker_1"]


def test_no_candidate_means_no_spawn_and_a_line_saying_so():
    a = Arm(True)
    a.lua.execute("SECTIONS.sim_default_stalker_0 = nil SECTIONS.sim_default_stalker_1 = nil")
    a.first_update()
    assert [str(s) for s in a.g.CREATED.values()] == []
    assert "[alao_spawn] prewarm skipped: no sim_default stalker section found" in a.spawn_lines()


def test_full_id_storage_skips_the_prewarm():
    a = Arm(True)
    a.g.ALIFE_FULL = True
    a.first_update()
    assert [str(s) for s in a.g.CREATED.values()] == []
    assert any("almost full" in l for l in a.spawn_lines())


def test_failed_create_is_reported_and_nothing_is_released():
    a = Arm(True)
    a.g.FAIL_CREATE = True
    a.first_update()
    assert any(l.startswith("[alao_spawn] prewarm FAILED: alife_create(sim_default_stalker_0)") for l in a.spawn_lines())


def test_a_failed_deferred_release_never_leaves_the_throwaway_behind():
    a = Arm(True)
    a.lua.execute("safe_release_manager.release = function() error('boom') end")
    a.lua.execute('SendScriptCallback("actor_on_first_update")')      # must not raise
    assert any("deferred release failed" in l and "boom" in l for l in a.spawn_lines())
    assert a.live_sections() == [], "released on the spot instead"
    assert int(a.g.ALIFE_CNT) == 0


def test_an_error_inside_the_prewarm_is_contained():
    a = Arm(True)
    a.lua.execute("parse_names = function() error('boom') end")
    a.first_update()                         # must not raise
    assert any("prewarm ERROR (contained)" in l and "boom" in l for l in a.spawn_lines())
    assert [str(s) for s in a.g.CREATED.values()] == []
