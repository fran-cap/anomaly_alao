"""Stub-engine harness for the I-063 prewarm bundle.

Same shape as `i050a_harness` / `i057_bundle_harness`: a stub Anomaly engine in
`lupa.luajit20`, the REAL `eft_jump_sounds.script` and `footstep_sounds.script`
loaded on top of it, and `zzz_alao_prewarm.script` as the thing under test.

Two ledgers per frame:

  EFFECTS - what the game would see: every `sound_object(path)` that gets
            PLAYED, every UI construction, every callback registration.  These
            must be identical between the arm with the mod and the arm without,
            except for the handful of differences the mod's README names.
  CONSTRUCTIONS - every `sound_object(path)` construction, with a `cold` flag
            for the first construction of that path in the session.  This is the
            thing the prewarm moves, and it is a count, not a timing.

Wall-clock is deliberately not measured.  Everything the prewarm removes sits
engine-side (an xml parse, a widget tree, a sound resource load) and a stub
cannot price it; counting the cold constructions that move out of play is the
honest arithmetic, and unlike a timing it does not care what else the box is
doing.
"""
from __future__ import annotations

from pathlib import Path

from lupa import luajit20 as lupa

REPO = Path(__file__).resolve().parent.parent.parent
MOD = REPO / "lab" / "mods" / "alao-prewarm" / "gamedata" / "scripts" / "zzz_alao_prewarm.script"

GAMMA_MODS = Path(r"D:\GOG_Games\Gamma\S.T.A.L.K.E.R. GAMMA\GAMMA\mods")
LIVE_JUMP = GAMMA_MODS / "Oleh's Extended MovementSFX" / "gamedata" / "scripts" / "eft_jump_sounds.script"
LIVE_STEP = GAMMA_MODS / "Oleh's Extended MovementSFX" / "gamedata" / "scripts" / "footstep_sounds.script"


PRELUDE = r"""
EFFECTS, EFFN = {}, 0
CONSTRUCTED, CN = {}, 0
COLD = {}                 -- path -> true once it has been constructed
FRAME = 0
BAD_PATHS = {}            -- paths sound_object() should refuse to build

function effect(s) EFFN = EFFN + 1 EFFECTS[EFFN] = s end

-- The engine's sound object.  A construction is recorded with whether this is
-- the first time this path was ever built in the session: that, and only that,
-- is what the prewarm moves.
local sndmt = {}
sndmt.__index = sndmt
function sndmt:play(who, d, mode) effect("play " .. self.path) self.playing = true end
function sndmt:stop() effect("stop " .. self.path) self.playing = false end

-- In the engine `sound_object` is a class, not a function: it is called AND
-- indexed (`sound_object.s2d`).  Model it as a callable table.
function raw_sound_object(path)
    SIM_US = SIM_US + SIM_COST_PER_SOUND
    if BAD_PATHS[path] then error("no such sound: " .. tostring(path), 0) end
    CN = CN + 1
    CONSTRUCTED[CN] = {path = path, cold = (not COLD[path]) and true or false, frame = FRAME}
    COLD[path] = true
    local o = setmetatable({path = path, volume = 0, playing = false}, sndmt)
    return o
end
sound_object = setmetatable({s2d = 1}, {__call = function(_, path)
    return raw_sound_object(path)
end})

-- callbacks
CALLBACKS = {}
function RegisterScriptCallback(name, f)
    CALLBACKS[name] = CALLBACKS[name] or {}
    CALLBACKS[name][#CALLBACKS[name] + 1] = f
    effect("register " .. name)
end
function UnregisterScriptCallback(name, f)
    local t = CALLBACKS[name]
    if not t then return end
    for i = 1, #t do
        if t[i] == f then table.remove(t, i) effect("unregister " .. name) return end
    end
end
function SendScriptCallback(name, ...)
    local t = CALLBACKS[name]
    if not t then return end
    for i = 1, #t do t[i](...) end
end
function listener_count(name) return #(CALLBACKS[name] or {}) end

function printf(...) end
function strformat(...) return string.format(...) end
function shuffle_table(t) return t end
function clamp(v, a, b) if v < a then return a end if v > b then return b end return v end
function IsMoveState(s) return STATE.move_state == s end
function vector() local v = {x=0,y=0,z=0} v.set = function(self,x,y,z) self.x,self.y,self.z=x,y,z return self end return v end
function vector_cross(a, b) return vector() end
function printe(...) end

STATE = {move_state = "none", material = "materials\\earth", outfit_class = "default"}

db = {actor = {
    position = function(self) return vector() end,
    direction = function(self) return vector() end,
    get_total_weight = function(self) return 20 end,
}}

demonized_geometry_ray = {geometry_ray = function(t)
    return {get = function(self, p, d) return {result = {material_name = STATE.material}} end}
end}

oleh_sound_utils = {
    get_outfit_class = function() return STATE.outfit_class end,
    get_outfit_params = function() return {id = nil, class = STATE.outfit_class, weight = 10} end,
}

ui_mcm = nil
oleh_sounds_mcm = nil
extended_movement_sounds_mcm = nil
extended_movement_sounds = nil

-- profile_timer: one unit is one microsecond.  Real os.clock so the mod's own
-- calibration against os.clock works unchanged, PLUS a simulated cost per sound
-- construction, so the slice budget is exercised deterministically instead of
-- depending on how fast this machine builds a stub table.
SIM_US = 0
SIM_COST_PER_SOUND = 120        -- microseconds each construction "costs"
local function now_us() return os.clock() * 1000000 + SIM_US end
function profile_timer()
    local acc, t0, running = 0, 0, false
    return {
        start = function(self) t0 = now_us() running = true end,
        stop  = function(self) if running then acc = acc + (now_us() - t0) running = false end end,
        time  = function(self) if running then return acc + (now_us() - t0) end return acc end,
    }
end

-- os.clock is real here; calibrate() only uses it to learn units-per-ms, and
-- the tests that care about the budget set units_per_ms themselves.

-- ---------------------------------------------------------------------------
-- the two lazy singletons, modelled exactly as the live scripts model them
-- ---------------------------------------------------------------------------

UI_BUILDS = 0
ENC_BUILDS = 0

ui_inventory = {GUI = nil}
function ui_inventory.UIInventory()
    UI_BUILDS = UI_BUILDS + 1
    effect("UIInventory()")
    local gui = {shown = false}
    gui.IsShown = function(self) return self.shown end
    -- the twelve listeners UIInventory:__init registers
    for _, n in ipairs({"actor_item_to_ruck", "actor_item_to_slot", "actor_item_to_belt",
                        "actor_on_item_drop", "actor_on_item_use", "actor_on_item_put_in_box",
                        "actor_on_item_take_from_box", "npc_on_item_take", "npc_on_item_drop",
                        "npc_on_use", "physic_object_on_use_callback", "actor_on_net_destroy"}) do
        RegisterScriptCallback(n, function() end)
    end
    return gui
end
function ui_inventory.start(mode)
    if not ui_inventory.GUI then ui_inventory.GUI = ui_inventory.UIInventory() end
    effect("inventory start " .. tostring(mode))
end
-- what a level change does
function ui_inventory.net_destroy() ui_inventory.GUI = nil end

local ENC_SINGLETON = nil
ui_pda_encyclopedia_tab = {}
function ui_pda_encyclopedia_tab.get_ui()
    if not ENC_SINGLETON then
        ENC_BUILDS = ENC_BUILDS + 1
        effect("pda_encyclopedia_tab()")
        ENC_SINGLETON = {}
    end
    return ENC_SINGLETON
end
function ui_pda_encyclopedia_tab.set_article(sec)
    local guide = ui_pda_encyclopedia_tab.get_ui()
    effect("set_article " .. sec)
end
function ui_pda_encyclopedia_tab.reset_singleton() ENC_SINGLETON = nil end
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


class Arm:
    """One stub engine, optionally with the prewarm mod installed."""

    def __init__(self, with_mod: bool):
        self.lua = lupa.LuaRuntime(unpack_returned_tuples=True)
        self.lua.execute(PRELUDE)
        self.load = self.lua.eval(LOADER)
        self.g = self.lua.globals()
        self.jump = self.load(LIVE_JUMP.read_bytes().decode("utf-8"), "eft_jump_sounds")
        self.step = self.load(LIVE_STEP.read_bytes().decode("utf-8"), "footstep_sounds")
        self.jump.on_game_start()
        self.step.on_game_start()
        self.mod = None
        if with_mod:
            self.mod = self.load(MOD.read_bytes().decode("utf-8"), "zzz_alao_prewarm")
            self.mod.on_game_start()

    # -- driving ----------------------------------------------------------
    def first_update(self):
        self.g.SendScriptCallback("actor_on_first_update")

    def frame(self, n=1):
        for _ in range(n):
            self.g.FRAME = int(self.g.FRAME) + 1
            self.g.SendScriptCallback("actor_on_update", None, 5)

    def jump_now(self):
        self.g.SendScriptCallback("actor_on_jump")

    def land_now(self, speed=6.0):
        self.g.SendScriptCallback("actor_on_land", speed)

    def footstep(self, material=None, power=1.0):
        self.g.SendScriptCallback("actor_on_footstep",
                                  material or self.g.STATE.material, power, False,
                                  self.lua.table_from({"ret_value": True}))

    # -- ledgers ----------------------------------------------------------
    def effects(self):
        return [self.g.EFFECTS[i] for i in range(1, int(self.g.EFFN) + 1)]

    def constructions(self):
        out = []
        for i in range(1, int(self.g.CN) + 1):
            c = self.g.CONSTRUCTED[i]
            out.append({"path": c["path"], "cold": bool(c["cold"]), "frame": int(c["frame"])})
        return out

    def cold_in_play(self, after_frame: int):
        """Cold constructions that happened at or after *after_frame*."""
        return [c for c in self.constructions() if c["cold"] and c["frame"] >= after_frame]

    def clear_effects(self):
        self.g.EFFECTS, self.g.EFFN = self.lua.table(), 0

    def set(self, **kw):
        for k, v in kw.items():
            self.g.STATE[k] = v
