"""Offline differential harness for I-050a (demonized_ledge_grabbing).

Loads the real mod script - original and patched - into a LuaJIT 2.0 runtime
(lupa.luajit20) on top of a stub Anomaly engine, drives both arms through the
same scripted sequence of camera / actor / MCM states and records, per frame,
both the observable module state (savedClimbPos & friends, which is the entire
contract between checkLedgeGrabbing and tryToClimb / onScreenCheck) and the
ordered log of engine work the arm asked for (ray constructions and queries).

The stubs are deliberately cheap Lua tables, so the log is the right thing to
compare and the wall-clock is NOT: an engine ray_pick query is not a table
lookup. See the report for what the numbers can and cannot say.
"""
from __future__ import annotations

from pathlib import Path

from lupa import luajit20 as lupa

MODDIR = Path(
    r"C:\code\GIT\anomaly_alao\extracted\gamma"
    r"\350- Ledge Grabbing - Demonized\gamedata\scripts"
)
MCM = MODDIR / "demonized_ledge_grabbing_mcm.script"

# --------------------------------------------------------------------------
# The stub engine. Everything demonized_ledge_grabbing.script touches at load
# time or inside checkLedgeGrabbing / checkClimbPrecondition / reset.
PRELUDE = r"""
LOG = {}
LOGN = 0
local function log(s)
    LOGN = LOGN + 1
    LOG[LOGN] = s
end
_G.log_entry = log

local function fmt(n)
    -- 6 decimals: enough to catch a real difference, loose enough that the
    -- two arms' identical float arithmetic compares equal as text.
    return string.format("%.6f", n)
end

-- vector -----------------------------------------------------------------
local vmt = {}
vmt.__index = vmt
function vmt:set(a, b, c)
    if b == nil then self.x, self.y, self.z = a.x, a.y, a.z
    else self.x, self.y, self.z = a, b, c end
    return self
end
function vmt:add(v)
    if type(v) == "number" then self.x, self.y, self.z = self.x+v, self.y+v, self.z+v
    else self.x, self.y, self.z = self.x+v.x, self.y+v.y, self.z+v.z end
    return self
end
function vmt:sub(v)
    if type(v) == "number" then self.x, self.y, self.z = self.x-v, self.y-v, self.z-v
    else self.x, self.y, self.z = self.x-v.x, self.y-v.y, self.z-v.z end
    return self
end
function vmt:mul(k)
    if type(k) == "number" then self.x, self.y, self.z = self.x*k, self.y*k, self.z*k
    else self.x, self.y, self.z = self.x*k.x, self.y*k.y, self.z*k.z end
    return self
end
function vmt:mad(a, b, k)
    -- both engine forms: v:mad(dir, scalar) and v:mad(base, dir, scalar)
    if k == nil then
        self.x, self.y, self.z = self.x+a.x*b, self.y+a.y*b, self.z+a.z*b
    else
        self.x, self.y, self.z = a.x+b.x*k, a.y+b.y*k, a.z+b.z*k
    end
    return self
end
function vmt:invert() self.x, self.y, self.z = -self.x, -self.y, -self.z return self end
function vmt:magnitude() return math.sqrt(self.x*self.x + self.y*self.y + self.z*self.z) end
function vmt:normalize()
    local m = self:magnitude()
    if m > 0 then self.x, self.y, self.z = self.x/m, self.y/m, self.z/m end
    return self
end
function vmt:dotproduct(v) return self.x*v.x + self.y*v.y + self.z*v.z end
function vmt:getP() return -math.asin(self.y) end
function vmt:__tostring() return "("..fmt(self.x)..","..fmt(self.y)..","..fmt(self.z)..")" end
vmt.__tostring = vmt.__tostring
function vector() return setmetatable({x=0, y=0, z=0}, vmt) end
function VEC(x, y, z) return setmetatable({x=x, y=y, z=z}, vmt) end
function vstr(v) if v == nil then return "nil" end return "("..fmt(v.x)..","..fmt(v.y)..","..fmt(v.z)..")" end
function vec_sub(a, b) return VEC(a.x-b.x, a.y-b.y, a.z-b.z) end
function vector_cross(a, b)
    return VEC(a.y*b.z - a.z*b.y, a.z*b.x - a.x*b.z, a.x*b.y - a.y*b.x)
end

-- world / device ---------------------------------------------------------
STATE = {
    cam = VEC(0, 1.7, 0),
    dir = VEC(0, 0, 1),
    actor = VEC(0, 0, 0),
    tg = 1000,
    move_state = {},
    mcm = {},
    -- The static world: nil is open flat ground (the gammabaseline scene -
    -- every downward probe in the 1.4..2.53 m band misses). Otherwise a
    -- half-space of static geometry at z >= ledge_z whose top face is at
    -- y = ledge_y, i.e. a climbable edge. Absolute, like real level geometry:
    -- it does not follow the camera.
    ledge_z = nil,
    ledge_y = 1.5,
}

function device() return {cam_pos = VEC(STATE.cam.x, STATE.cam.y, STATE.cam.z),
                          cam_dir = VEC(STATE.dir.x, STATE.dir.y, STATE.dir.z),
                          time_delta = 0.005} end
function time_global() return STATE.tg end
function IsMoveState(s) return STATE.move_state[s] == true end

db = {actor = {
    position = function(self) return VEC(STATE.actor.x, STATE.actor.y, STATE.actor.z) end,
    direction = function(self) return VEC(STATE.dir.x, 0, STATE.dir.z):normalize() end,
    health = 1.0,
    power = 1.0,
    -- hardcore mode reaches these through getActorWeights()
    get_actor_max_walk_weight = function(self) return 60 end,
    get_total_weight = function(self) return 20 end,
    item_in_slot = function(self, n) return nil end,
    iterate_belt = function(self, f) end,
    cast_Actor = function(self)
        return {conditions = function() return {GetSatiety = function() return 1.0 end} end}
    end,
}}

function normalize(v, mn, mx) if mx == mn then return 0 end return (v-mn)/(mx-mn) end
function clamp(v, mn, mx) if v < mn then return mn end if v > mx then return mx end return v end
function is_empty(t) if not t then return true end return next(t) == nil end
function empty_table(t) for k in pairs(t) do t[k] = nil end return t end
function printf(...) end
function random_float(a, b) return (a+b)*0.5 end
function ini_file() return {line_count = function() return 0 end,
                            r_line_ex = function() return false, nil, nil end} end
function RegisterScriptCallback() end
function UnregisterScriptCallback() end
function AddScriptCallback() end
function SendScriptCallback() end
function getFS() return {file_list_open_ex = function() return {Size = function() return 0 end} end} end
function strformat(s, ...) return s end
VEC_Y = nil
function AddUniqueCall() end
function CreateTimeEvent() end
game = {world2ui = function(v) log("world2ui "..vstr(v)) return VEC(512, 384, 0) end}
ui_mcm = {get = function(path)
    local key = path:match("/(.*)$")
    return STATE.mcm[key]
end, kb_mod_radio = "kb_mod_radio"}
DIK_keys = {DIK_SPACE = 57}

-- geometry ray -----------------------------------------------------------
-- A ray is a miss unless STATE.ledge_at is set and the ray's origin has
-- advanced at least that far along +z from the camera.
local function world_hit(pos, dir, range)
    if not STATE.ledge_z then return nil end
    if pos.z < STATE.ledge_z then return nil end
    if dir.y >= 0 then return nil end          -- upward probes (posCheck) miss
    local d = (pos.y - STATE.ledge_y) / (-dir.y)
    if d < 0 or d > range then return nil end
    return d
end

local raymt = {}
raymt.__index = raymt
function raymt:get(position, direction)
    local p = VEC(position.x, position.y, position.z)
    local d = VEC(direction.x, direction.y, direction.z)
    log("ray:get range="..fmt(self.ray_range).." pos="..vstr(p).." dir="..vstr(d))
    local hit = world_hit(p, d, self.ray_range)
    local distance = hit or self.ray_range
    local res = {}
    res.in_contact = distance <= self.contact_range
    res.position = VEC(p.x + d.x*distance, p.y + d.y*distance, p.z + d.z*distance)
    res.distance = distance
    res.raw_distance = distance
    res.success = hit ~= nil
    res.object = nil
    res.element = nil
    res.result = hit and {material_name = "concrete", material_shoot_factor = 1.0} or nil
    return res
end

demonized_geometry_ray = {}
function demonized_geometry_ray.geometry_ray(args)
    log("geometry_ray{range="..fmt(args.ray_range or args.contact_range)
        ..",flags="..tostring(args.flags)..",ignore="..tostring(args.ignore_object ~= nil).."}")
    local self = setmetatable({}, raymt)
    self.ray_range = args.ray_range or args.contact_range
    self.contact_range = args.contact_range or args.ray_range
    return self
end
function demonized_geometry_ray.get_surface_normal(pos, dir, props, vis, initial_ray)
    log("get_surface_normal "..vstr(pos).." "..vstr(dir).." initial="..tostring(initial_ray ~= nil))
    if initial_ray and not initial_ray.success then return nil end
    if not initial_ray then return nil end
    return VEC(0, 1, 0)          -- a flat, climbable top surface
end

debug_render = {add_object = function() return {cast_dbg_sphere = function() return {} end,
                                                cast_dbg_line = function() return {} end} end}
DBG_ScriptObject = {sphere = 1, line = 2}
function fcolor() return {set = function(self) return self end} end
function matrix() return {identity = function(self) return self end,
                          scale = function(self) return self end,
                          translate = function(self) return self end,
                          mul = function(self) return self end} end
"""

PROBE = "\r\n" + r"""
function __probe()
	return vstr(savedClimbPos), vstr(savedInterPos), vstr(savedCollisionPos),
	       vstr(savedClimbNormal), tostring(climbActive),
	       vstr(savedActorPos), vstr(savedActorDir)
end
""".replace("\n", "\r\n")

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
    """One LuaRuntime with the mod loaded on top of the stub engine."""

    def __init__(self, script: Path):
        self.lua = lupa.LuaRuntime(unpack_returned_tuples=True)
        self.lua.execute(PRELUDE)
        load = self.lua.eval(LOADER)
        load(MCM.read_text(encoding="utf-8"), "demonized_ledge_grabbing_mcm")
        # savedClimbPos & friends are file-scope LOCALS, so the only way to see
        # them is from inside the chunk. Identical scaffolding in both arms.
        src = script.read_text(encoding="utf-8") + PROBE
        self.ns = load(src, "demonized_ledge_grabbing")
        self.state = self.lua.globals().STATE
        self.g = self.lua.globals()
        self._mcm_seeded = False

    # -- driving ---------------------------------------------------------
    def set_mcm(self, **kw):
        # ui_mcm.get() answers out of STATE.mcm, so seed it with the mod's own
        # MCM defaults and then apply the overrides this scenario wants.
        if not self._mcm_seeded:
            defaults = self.ns.load_defaults()
            for k in defaults:
                self.state.mcm[k] = defaults[k]
            self._mcm_seeded = True
        for k, v in kw.items():
            self.state.mcm[k] = v
        self.ns.load_settings()

    def first_update(self):
        self.ns.actor_on_first_update()

    def place(self, cam=None, dirv=None, actor=None, tg=None):
        for name, val in (("cam", cam), ("dir", dirv), ("actor", actor)):
            if val is not None:
                v = getattr(self.state, name)
                v.x, v.y, v.z = val
        if tg is not None:
            self.state.tg = tg

    def clear_log(self):
        self.g.LOG = self.lua.table()
        self.g.LOGN = 0

    def log(self):
        n = int(self.g.LOGN)
        return [self.g.LOG[i] for i in range(1, n + 1)]

    def frame(self):
        self.ns.checkLedgeGrabbing()

    # -- observing -------------------------------------------------------
    def saved(self):
        return tuple(self.ns["__probe"]())


def run_sequence(script: Path, steps, mcm=None):
    """steps: list of dicts passed to place(); returns per-frame (saved, log)."""
    arm = Arm(script)
    if mcm:
        arm.set_mcm(**mcm)
    else:
        arm.set_mcm()
    arm.first_update()
    out = []
    for st in steps:
        pre = st.pop("_pre", None)
        if pre:
            pre(arm)
        arm.place(**st)
        arm.clear_log()
        arm.frame()
        out.append((arm.saved(), arm.log()))
    return arm, out
