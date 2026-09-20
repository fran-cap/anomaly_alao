"""Stub-engine harness for the I-057 track-B listeners.

Same idea as `i050a_harness`: load the real script into lupa.luajit20 on top of
a stub Anomaly engine, drive it frame by frame, and record two things per frame.

  EFFECTS - the calls that change the game (level.press_action, switch_state,
            set_notification, SetTextureColor, SetWndRect, Show-state, ...).
            These must be identical between the arms.
  QUERIES - the calls that only ask the engine something (section(),
            SYS_GetParam, get_hud, GetCustomStatic, item_in_slot, r_value,
            get_console_cmd, main_hud_shown, ...).  These are what the patch
            removes, so the *difference* in their count is the saving, in
            crossings per frame.

Wall-clock is deliberately not measured here: every stub is a Lua table lookup
and an engine crossing is not, so counting them and pricing them pessimistically
(the gen-4 rule: trivial getters ~0.25 us, ltx/console/UI calls more) is the
honest arithmetic.  Crossing counts are also deterministic, so unlike a timing
they do not care whether another agent is using the box.
"""
from __future__ import annotations

from pathlib import Path

from lupa import luajit20 as lupa

PRELUDE = r"""
EFFECTS, EFFN = {}, 0
QUERIES, QN = {}, 0
function effect(s) EFFN = EFFN + 1 EFFECTS[EFFN] = s end
function query(s) QN = QN + 1 QUERIES[QN] = s end

STATE = {
    tg = 1000,
    alive = true,
    -- weapon
    section = "wpn_ak74",
    state = 0,
    kind = "w_rifle",
    class = "WP_AK74",
    scope = false,
    has_item = true,
    -- ui / menus
    last_mode = 0,
    dialog_closed = true,
    check_ui = false,
    has_info = false,
    aim_toggle = false,
    zoomed = false,
    hud_shown = true,
    pda_shown = false,
    inv_open = false,
    -- detector / torch / gem
    detector = nil,
    torch_on = false,
    flash_sec = "device_torch",
    icon = false,
    lum = 0.2,
    charged = true,
}

function time_global() query("time_global") return STATE.tg end
function key_state(k) query("key_state") return 0 end
function bind_to_dik(b) query("bind_to_dik") return b end
key_bindings = {kWPN_FIRE = 1, kWPN_ZOOM = 2}
function IsWeapon(o) query("IsWeapon") return o ~= nil end
function SYS_GetParam(t, sec, key)
    query("SYS_GetParam:"..tostring(key))
    if key == "kind" then return STATE.kind end
    if key == "class" then return STATE.class end
    return nil
end
function Check_UI() query("Check_UI") return STATE.check_ui end
function clamp(v, a, b) if v < a then return a end if v > b then return b end return v end
function GetARGB(a,r,g,b) query("GetARGB") return a*16777216 + r*65536 + g*256 + b end
function main_hud_shown() query("main_hud_shown") return STATE.hud_shown end
function get_console_cmd(t, c) query("get_console_cmd:"..c) return STATE.aim_toggle end
function printe() end
function printf() end
function RegisterScriptCallback(name, f)
    CALLBACKS[name] = CALLBACKS[name] or {}
    CALLBACKS[name][#CALLBACKS[name]+1] = f
end
function UnregisterScriptCallback() end
function SendScriptCallback(name, ...)
    for _, f in ipairs(CALLBACKS[name] or {}) do f(...) end
end
CALLBACKS = {}
function CreateTimeEvent(a, b, t, f) effect("CreateTimeEvent "..tostring(b)) end
function empty_table(t) for k in pairs(t) do t[k] = nil end return t end

local wpnmt = {}
wpnmt.__index = wpnmt
function wpnmt:section() query("section") return STATE.section end
function wpnmt:get_state() query("get_state") return STATE.state end
function wpnmt:weapon_is_scope() query("weapon_is_scope") return STATE.scope end
function wpnmt:switch_state(s) effect("switch_state "..tostring(s)) end
function wpnmt:torch_enabled() query("torch_enabled") return STATE.torch_on end
function wpnmt:id() query("id") return 7 end
local WPN = setmetatable({}, wpnmt)
local TORCH = setmetatable({}, wpnmt)

db = {actor = {
    alive = function(self) query("alive") return STATE.alive end,
    active_item = function(self) query("active_item") if STATE.has_item then return WPN end return nil end,
    has_info = function(self, s) query("has_info") return STATE.has_info end,
    item_in_slot = function(self, n)
        query("item_in_slot:"..n)
        if n == 10 then return STATE.torch_on and TORCH or TORCH end
        if n == 9 then return setmetatable({section = function() query("section") return STATE.flash_sec end}, nil) end
        return nil
    end,
    active_detector = function(self) query("active_detector") return STATE.detector end,
}}

level = {press_action = function(k) effect("press_action "..tostring(k)) end}
game = {actor_weapon_lowered = function() query("weapon_lowered") return false end}
axr_main = {weapon_is_zoomed = false,
            config = {r_value = function(self, s, id, v) query("r_value:"..id) return nil end,
                      w_value = function() effect("w_value") end,
                      save = function() effect("config_save") end}}
actor_menu = {last_mode = 0,
              inventory_opened = function() query("inventory_opened") return STATE.inv_open end,
              last_hud_msg = function() query("last_hud_msg") return false end,
              set_notification = function(a, b, c, d) effect("notify "..tostring(b)) end}
pda = {dialog_closed = true}
ActorMenu = {get_pda_menu = function()
    query("get_pda_menu")
    return {IsShown = function() query("IsShown") return STATE.pda_shown end}
end}
ui_options = {get = function(id)
    -- the real path on this install: an ini_file_ex miss, then a console read
    query("r_value:"..id)
    query("get_console_cmd:wpn_aim_toggle")
    return STATE.aim_toggle
end}
item_device = {is_device_charged = function(d) query("is_device_charged") return STATE.charged end}
stealth_mcm = {get_config = function(k) query("mcm:"..k) return STATE.icon end}
visual_memory_manager = {icon_lum = function() query("icon_lum") return STATE.lum end}
function get_hud() query("get_hud") return HUD end
HUD = {
    GetCustomStatic = function(self, n) query("GetCustomStatic:"..n) return HUD._stat[n] end,
    AddCustomStatic = function(self, n) effect("AddCustomStatic "..n) HUD._stat[n] = MKSTATIC() end,
    RemoveCustomStatic = function(self, n) effect("RemoveCustomStatic "..n) HUD._stat[n] = nil end,
    _stat = {},
}
function MKSTATIC()
    local w = {GetWndPos = function() query("GetWndPos") return {x = 0, y = 0} end,
               SetWndPos = function() effect("SetWndPos") end,
               SetWndRect = function(self, r) effect("SetWndRect") end}
    return {wnd = function(self) query("wnd") return w end,
            SetWndRect = function(self, r) effect("SetWndRect") end}
end
function CScriptXmlInit()
    query("CScriptXmlInit")
    return {ParseFile = function(self, f) effect("ParseFile "..f) end,
            InitStatic = function(self, n, w)
                effect("InitStatic "..n)
                return {Show = function(self, v) effect("Show "..tostring(v)) end,
                        SetTextureColor = function(self, c) effect("SetTextureColor "..tostring(c)) end}
            end}
end
function Frect() query("Frect") return {set = function(self) return self end} end
"""

LOADER = r"""
function(src, name)
    local env = setmetatable({}, {__index = _G})
    local chunk = assert(loadstring(src, "@"..name))
    setfenv(chunk, env)
    _G[name] = env
    chunk()
    return env
end
"""


class Arm:
    def __init__(self, script: Path, name: str):
        self.lua = lupa.LuaRuntime(unpack_returned_tuples=True)
        self.lua.execute(PRELUDE)
        self.ns = self.lua.eval(LOADER)(
            script.read_text(encoding="utf-8"), name)
        self.g = self.lua.globals()
        self.state = self.g.STATE

    def clear(self):
        self.g.EFFECTS, self.g.EFFN = self.lua.table(), 0
        self.g.QUERIES, self.g.QN = self.lua.table(), 0

    def effects(self):
        return [self.g.EFFECTS[i] for i in range(1, int(self.g.EFFN) + 1)]

    def queries(self):
        return [self.g.QUERIES[i] for i in range(1, int(self.g.QN) + 1)]

    def set(self, **kw):
        for k, v in kw.items():
            self.state[k] = v
