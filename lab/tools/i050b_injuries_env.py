"""I-050b: a stubbed Anomaly environment for zzz_player_injuries.script.

Loads the live (ALAO-rewritten) script into a fresh LuaJIT 2.0 runtime with every
engine global replaced by a Lua stub that (a) behaves like the real one closely
enough for the per-frame path and (b) appends a line to a trace table every time
it is called.  Two things come out of that:

  * an exact count of Lua->C boundary crossings per frame, which is the currency
    the 74 us/frame profile number is spent in; and
  * an observable-behaviour trace (every engine call that mutates anything, in
    order, with its arguments), which is what the differential test compares
    between the original file and the patched one.

The stubs are plain Lua so no Python callback cost pollutes the timings.

The script under test is third-party (GAMMA mod "479- Voiced Actor Refined -
SaloEater"); nothing from it is committed.  We read it from the read-only overlay
that the gen-3 profile run used, so "the body that cost 74 us" and "the body we
measure" are the same bytes.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
# the copy the 74 us was measured on: full-ALAO overlay, live winner
ALAO_LIVE = Path(
    r"C:\code\GIT\anomaly_alao\lab\coord\overlays\ref3-alao-b\gamedata\scripts\zzz_player_injuries.script"
)
# the un-rewritten upstream file of the same mod
ORIG_LIVE = Path(
    r"C:\code\GIT\anomaly_alao\extracted\gamma"
    r"\479- Voiced Actor Refined - SaloEater\gamedata\scripts\zzz_player_injuries.script"
)

# Live MCM values, read out of
# GAMMA/mods/G.A.M.M.A. MCM values .../gamedata/configs/axr_options.ltx
MCM = {
    "TEXT_BASED_PATCH": False,
    "NEW_LIMB_PENALTIES_FEATURE": True,
    "new_voice_sounds": True,
    "leg_penalty_minimum_hp": 1,
    "leg_limping_damage_minimum_hp": 2,
    "leg_animation_power": 0.1,
    "arm_penalty_minimum_hp": 7,
    "arm_animation_power": 1,
    "head_penalty_minimum_hp": 2,
    "head_animation_power": 0.1,
    "chance_damage_on_footstep": 13,
    "hud_toggle_key_bind": 35,
}

PRELUDE = r"""
-- ---------------------------------------------------------------- trace ----
local T = {}            -- trace lines, in order
local C = {}            -- engine call counts by name
_G.__trace = T
_G.__counts = C
_G.__engine_calls = 0
_G.__trace_on = true

local tconcat, tostring_, type_ = table.concat, tostring, type

local function fmt(v)
  local t = type_(v)
  if t == "number" then
    -- keep floats stable across arms
    if v == math.floor(v) and v < 1e14 and v > -1e14 then return string.format("%d", v) end
    return string.format("%.6f", v)
  elseif t == "string" then return string.format("%q", v)
  elseif t == "boolean" or t == "nil" then return tostring_(v)
  elseif t == "table" and v.__name then return "<"..v.__name..">"
  end
  return "<"..t..">"
end

-- record a boundary crossing; `obs` marks the ones that change the game's state
local function hit(name, obs, ...)
  C[name] = (C[name] or 0) + 1
  _G.__engine_calls = _G.__engine_calls + 1
  if obs and _G.__trace_on then
    local n = select('#', ...)
    local parts = {}
    for i = 1, n do parts[i] = fmt((select(i, ...))) end
    T[#T+1] = name.."("..tconcat(parts, ",")..")"
  end
end
_G.__hit = hit

function _G.__reset_trace()
  for i = #T, 1, -1 do T[i] = nil end
  for k in pairs(C) do C[k] = nil end
  _G.__engine_calls = 0
end

-- ------------------------------------------------------------ engine time ---
_G.__tg = 100000                       -- ms, the harness drives it
function time_global() hit("time_global", false); return _G.__tg end

-- ------------------------------------------------------------------ misc ----
function clamp(v, lo, hi) if v < lo then return lo elseif v > hi then return hi else return v end end
function printf() end
function printe() end
function print_dbg() end
strformat = string.format
VEC_ZERO = {x=0, y=0, z=0, __name="vec0"}

dialogs = {}
DIK_keys = setmetatable({}, {__index = function(_, k) return 35 end})
colors = setmetatable({}, {__index = function(_, k) return "%c["..k.."]" end})
function GetARGB(a,r,g,b) hit("GetARGB", false); return 0 end

function CreateTimeEvent(a,b,c,d) hit("CreateTimeEvent", true, a, b, c) return true end
function RegisterScriptCallback(name, fn) _G.__callbacks = _G.__callbacks or {}; _G.__callbacks[name] = fn end
function UnregisterScriptCallback() end
function exec_console_cmd(s) hit("exec_console_cmd", true, s) end
function get_console_cmd(t, s) hit("get_console_cmd", false); return 1 end

ui_options = { get = function(k) hit("ui_options.get", false); return 1 end }

-- ------------------------------------------------------------------ MCM -----
-- Faithful model of the live chain:
--   zzz_player_injuries_mcm.get_config(k)  (G.A.M.M.A. Medications Balance)
--     -> ui_mcm.get("body_health_system/"..k)
--        -> axr_main.config:r_value("mcm", id, opt_val[id])     (_g.script ini_file_ex)
-- ini_file_ex:r_value caches, but caches by truthiness: `if (cache_result) then`.
-- A stored value of FALSE therefore never hits the cache and pays
-- section_exist + line_exist + r_string -- three engine calls -- every call.
local mcm_values = {}
local ini_cache = {}
local function ini_r_value(s, k)
  local key = s.."&"..k
  local cached = ini_cache[key]
  if cached then return cached end                     -- false/nil fall through
  hit("ini:section_exist", false)
  hit("ini:line_exist", false)
  hit("ini:r_string", false)
  local v = mcm_values[k]
  ini_cache[key] = v
  return v
end
ui_mcm = { get = function(id)
  local k = id:match("/(.*)$")
  return ini_r_value("mcm", id)
end }
zzz_player_injuries_mcm = { get_config = function(k)
  return ui_mcm.get("body_health_system/"..k)
end }
function __set_mcm(k, v) mcm_values["body_health_system/"..k] = v end

-- ----------------------------------------------------------------- actor ----
-- Engine vectors and the actor are C++ objects: reading `vec.x` or
-- `actor.health` is a luabind property getter, i.e. a boundary crossing just
-- like a method call.  Count them.
local function vec(x,y,z)
  return setmetatable({__name="vec", _x=x, _y=y, _z=z}, {__index = function(t,k)
    hit("vec."..k, false); return rawget(t, "_"..k)
  end})
end

local actor_fields = {health = 1.0, power = 1.0}
local actor = setmetatable({__name = "actor"}, {
  __index = function(t, k)
    if actor_fields[k] ~= nil then hit("actor."..k..".get", false); return actor_fields[k] end
    return rawget(t, "_m_"..k)
  end,
  __newindex = function(t, k, v)
    if actor_fields[k] ~= nil then hit("actor."..k..".set", true, k, v); actor_fields[k] = v
    else rawset(t, "_m_"..k, v) end
  end,
})
_G.__actor_fields = actor_fields
local function amethod(name, fn) rawset(actor, "_m_"..name, fn) end
amethod("character_name", function(self) hit("actor:character_name", false); return "Marked One" end)
amethod("get_movement_speed", function(self) hit("actor:get_movement_speed", false); return vec(0,0,0) end)
amethod("active_slot", function(self) hit("actor:active_slot", false); return 2 end)
amethod("item_in_slot", function(self, s) hit("actor:item_in_slot", false); return nil end)
amethod("drop_item", function(self, i) hit("actor:drop_item", true) end)
amethod("get_actor_run_coef", function(self) hit("actor:get_actor_run_coef", false); return 1 end)
amethod("get_actor_runback_coef", function(self) hit("actor:get_actor_runback_coef", false); return 1 end)
amethod("get_actor_sprint_koef", function(self) hit("actor:get_actor_sprint_koef", false); return 1 end)
amethod("id", function(self) hit("actor:id", false); return 0 end)
amethod("character_icon", function(self) hit("actor:character_icon", false); return "icon" end)
amethod("give_game_news", function(...) hit("actor:give_game_news", true) end)
db = { actor = actor }
_G.__actor = actor

actor_menu = {
  set_msg = function(a, b, c) hit("actor_menu.set_msg", true, a, b, c) end,
  get_maingame = function() hit("actor_menu.get_maingame", false); return _G.__maingame end,
}
local hud_states = setmetatable({}, {
  __index = function(t, k) hit("hud_states.get", false); return rawget(t, "_"..k) end,
  __newindex = function(t, k, v) hit("hud_states.set", true, k, v); rawset(t, "_"..k, v) end,
})
_G.__maingame = setmetatable({__name="maingame"}, {
  __index = function(t, k)
    if k == "m_ui_hud_states" then hit("maingame.m_ui_hud_states", false); return hud_states end
  end,
})
ActorMenu = { get_maingame = function() hit("ActorMenu.get_maingame", false); return _G.__maingame end }

-- ------------------------------------------------------------------ HUD -----
local statics = {}
local function make_wnd(name)
  local tc = {
    __name = "textcontrol",
    SetTextST = function(self, s) hit("wnd:SetTextST", true, name, s) end,
    SetTextColor = function(self, c) hit("wnd:SetTextColor", true, name) end,
  }
  return {
    __name = "wnd:"..name,
    SetAutoDelete = function(self, b) hit("wnd:SetAutoDelete", true, name, b) end,
    TextControl = function(self) hit("wnd:TextControl", false); return tc end,
  }
end
local hud = {
  __name = "hud",
  GetCustomStatic = function(self, n) hit("hud:GetCustomStatic", false); return statics[n] end,
  AddCustomStatic = function(self, n, b)
    hit("hud:AddCustomStatic", true, n, b)
    statics[n] = {__name="cs:"..n, wnd = function(self) hit("cs:wnd", false); return self._wnd end, _wnd = make_wnd(n)}
    return statics[n]
  end,
  RemoveCustomStatic = function(self, n) hit("hud:RemoveCustomStatic", true, n); statics[n] = nil end,
}
function get_hud() hit("get_hud", false); return hud end
_G.__statics = statics

function CScriptXmlInit()
  hit("CScriptXmlInit", true)
  return {
    __name = "xml",
    ParseFile = function(self, f) hit("xml:ParseFile", true, f) end,
    InitProgressBar = function(self, n, w)
      hit("xml:InitProgressBar", true, n)
      return {
        __name = "bar:"..n,
        Show = function(self, b) hit("bar:Show", true, n, b) end,
        SetProgressPos = function(self, p) hit("bar:SetProgressPos", true, n, p) end,
      }
    end,
  }
end

function device() hit("device", false); return {width = 2560, height = 1440} end

-- ---------------------------------------------------------------- level -----
level = {
  add_pp_effector = function(a,b,c) hit("level.add_pp_effector", true, a, b, c) end,
  remove_pp_effector = function(a) hit("level.remove_pp_effector", true, a) end,
  set_pp_effector_factor = function(a, f) hit("level.set_pp_effector_factor", true, a, f) end,
  add_cam_effector = function(...) hit("level.add_cam_effector", true, ...) end,
}

-- --------------------------------------------------------------- gametime ---
_G.__gametime = 0
local CTime = {}
CTime.__index = CTime
function CTime:diffSec(o) hit("CTime:diffSec", false); return self.t - o.t end
game = {
  get_game_time = function()
    hit("game.get_game_time", false)
    return setmetatable({t = _G.__gametime, __name = "ctime"}, CTime)
  end,
}

-- ---------------------------------------------------------------- speed -----
speed = { add_speed = function(n, k, a, b) hit("speed.add_speed", true, n, k, a, b) end }

sound_object = setmetatable({s2d = 1}, {__call = function(self, p)
  hit("sound_object", true, p)
  return {__name="snd", play_no_feedback = function(...) hit("snd:play_no_feedback", true) end, volume = 0}
end})

-- -------------------------------------------------------------- utils -------
utils_obj = {
  save_var = function(o, k, v) hit("utils_obj.save_var", true, k, v) end,
  load_var = function(o, k) hit("utils_obj.load_var", false); return nil end,
}
ini_sys = {
  section_exist = function(self, s, k) hit("ini_sys:section_exist", false); return false end,
  r_float_ex = function(self, s, k) hit("ini_sys:r_float_ex", false); return 1 end,
}
news_manager = { send_tip = function(...) hit("news_manager.send_tip", true) end }
xr_sound = { set_sound_play = function(id, s) hit("xr_sound.set_sound_play", true, s) end }
grok_progressive_rad_damages = { grok_rads = 0 }
math.randomseed(20260920)        -- same draw sequence in both arms
function IsMoveState(s) hit("IsMoveState", false); return false end
function round_idp(v, n)
  local m = 10 ^ (n or 0)
  return math.floor(v * m + 0.5) / m
end
"""


def make_runtime(script_path: Path, mcm: dict | None = None, jit_off: bool = False):
    """Fresh LuaJIT 2.0 runtime with the stub env and the script loaded.

    Returns (lua, module_table).  The script's own globals live in `module`,
    exactly as Anomaly's per-file namespace does.
    """
    from lupa import luajit20 as lupa

    lua = lupa.LuaRuntime(unpack_returned_tuples=True)
    if jit_off:
        # jit.off() is the global switch; jit.off(true,true) only covers the
        # calling one-liner and leaves everything loaded afterwards compiled.
        lua.execute("if jit then jit.off() end")
    lua.execute(PRELUDE)
    set_mcm = lua.globals()["__set_mcm"]
    for k, v in (mcm or MCM).items():
        set_mcm(k, v)

    src = script_path.read_bytes().decode("cp1251")
    lua.globals()["__src"] = src
    lua.globals()["__chunkname"] = script_path.name
    lua.execute(
        """
        local f, err = loadstring(__src, "@"..__chunkname)
        if not f then error(err) end
        local M = {}
        setmetatable(M, {__index = _G, __newindex = function(t,k,v) rawset(t,k,v) end})
        setfenv(f, M)
        f()
        _G.__M = M
        """
    )
    return lua, lua.globals()["__M"]


def boot(lua, module):
    """Bring the script to the steady state the profiler measured: game running,
    first update done, HUD statics created."""
    module["on_game_start"]()
    module["actor_on_first_update"]()
    # ParamBar creates at most ONE custom static per pass (the `scuffed_fix`
    # latch) and re-arms itself 0.2 s later through CreateTimeEvent /
    # worst_possible_fix.  The stub does not run time events, so drive the latch
    # by hand until every bar exists and the frame settles.
    for _ in range(40):
        module["worst_possible_fix"]()   # what the 0.2 s time event does
        tick(lua)
    tick(lua)
    return module


def tick(lua, ms=16, game_s=0.11):
    g = lua.globals()
    g["__tg"] = g["__tg"] + ms
    g["__gametime"] = g["__gametime"] + game_s
    g["__M"]["actor_on_update"]()


def frame_profile(lua, frames=1):
    """counts + trace for `frames` steady-state frames"""
    g = lua.globals()
    g["__reset_trace"]()
    for _ in range(frames):
        tick(lua)
    counts = dict(g["__counts"])
    trace = [g["__trace"][i] for i in range(1, len(g["__trace"]) + 1)]
    return int(g["__engine_calls"]), counts, trace


if __name__ == "__main__":
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else ALAO_LIVE
    lua, M = make_runtime(path)
    boot(lua, M)
    n, counts, trace = frame_profile(lua, 1)
    print(f"{path.name}: {n} engine calls / steady-state frame")
    for k, v in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {v:4d}  {k}")
    print(f"observable calls: {len(trace)}")
    for line in trace:
        print("   ", line)
