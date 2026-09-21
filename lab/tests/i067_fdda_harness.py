"""Stub-engine harness for the I-067 FDDA prewarm (alao-prewarm v1.4).

Same idea as `i063_prewarm_harness`: a stub Anomaly engine in `lupa.luajit20`,
the REAL FDDA Redone scripts on top of it when the GAMMA install is there
(`lam2`, `liz_fdda_redone_consumables`, `liz_fdda_redone_backpack`), and
`zzz_alao_prewarm_fdda.script` as the thing under test.

The engine model is a ledger of RESOURCE TOUCHES, keyed the way the engine keys
its caches (xray-monolith player_hud.cpp / level_script.cpp):

    motions:<hud section>   player_hud::get_hand_motions - touched by BOTH
                            game.get_motion_length and game.play_hud_motion
    model:<item_visual>     ::Render->model_Create - touched by play_hud_motion
                            and by game.motion_exists
    sound:<path>            the path-keyed sound source - sound_object(path)
    camfile:<path>          the .anm on disk - level.add_cam_effector reads it,
                            and so does io.open():read()

Each touch is recorded with `cold` (first touch of that key this session) and
the frame.  The prewarm's whole job is to move cold touches out of play, so the
tests count them; nothing here is a timing.

EFFECTS is the other ledger: everything the player could see or hear - a hud
motion started, a cam effector added, a sound PLAYED.  The prewarm must add
nothing to it.
"""
from __future__ import annotations

from pathlib import Path

from lupa import luajit20 as lupa

REPO = Path(__file__).resolve().parent.parent.parent
MOD_DIR = REPO / "lab" / "mods" / "alao-prewarm" / "gamedata" / "scripts"
MOD = MOD_DIR / "zzz_alao_prewarm_fdda.script"

FDDA = Path(r"D:\GOG_Games\Gamma\S.T.A.L.K.E.R. GAMMA\GAMMA\mods"
            r"\447- FDDA Redone - lizzardman\gamedata\scripts")
LIVE = {n: FDDA / f"{n}.script"
        for n in ("lam2", "liz_fdda_redone_consumables", "liz_fdda_redone_backpack")}


def live_available() -> bool:
    return all(p.is_file() for p in LIVE.values())


PRELUDE = r"""
FRAME = 0
TOUCH, TN = {}, 0
WARM = {}
EFFECTS, EFFN = {}, 0
LOG, LOGN = {}, 0
MISSING_MESH = {}          -- visual file -> true: getFS():exist says no
PACKED_ANM = {}            -- cam path -> true: io.open fails (inside a .db)
BAD_SOUND = {}
HAS = {motion_exists = true, get_motion_length = true, io = true}

function effect(s) EFFN = EFFN + 1 EFFECTS[EFFN] = s end
function touch(key)
    TN = TN + 1
    TOUCH[TN] = {key = key, cold = not WARM[key], frame = FRAME}
    WARM[key] = true
end

-- Anomaly's printf / strformat only substitutes %s.  Anything else in a format
-- string would print literally in game, so the stub makes it an error.
function strformat(fmt, ...)
    local args, i = {...}, 0
    local bad = string.match(string.gsub(fmt, "%%s", ""), "%%[^%%]")
    if bad then error("printf directive the game does not support: " .. bad .. " in: " .. fmt, 0) end
    return (string.gsub(fmt, "%%s", function() i = i + 1 return tostring(args[i]) end))
end
function printf(fmt, ...) LOGN = LOGN + 1 LOG[LOGN] = strformat(fmt, ...) end

-- callbacks
CALLBACKS = {}
function RegisterScriptCallback(name, f)
    CALLBACKS[name] = CALLBACKS[name] or {}
    local t = CALLBACKS[name]
    for i = 1, #t do if t[i] == f then return end end
    t[#t + 1] = f
end
function UnregisterScriptCallback(name, f)
    local t = CALLBACKS[name]
    if not t then return end
    for i = 1, #t do if t[i] == f then table.remove(t, i) return end end
end
function fire(name, ...)
    local t = CALLBACKS[name]
    if not t then return 0 end
    local copy = {}
    for i = 1, #t do copy[i] = t[i] end
    for i = 1, #copy do copy[i](...) end
    return #copy
end
function listeners(name) return CALLBACKS[name] and #CALLBACKS[name] or 0 end

-- ini: sections are flat tables, inheritance already resolved by the test
SYS, EFF = {}, {}
local function mk_ini(tbl)
    local o = {}
    function o:section_exist(s) return tbl[s] ~= nil end
    function o:line_exist(s, k) return tbl[s] ~= nil and tbl[s][k] ~= nil end
    function o:r_string_ex(s, k) local v = tbl[s] and tbl[s][k] return v ~= nil and tostring(v) or nil end
    function o:r_float_ex(s, k) local v = tbl[s] and tbl[s][k] return v ~= nil and tonumber(v) or nil end
    function o:r_bool_ex(s, k) local v = tbl[s] and tbl[s][k] if v == nil then return nil end return v == true or v == "true" end
    return o
end
ini_sys = mk_ini(SYS)
INI_OPENS = 0
function ini_file(path)
    INI_OPENS = INI_OPENS + 1
    if path == "items\\items\\animations_settings.ltx" then return mk_ini(EFF) end
    return mk_ini({})
end

-- engine: hud motions
game = {}
function game.get_motion_length(sec, anm, speed)
    if not SYS[sec] then effect("!script motion section missing " .. sec) return 0 end
    touch("motions:" .. sec)
    return 2000
end
function game.play_hud_motion(hand, sec, anm, mix, speed)
    effect("play_hud_motion " .. sec .. " " .. anm)
    local v = SYS[sec] and SYS[sec].item_visual
    if v then touch("model:" .. v) end
    touch("motions:" .. sec)
    return 2000
end
function game.stop_hud_motion() effect("stop_hud_motion") end
function game.motion_exists(visual, motion)
    local file = visual
    if not string.find(file, "%.ogf$") then file = file .. ".ogf" end
    if MISSING_MESH[file] then error("FATAL: model_Create on a missing file " .. file, 0) end
    touch("model:" .. visual)
    return true
end
function game.set_actor_allow_ladder(b) end
function game.translate_string(s) return s end

level = {}
function level.add_cam_effector(cam, id, cyclic, cb, fov, b)
    effect("add_cam_effector " .. cam)
    touch("camfile:" .. cam)
end
function level.remove_cam_effector(id) end
function level.disable_input() end
function level.enable_input() end
function level.object_by_id(id) return nil end

-- sounds: `sound_object` is called AND indexed (sound_object.s2d)
local sndmt = {}
sndmt.__index = sndmt
function sndmt:play(who, d, mode) effect("play " .. self.path) end
function sndmt:stop() effect("stop " .. self.path) end
sound_object = setmetatable({s2d = 1}, {__call = function(_, path)
    if BAD_SOUND[path] then error("no such sound " .. path, 0) end
    touch("sound:" .. path)
    return setmetatable({path = path}, sndmt)
end})

-- file system
local fs = {}
function fs:exist(root, file) if MISSING_MESH[file] then return nil end return {} end
function fs:update_path(root, file) return "x:\\gamedata\\anims\\" .. file end
function getFS() return fs end
REAL_IO = io
FAKE_IO = {open = function(path, mode)
    local cam = string.gsub(path, "^x:\\gamedata\\anims\\", "")
    if PACKED_ANM[cam] then return nil end
    local f = {}
    function f:read(what) touch("camfile:" .. cam) return "" end
    function f:close() end
    return f
end}
io = FAKE_IO

-- the actor and its ruck
RUCK = {}          -- array of sections
local function mk_item(sec, id)
    local o = {}
    function o:section() return sec end
    function o:id() return id end
    return o
end
ACTOR = {}
function ACTOR:iterate_inventory(f, who)
    for i = 1, #RUCK do f(who, mk_item(RUCK[i], 1000 + i)) end
end
function ACTOR:item_in_slot(n) if n == 13 then return mk_item("backpack_x", 77) end return nil end
function ACTOR:object(sec) return nil end
function ACTOR:active_slot() return 0 end
function ACTOR:active_detector() return nil end
function ACTOR:eat(o) effect("eat") end
db = {actor = ACTOR}
COMMUNITY = "actor_stalker"
function character_community(o) return COMMUNITY end
function take(sec) return fire("actor_on_item_take", mk_item(sec, 5000)) end
function new_item(sec) return mk_item(sec, 6000) end

-- what the real FDDA scripts need at load
MCM = {["backpack/enable"] = true, ["consumables/enable"] = true,
       ["backpack/max_speed"] = 1.5, ["backpack/min_weight_multiplier"] = 50,
       ["backpack/type"] = 0}
liz_fdda_redone_mcm = {get_config = function(k) return MCM[k] end}
ui_pda_npc_tab = {use_view = function() end}
actor_effects = {play_item_fx = function() end, item_not_in_use = true}
itms_manager = {actor_on_item_before_use = function() end}
ui_inventory = {GUI = nil, UIInventory = {ParseInventory = function() return {} end}}
rax_persistent_highlight = {register = function() end}
lam_fov_manager = {set_fov = function() end, restore_fov = function() end}
liz_fdda_input_manager = {is_input_disabled = false}
ui_options = {get = function() return true end}
actor_menu = {set_msg = function() end}
function GetARGB(a, r, g, b) return 0 end
function IsMoveState(s) return false end
function alife_create_item(sec, who) effect("alife_create_item " .. sec) end
function alife_release(o) end
function callstack() end
function clamp(v, a, b) if v < a then return a elseif v > b then return b end return v end
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

# a small, faithful slice of the shipped FDDA configs, inheritance flattened
BACKPACK_OPEN = {"snd": r"interface\item_usage\backpack_open", "anm": "item_ea_backpack_open",
                 "cam": r"itemuse_anm_effects\backpack_open.anm", "tm": 1600}
BACKPACK_CLOSE = {"snd": r"interface\item_usage\backpack_close", "anm": "item_ea_backpack_close",
                  "cam": r"itemuse_anm_effects\backpack_open.anm", "tm": 1600}
EFF = {
    "backpack_open_stalker": dict(BACKPACK_OPEN, anm="item_ea_backpack_open_stalker"),
    "backpack_close_stalker": dict(BACKPACK_CLOSE, anm="item_ea_backpack_close_stalker"),
    "backpack_open_dolg": dict(BACKPACK_OPEN, anm="item_ea_backpack_open_dolg"),
    "backpack_close_dolg": dict(BACKPACK_CLOSE, anm="item_ea_backpack_close_dolg"),
    "vodka": {"snd": r"interface\item_usage\vodka_use", "anm": "item_ea_cmuphob_vodka",
              "cam": r"itemuse_anm_effects\vodka_use.anm", "tm": 4300},
    "bread": {"snd": r"interface\item_usage\food_use", "anm": "item_ea_bread",
              "cam": r"itemuse_anm_effects\eat_kolbasa_d_use_h.anm", "tm": 7000},
    "sausage": {"snd": r"interface\item_usage\food_use", "anm": "item_ea_kolbasa",
                "cam": r"itemuse_anm_effects\eat_kolbasa_d_use_h.anm", "tm": 7000},
    "medkit": {"snd": r"interface\item_usage\medkit_use", "anm": "item_ea_medkit",
               "cam": r"itemuse_anm_effects\medkit_use.anm", "tm": 5000},
    "fdda_error_snd": {"snd": r"interface\item_usage\error_sound"},   # no anm: not a target
}
BP = r"dynamics\weapons\wpn_eat\backpack\wpn_backpack_act_hud"
SYS = {
    "item_ea_backpack_open_stalker_hud": {"item_visual": BP + "_stalker.ogf"},
    "item_ea_backpack_close_stalker_hud": {"item_visual": BP + ".ogf"},
    "item_ea_backpack_open_dolg_hud": {"item_visual": BP + "_dolg.ogf"},
    "item_ea_backpack_close_dolg_hud": {"item_visual": BP + ".ogf"},
    "item_ea_cmuphob_vodka_hud": {"item_visual": r"dynamics\weapons\wpn_eat\vodka_hud"},
    "item_ea_bread_hud": {"item_visual": r"dynamics\weapons\wpn_eat\bread_hud"},
    "item_ea_kolbasa_hud": {"item_visual": r"dynamics\weapons\wpn_eat\kolbasa_hud"},
    "item_ea_medkit_hud": {"item_visual": r"dynamics\weapons\wpn_eat\aptechki\wpn_aptechka_basic_hud"},
}


class Arm:
    """One stub engine, optionally with the prewarm installed."""

    def __init__(self, with_mod: bool = True, live: bool = False, ruck=None,
                 edit=None, community: str = "actor_stalker"):
        self.rt = lupa.LuaRuntime(unpack_returned_tuples=True)
        self.rt.execute(PRELUDE)
        g = self.rt.globals()
        fill = self.rt.eval("function(dst, s, k, v) dst[s] = dst[s] or {} dst[s][k] = v end")
        for name, src in (("EFF", EFF), ("SYS", SYS)):
            for sec, kv in src.items():
                self.rt.eval("function(dst, s) dst[s] = dst[s] or {} end")(g[name], sec)
                for k, v in kv.items():
                    fill(g[name], sec, k, v)
        g.COMMUNITY = community
        push = self.rt.eval("function(s) RUCK[#RUCK + 1] = s end")
        for s in (ruck if ruck is not None else ["vodka", "bread", "ammo_9x18", "bread"]):
            push(s)
        load = self.rt.eval(LOADER)
        self.live = live
        if live:
            for name, path in LIVE.items():
                load(path.read_text(encoding="utf-8", errors="replace"), name)
        else:
            self.rt.execute("lam2 = {} liz_fdda_redone_consumables = {}")
        self.mod = None
        if with_mod:
            src = MOD.read_text(encoding="utf-8")
            if edit:
                src = edit(src)
            self.mod = load(src, "zzz_alao_prewarm_fdda")
        self.rt.execute(
            "for _, n in ipairs({'lam2','liz_fdda_redone_consumables','liz_fdda_redone_backpack',"
            "'zzz_alao_prewarm_fdda'}) do local m = rawget(_G, n) "
            "if m and m.on_game_start then m.on_game_start() end end")

    # -- driving ---------------------------------------------------------
    def lua(self, code):
        return self.rt.eval(code)

    def load_game(self):
        self.rt.execute("fire('on_game_load') FRAME = 7 fire('actor_on_first_update')")

    def frame(self, n=1):
        for _ in range(n):
            self.rt.execute("FRAME = FRAME + 1 fire('actor_on_update', nil, 16)")

    def take(self, sec):
        self.rt.eval("take")(sec)

    def open_inventory(self, frames=10):
        """The live path: GUI_on_show -> try_open_backpack -> lam2 queue."""
        self.rt.execute("ui_inventory.GUI = {mode = 'inventory', IsShown = function() return true end} "
                        "fire('GUI_on_show', 'UIInventory')")
        self.frame(frames)

    def use_item(self, sec, frames=5):
        self.rt.execute(f"liz_fdda_redone_consumables.perform_item_use(new_item('{sec}'))")
        self.frame(frames)

    # -- reading ---------------------------------------------------------
    def touches(self):
        t = self.rt.globals().TOUCH
        return [{"key": t[i].key, "cold": bool(t[i].cold), "frame": t[i].frame}
                for i in range(1, self.rt.globals().TN + 1)]

    def cold_after(self, frame):
        return [t["key"] for t in self.touches() if t["cold"] and t["frame"] > frame]

    def effects(self):
        e = self.rt.globals().EFFECTS
        return [e[i] for i in range(1, self.rt.globals().EFFN + 1)]

    def log(self):
        l = self.rt.globals().LOG
        return [l[i] for i in range(1, self.rt.globals().LOGN + 1)]

    def listeners(self, name):
        return self.rt.eval("listeners")(name)
