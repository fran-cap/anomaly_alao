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

__PRINTED = {}
function printf(fmt, ...)
    local ok, s = pcall(string.format, fmt, ...)
    __PRINTED[#__PRINTED + 1] = ok and s or tostring(fmt)
end
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

-- The ruck.  RUCK_N items; ParseInventory hands them back.
RUCK_N = 19

ui_inventory = {GUI = nil}
function ui_inventory.UIInventory()
    UI_BUILDS = UI_BUILDS + 1
    effect("UIInventory()")
    local gui = {shown = false, CC = {}}
    gui.IsShown = function(self) return self.shown end
    -- actor_bag is the lazy one: built empty, grown on the first open
    gui.CC["actor_bag"] = MAKE_CC("actor_bag", gui)
    -- actor_equ / belt / quick get their cells here, like InitControls does
    for _, name in ipairs({"actor_equ", "actor_belt", "actor_quick"}) do
        local cc = MAKE_CC(name, gui)
        cc.disable_callback["On_CC_Add"] = true
        cc.disable_callback["On_CC_Remove"] = true
        for i = 1, 13 do cc.cell[i] = utils_ui.UICellItem(cc, cc.st, i, true) end
        gui.CC[name] = cc
    end
    gui.ParseInventory = function(self, npc, all, id_list, ignore_kind)
        local t = {}
        for i = 1, RUCK_N do t[i] = "item_" .. i end
        return t
    end
    -- the only On_CC_Add subscriber in the live stack: sets a flag, nothing else
    gui.On_CC_Add = function(self, bag, idx, on_area) self.update_info = true end
    -- what an "inventory" mode open does: IMode_ResetInventories
    gui.IMode_Init = function(self)
        self.CC["actor_bag"]:Reinit(self:ParseInventory(db.actor))
        effect("IMode_Init")
    end
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
    if mode == "inventory" then ui_inventory.GUI:IMode_Init() end
    effect("inventory start " .. tostring(mode))
end

-- What FDDA Redone / SortingPlus do at actor_on_first_update, ahead of us.
function OTHER_MOD_BUILDS_GUI()
    if not ui_inventory.GUI then ui_inventory.GUI = ui_inventory.UIInventory() end
end

-- ---------------------------------------------------------------------------
-- the engine tutorial sequencer
-- ---------------------------------------------------------------------------

TUTORIALS = {alao_prewarm_noop = true,
             tutorial_campfire_ignite = true, tutorial_campfire_extinguish = true}
TUT_ACTIVE = nil
TUT_STARTS = {}
TUT_WARM = false          -- the engine's one-time sequencer cost
TUT_COLD_COST = 0         -- bumped by the test to represent the 700 ms

game = game or {}
function game.start_tutorial(name)
    TUT_STARTS[#TUT_STARTS + 1] = name
    effect("start_tutorial " .. tostring(name))
    if not TUTORIALS[name] then
        effect("start_tutorial UNKNOWN " .. tostring(name))
        return
    end
    if not TUT_WARM then
        TUT_WARM = true
        SIM_US = SIM_US + TUT_COLD_COST
        effect("sequencer cold load")
    end
    TUT_ACTIVE = name
end
function game.stop_tutorial()
    effect("stop_tutorial " .. tostring(TUT_ACTIVE))
    TUT_ACTIVE = nil
end
function game.has_active_tutorial() return TUT_ACTIVE ~= nil end
-- what a level change does
function ui_inventory.net_destroy() ui_inventory.GUI = nil end

-- ---------------------------------------------------------------------------
-- UICellContainer, modelled on the live utils_ui.script closely enough to be
-- wrong in the same ways: Reset() hides cells but NEVER removes one, Grow()
-- only extends the grid, and AddItemInCell constructs a UICellItem only when
-- self.cell[indx] is nil.  That is the whole mechanism the pool prewarm is for.
-- ---------------------------------------------------------------------------

CELL_BUILDS = 0
CC_ADD_FIRED = 0
COLS = 5

local celmt = {}
celmt.__index = celmt
function celmt:Reset() self.ID = nil self.area = nil effect("cell reset " .. self.indx) end
function celmt:Set(obj, area) self.ID = obj self.area = area return true end
function celmt:Show(v) end

utils_ui = {}
function utils_ui.UICellItem(container, st, indx, manual)
    CELL_BUILDS = CELL_BUILDS + 1
    effect("UICellItem " .. indx)
    -- four xml:InitStatic in the real InitControls
    for _ = 1, 4 do effect("InitStatic") end
    return setmetatable({container = container, indx = indx, manual = manual}, celmt)
end

local ccmt = {}
ccmt.__index = ccmt

function ccmt:Callback(func, ...)
    if self.disable_callback[func] then return end
    if func == "On_CC_Add" then CC_ADD_FIRED = CC_ADD_FIRED + 1 end
    if self.owner and self.owner[func] then return self.owner[func](self.owner, ...) end
end

GROWS = 0
function ccmt:Grow()
    -- The real one is a Lua table and `cols` booleans: no static, no texture,
    -- no engine call.  Counted here only so a test can assert it never happens
    -- at open time.
    GROWS = GROWS + 1
    local rows = #self.grid + 1
    self.grid[rows] = {}
    for i = 1, COLS do self.grid[rows][i] = true end
end

function ccmt:Reset()
    -- keeps self.cell, exactly like the real one
    for _, ci in pairs(self.cell) do ci:Reset() end
    self.idxer = 0
    empty_table(self.indx_id)
    for _, v in pairs(self.grid) do
        for col in pairs(v) do v[col] = true end
    end
end

-- How densely items pack. "kind" starts a fresh row per kind group, which is
-- what SortingPlus turns on AFTER this mod's listener has run; the nil/default
-- sizekind layout packs COLS per row.  That difference is the v1.2 grid bug.
SORT_METHOD = nil

function ccmt:AddItem(obj)
    local id = tostring(obj)
    if self.indx_id[id] then return end
    local per_row = (SORT_METHOD == "kind") and 1 or COLS
    local placed = nil
    for r = 1, #self.grid do
        local used = 0
        for c = 1, COLS do if self.grid[r][c] == false then used = used + 1 end end
        if used < per_row then
            for c = 1, COLS do
                if self.grid[r][c] then self.grid[r][c] = false placed = r break end
            end
            break
        end
    end
    if not placed then
        self:Grow()
        placed = #self.grid
        self.grid[placed][1] = false
    end
    local indx = self.idxer + 1
    if not self.cell[indx] then
        self.cell[indx] = utils_ui.UICellItem(self, self.st, indx)
    end
    self.cell[indx]:Set(obj, {y = placed, x = 1, w = 1, h = 1})
    self.idxer = indx
    self.indx_id[id] = indx
    self:Callback("On_CC_Add", self.ID, indx, true)
end

function ccmt:Reinit(t)
    self:Reset()
    if not t then return end
    for _, obj in pairs(t) do self:AddItem(obj) end
end

function MAKE_CC(id, owner)
    return setmetatable({ID = id, owner = owner, st = {}, cell = {}, grid = {},
                         indx_id = {}, idxer = 0, disable_callback = {}}, ccmt)
end

function empty_table(t) for k in pairs(t) do t[k] = nil end return t end

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

    def __init__(self, with_mod: bool, slice_sounds: bool = False):
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
            src = MOD.read_bytes().decode("utf-8")
            if slice_sounds:
                # The escape hatch is a file-local constant, so flip it in the
                # source rather than pretending the mod has a setter.
                old = "local SLICE_SOUNDS         = false"
                assert src.count(old) == 1, "SLICE_SOUNDS declaration moved"
                src = src.replace(old, "local SLICE_SOUNDS         = true")
            self.mod = self.load(src, "zzz_alao_prewarm")
            self.mod.on_game_start()

    # -- driving ----------------------------------------------------------
    def first_update(self, others_build_gui=True, sortingplus="kind"):
        """One actor_on_first_update pass, in the live registration order.

        `custom_functor_autoinject` (FDDA) builds the GUI first, then ours runs
        - `zzz_alao_prewarm` sorts before `zzz_rax_sortingplus_mcm` - and then
        SortingPlus sets the sort method, which changes how densely the next
        open packs the grid.  That ordering is the whole of the v1.2 grid bug.
        """
        if others_build_gui:
            self.g.OTHER_MOD_BUILDS_GUI()
        self.g.SendScriptCallback("actor_on_first_update")
        if sortingplus:
            self.g.SORT_METHOD = sortingplus

    def open_inventory(self):
        self.g.ui_inventory.start("inventory")

    def pool(self):
        cc = self.g.ui_inventory.GUI.CC["actor_bag"]
        cells = sum(1 for _ in cc.cell.items()) if cc.cell is not None else 0
        grid = sum(1 for _ in cc.grid.items()) if cc.grid is not None else 0
        return {"cells": cells, "grid": grid, "idxer": int(cc.idxer)}

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

    def report(self):
        """The [alao_prewarm] lines the mod would print to the engine log."""
        n = int(self.lua.eval("#__PRINTED"))
        return [self.lua.eval("__PRINTED")[i] for i in range(1, n + 1)]

    def clear_effects(self):
        self.g.EFFECTS, self.g.EFFN = self.lua.table(), 0

    def set(self, **kw):
        for k, v in kw.items():
            self.g.STATE[k] = v
