"""Stub-engine harness for I-066 (alao-vmm-cache).

Loads the REAL `visual_memory_manager.script` and `stealth_mcm.script` into
lupa.luajit20 on top of a stub Anomaly engine, with or without
`zzz_alao_vmm_cache.script` installed, and drives `get_visible_value` the way
the engine does: by name, through the module table, on every call.

The MCM chain is kept at its real shape because that is the thing being cached:

    stealth_mcm.get_config(key)                      real file
      -> ui_mcm.get("stealth/"..key)                 hot path of the real one
        -> axr_main.config:r_value("mcm", id, typ)   _g_patches new_ini_file_ex
          -> line_exist + r_string                   stubs, counted as crossings

Every stub that stands in for an engine call bumps `__cross`, and the MCM ones
also bump `__cross_mcm`, so a test or the bench can price crossings separately
from the Lua time.  Nothing here is timed.
"""
from __future__ import annotations

from pathlib import Path

from lupa import luajit20 as lupa

REPO = Path(__file__).resolve().parents[2]
MOD_SCRIPT = REPO / "lab" / "mods" / "alao-vmm-cache" / "gamedata" / "scripts" / "zzz_alao_vmm_cache.script"

CORPUS = Path(r"C:\code\GIT\anomaly_alao\extracted\gamma")
STEALTH_MCM = CORPUS / "45- Stealth Overhaul - xcvb" / "gamedata" / "scripts" / "stealth_mcm.script"
VMM_COPIES = {
    # live winner in the GAMMA modlist (prio #35)
    "live-atmospherics": CORPUS / "290- Atmospherics Shaders Weathers and Reshade Latest - Hippobot"
    / "gamedata" / "scripts" / "visual_memory_manager.script",
    "shadowed-crashfix": CORPUS / "G.A.M.M.A. Stealth Crash Fix" / "gamedata" / "scripts"
    / "visual_memory_manager.script",
    "shadowed-xcvb": CORPUS / "45- Stealth Overhaul - xcvb" / "gamedata" / "scripts"
    / "visual_memory_manager.script",
    # what the in-game arms actually load: the ALAO rewrite of the live winner
    "overlay-alao": Path(r"C:\code\GIT\anomaly_alao\lab\coord\overlays\agent-I063-b")
    / "gamedata" / "scripts" / "visual_memory_manager.script",
}

# stealth_mcm.get_config at its shipped shape, for the tests that must run
# without the extracted corpus.
STEALTH_MCM_SHAPE = r"""
local defaults = { ["memory"] = 1, ["luminocity"] = 1, ["distance"] = 1, ["velocity"] = 1,
	["weight"] = 1, ["crouch"] = 0.4, ["low_crouch"] = 0.25, ["icon"] = true,
	["michiko_patch"] = false, ["debugx"] = false, }
function get_config(key)
	if ui_mcm then return ui_mcm.get("stealth/"..key) else return defaults[key] end
end
"""

PRELUDE = r"""
__cross, __cross_mcm = 0, 0
LOG, LOGN = {}, 0
CALLBACKS = {}
TIME_EVENTS = {}

STATE = {
    tg = 100000, hours = 12, minutes = 30,
    underground = false, camp_inc = 0,
    crouch = false, accel = false,
    torch_on = false, flash_sec = "device_torch", detector = false,
    weight = 25,
    sun = {x = 0.4, y = 0.4, z = 0.3}, hemi = {x = 0.2, y = 0.25, z = 0.3},
    target = nil,
}

-- axr_options.ltx, [mcm] section, as the strings the ini holds
INI = {
    ["stealth/memory"] = "1", ["stealth/luminocity"] = "1", ["stealth/distance"] = "1",
    ["stealth/velocity"] = "1", ["stealth/weight"] = "1", ["stealth/crouch"] = "0.4",
    ["stealth/low_crouch"] = "0.25", ["stealth/icon"] = "true",
    ["stealth/michiko_patch"] = "false", ["stealth/debugx"] = "false",
}

local function X() __cross = __cross + 1 end
local function XM() __cross = __cross + 1 __cross_mcm = __cross_mcm + 1 end

function printf(fmt, ...)
    local a = {...}
    for i = 1, select("#", ...) do a[i] = tostring(a[i]) end
    local ok, s = pcall(string.format, tostring(fmt), unpack(a))
    LOGN = LOGN + 1
    LOG[LOGN] = ok and s or tostring(fmt)
end
function printe(fmt, ...) printf(fmt, ...) end
function strformat(fmt, ...) return tostring(fmt) end
function clamp(v, a, b) if v < a then return a end if v > b then return b end return v end
function round_idp(n, idp) local m = 10 ^ (idp or 0) return math.floor(n * m + 0.5) / m end
function time_global() X() return STATE.tg end
function get_console_cmd(t, c) X() return STATE.renderer or "renderer_r4" end
function RegisterScriptCallback(name, f)
    CALLBACKS[name] = CALLBACKS[name] or {}
    table.insert(CALLBACKS[name], f)
end
function UnregisterScriptCallback() end
function SendScriptCallback(name, ...)
    for _, f in ipairs(CALLBACKS[name] or {}) do f(...) end
end
function CreateTimeEvent(a, b, t, f) TIME_EVENTS[#TIME_EVENTS + 1] = f end
function fire_time_events()
    local ev = TIME_EVENTS
    TIME_EVENTS = {}
    for _, f in ipairs(ev) do f() end
end

level = {
    get_time_hours = function() X() return STATE.hours end,
    get_time_minutes = function() X() return STATE.minutes end,
    get_target_obj = function() X() return STATE.target end,
    object_by_id = function(id) X() return nil end,
}
weather = { get_value_vector = function(name)
    X()
    local v = (name == "sun_color") and STATE.sun or STATE.hemi
    return {x = v.x, y = v.y, z = v.z}      -- the engine hands back a fresh vector
end }
level_weathers = { bLevelUnderground = false,
    get_weather_manager = function() return { get_curr_weather_preset = function() return "clear" end } end }
camp_lum = { luminocity_inc = 0 }
news_manager = { send_tip = function() LOGN = LOGN + 1 LOG[LOGN] = "send_tip" end }
actor_menu = { set_msg = function(_, s) LOGN = LOGN + 1 LOG[LOGN] = "set_msg" end }
function IsMoveState(s)
    X()
    if s == "mcCrouch" then return STATE.crouch end
    if s == "mcAccel" then return STATE.accel end
    return false
end

local objmt = {}
objmt.__index = objmt
function objmt:id() X() return self._id end
function objmt:alive() X() return self._alive end
function objmt:get_luminocity() X() return self._lum end
function objmt:object(sec) X() return self._torch end
function objmt:see(o) X() return self._sees[o._id] or false end
function objmt:memory_time(o) X() return self._memt end
function objmt:visibility_threshold() X() return 35 end
function objmt:item_in_slot(n)
    X()
    if n == 10 then return self._slot10 end
    if n == 9 then return self._slot9 end
    return nil
end
function objmt:get_total_weight() X() return STATE.weight end
function objmt:active_detector() X() return STATE.detector end
function objmt:torch_enabled() X() return STATE.torch_on end
function objmt:attachable_item_enabled() X() return self._on end
function objmt:section() X() return self._sec end

function new_obj(id, kind, alive, lum)
    return setmetatable({_id = id, _kind = kind, _alive = alive, _lum = lum or 0.2,
                         _sees = {}, _memt = 90000}, objmt)
end
function IsStalker(o) X() return o._kind == "stalker" or o._kind == "actor" end
function IsMonster(o) X() return o._kind == "monster" end

ACTOR = new_obj(0, "actor", true, 0.3)
ACTOR._slot10 = setmetatable({_sec = "device_torch"}, objmt)
ACTOR._slot9 = setmetatable({_sec = "device_flashlight"}, objmt)
db = { actor = ACTOR, storage = {} }

-- _g_patches.script new_ini_file_ex:r_value / w_value, verbatim shape
local cfg = {}
function cfg:line_exist(s, k) XM() return INI[k] ~= nil end
function cfg:r_string(s, k) XM() local v = INI[k] return v and (v .. "") end
function cfg:r_value(s, k, typ, def)
    if not self:line_exist(s, k) then return def end
    local v = self:r_string(s, k)
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
function cfg:w_value(s, k, val) XM() INI[k] = val ~= nil and tostring(val) or "" end
function cfg:save() XM() end
axr_main = { config = cfg }

-- ui_mcm.get / set: the hot path of the real one (MCM 1.6.x), same order of tests
local options = { {id = "mcm"} }
local gathering = false
local opt_section = "mcm"
local opt_val = {
    ["stealth/memory"] = 2, ["stealth/luminocity"] = 2, ["stealth/distance"] = 2,
    ["stealth/velocity"] = 2, ["stealth/weight"] = 2, ["stealth/crouch"] = 2,
    ["stealth/low_crouch"] = 2, ["stealth/icon"] = 1, ["stealth/michiko_patch"] = 1,
    ["stealth/debugx"] = 1, ["other/thing"] = 2,
}
local opt_def = { ["stealth/memory"] = 1, ["stealth/crouch"] = 0.4, ["other/thing"] = 3 }
ui_mcm = {}
function ui_mcm.get(id)
    assert(not gathering, "ui_mcm.get() cannot be called during script load or on_mcm_load()!")
    if (#options == 0) then error("init_opt_base") end
    if not opt_val[id] then
        printe("!MCM given bad path:%s", id)
        return
    end
    local value = axr_main.config:r_value(opt_section, id, opt_val[id])
    if (value ~= nil) then
        return value
    end
    value = opt_def[id]
    axr_main.config:w_value(opt_section, id, value)
    axr_main.config:save()
    if (value == nil) then printe("!Found nil option value [%s]", id) end
    return value
end
function ui_mcm.set(id, value)
    axr_main.config:w_value(opt_section, id, value)
    axr_main.config:save()
end
function ui_mcm.__gathering(b) gathering = b end
-- what UI_MCM:On_Accept does: write every pending value, save, then ONE callback
function mcm_apply(changes)
    for id, val in pairs(changes) do axr_main.config:w_value(opt_section, id, val) end
    axr_main.config:save()
    SendScriptCallback("on_option_change", true)
end

-- the script loader: each .script gets its own namespace that falls through to _G
function load_script(name, src)
    local f, err = loadstring(src, "@" .. name .. ".script")
    if not f then error(err) end
    local ns = setmetatable({}, {__index = _G})
    setfenv(f, ns)
    _G[name] = ns
    f()
    return ns
end

-- what CVisualMemoryManager::get_visible_value does: functor by NAME, per call
function engine_call(npc, who, time_delta, time_quant, lum, vf, vel, dist, odist, avd)
    return visual_memory_manager.get_visible_value(npc, who, time_delta, time_quant, lum, vf, vel, dist, odist, avd)
end
"""


def read_script(path: Path) -> str:
    raw = path.read_bytes()
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1251", errors="replace")


def build(vmm_src: str | None, *, patched: bool, stealth_src: str | None = None,
          mod_src: str | None = None, jit: bool = True, with_ui_mcm: bool = True):
    """A fresh runtime with the scripts loaded and on_game_start run.

    `vmm_src=None` skips visual_memory_manager (accessor-only tests)."""
    lua = lupa.LuaRuntime()
    if not jit:
        lua.execute("if jit then jit.off() end")
    lua.execute(PRELUDE)
    if not with_ui_mcm:
        lua.execute("ui_mcm = nil")
    load = lua.globals()["load_script"]
    load("stealth_mcm", stealth_src if stealth_src is not None
         else (read_script(STEALTH_MCM) if STEALTH_MCM.is_file() else STEALTH_MCM_SHAPE))
    if vmm_src is not None:
        load("visual_memory_manager", vmm_src)
        lua.execute("visual_memory_manager.on_game_start()")
    if patched:
        load("zzz_alao_vmm_cache", mod_src if mod_src is not None else read_script(MOD_SCRIPT))
        lua.execute("zzz_alao_vmm_cache.on_game_start()")
    return lua


def log_lines(lua, *, ours: bool | None = None):
    """The printf log. ours=True only [alao_vmm_cache] lines, False everything else."""
    out = [str(v) for _, v in sorted(lua.globals()["LOG"].items())]
    if ours is None:
        return out
    return [s for s in out if s.startswith("[alao_vmm_cache") == ours]
