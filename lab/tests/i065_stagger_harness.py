"""Stub-engine harness for the I-065 squad first-update stagger.

Same shape as `i063_prewarm_harness`: a stub Anomaly engine in `lupa.luajit20`
with `zzz_alao_squad_stagger.script` as the thing under test.

What the stub models, because it is what the captures show:

  * a board of squads (`SIMBOARD.squads[id] = true`, `alife_object(id)`), each
    with the real class's `first_update` flag and an `update()` shaped like
    `sim_squad_scripted:update`: the first-update block (flag, callback), then
    a target search IF the squad has no target, which is the expensive part;
  * the target search is order-sensitive on purpose (it takes the least
    populated smart and bumps its population), so a test can tell whether the
    mod changes WHO gets WHAT compared with the engine's own id-order sweep;
  * the ALife scheduler in its two observed moods: mode A sweeps every squad in
    the first-update frame, mode B visits K squads per tick, one tick every T
    frames, starting some frames after the load;
  * a simulated clock.  `os.clock` inside the mod's environment reads it, a
    searching update advances it by COST_SEARCH ms and a cheap one by COST_CHEAP.
    The per-frame ledger FRAME_MS is what a frame-time trace would show.

Wall-clock is not measured anywhere.  The costs are the measured ones
(2-4.7 ms searching, 0.05 ms not) and the claims under test are structural:
which frame the work lands in, that every squad gets exactly one first update,
and that nobody's target changes.
"""
from __future__ import annotations

from pathlib import Path

from lupa import luajit20 as lupa

REPO = Path(__file__).resolve().parent.parent.parent
MOD_ROOT = REPO / "lab" / "mods" / "alao-squad-stagger"
MOD = MOD_ROOT / "gamedata" / "scripts" / "zzz_alao_squad_stagger.script"

# the live copy in both arms of the I-065 comparison: ZCP's file, ALAO-rewritten
LIVE_SQUAD = Path(r"C:\code\GIT\anomaly_alao\lab\coord\overlays\agent-I063-b"
                  r"\gamedata\scripts\sim_squad_scripted.script")

PRELUDE = r"""
CLOCK_MS = 0
FRAME = 0
FRAME_MS = {}             -- frame -> ms of squad work that landed in it
LOG = {}                  -- printf lines
UPDATES = {}              -- array of {id=, frame=, first=, searched=, by=}
FIRST_CB = {}             -- id -> times squad_on_first_update fired
CALLER = "engine"
COST_SEARCH = 3.0
COST_CHEAP = 0.05
NSMARTS = 7

-- Anomaly's printf / strformat: %s is the ONLY directive.  The real ones leave
-- anything else in the output as literal text and shift the arguments; the stub
-- raises instead, so a %d or %.1f in a log call fails the test that reaches it.
BAD_FORMATS = {}
function strformat(fmt, ...)
    fmt = tostring(fmt)
    local stripped = fmt:gsub("%%s", "")
    if stripped:find("%%") then
        BAD_FORMATS[#BAD_FORMATS + 1] = fmt
        error("printf stub: only %s is substituted in game, got: " .. fmt, 0)
    end
    local args, i = {...}, 0
    return (fmt:gsub("%%s", function() i = i + 1 return tostring(args[i]) end))
end
function printf(fmt, ...)
    LOG[#LOG + 1] = strformat(fmt, ...)
end

CALLBACKS = {}
function RegisterScriptCallback(name, f)
    CALLBACKS[name] = CALLBACKS[name] or {}
    local t = CALLBACKS[name]
    t[#t + 1] = f
end
function UnregisterScriptCallback(name, f)
    local t = CALLBACKS[name]
    if not t then return end
    for i = #t, 1, -1 do
        if t[i] == f then table.remove(t, i) end
    end
end
function SendScriptCallback(name, ...)
    local t = CALLBACKS[name]
    if not t then return end
    local copy = {}
    for i = 1, #t do copy[i] = t[i] end
    for i = 1, #copy do copy[i](...) end
end
function listeners(name) return CALLBACKS[name] and #CALLBACKS[name] or 0 end

-- the board
OBJECTS = {}
SIMBOARD = {squads = {}, population = {}}
for s = 1, NSMARTS do SIMBOARD.population[s] = 0 end
function alife_object(id) return OBJECTS[id] end

db = {actor = {position = function() return {x = 0, y = 0, z = 0} end}}

local function spend(ms)
    CLOCK_MS = CLOCK_MS + ms
    FRAME_MS[FRAME] = (FRAME_MS[FRAME] or 0) + ms
end

local function squad_update(self)
    if self.explode_before_flag then error("boom " .. self.id, 0) end
    local first = false
    if not self.first_update then
        self.first_update = true
        first = true
        FIRST_CB[self.id] = (FIRST_CB[self.id] or 0) + 1
    end
    if self.explode then
        error(type(self.explode) == "string" and self.explode or ("boom " .. self.id), 0)
    end
    local searched = false
    if not self.target then
        searched = true
        spend(COST_SEARCH)
        local best, bestpop = nil, math.huge
        for s = 1, NSMARTS do
            if SIMBOARD.population[s] < bestpop then best, bestpop = s, SIMBOARD.population[s] end
        end
        self.target = best
        SIMBOARD.population[best] = bestpop + 1
    else
        spend(COST_CHEAP)
    end
    UPDATES[#UPDATES + 1] = {id = self.id, frame = FRAME, first = first,
                             searched = searched, by = CALLER}
end

-- dist: metres from the actor.  has_target: came back from the save with one.
function add_squad(id, dist, has_target)
    local sq = {id = id, first_update = false, update = squad_update,
                target = has_target and 1 or nil}
    sq.position = {distance_to_sqr = function(_, p) return dist * dist end}
    OBJECTS[id] = sq
    SIMBOARD.squads[id] = true
    return sq
end

-- something on the board that is not a sim_squad_scripted: no first_update field
function add_foreign(id)
    OBJECTS[id] = {id = id, update = function() error("must never be called") end}
    SIMBOARD.squads[id] = true
end

-- the scheduler
ENGINE_CURSOR = 0
local function sorted_ids()
    local ids = {}
    for id in pairs(SIMBOARD.squads) do ids[#ids + 1] = id end
    table.sort(ids)
    return ids
end
function engine_visit(k)
    local ids = sorted_ids()
    if #ids == 0 then return end
    CALLER = "engine"
    for _ = 1, k do
        ENGINE_CURSOR = ENGINE_CURSOR % #ids + 1
        local o = OBJECTS[ids[ENGINE_CURSOR]]
        if o and o.first_update ~= nil then
            local ok, err = pcall(o.update, o)
            if not ok and not (o.explode or o.explode_before_flag) then
                error("stub engine: unexpected error in update: " .. tostring(err), 0)
            end
        end
    end
end
function engine_sweep() engine_visit(#sorted_ids()) end

function fire(name)
    CALLER = "script"
    SendScriptCallback(name)
    CALLER = "engine"
end

function load_script(src, name, clockless)
    local env = setmetatable({}, {__index = _G})
    if clockless then
        env.os = {}
    else
        env.os = {clock = function() return CLOCK_MS / 1000 end}
    end
    local chunk = assert(loadstring(src, "@" .. name))
    setfenv(chunk, env)
    chunk()
    return env
end
"""


class World:
    """One level load.  `mode` is what the engine's scheduler does by itself."""

    FIRST_UPDATE_FRAME = 8
    KEY_PROMPT_FRAME = 63

    def __init__(self, with_mod=True, mode="B", squads=60, searching_every=2,
                 tick_every=90, per_tick=20, engine_starts_at=400, clockless=False,
                 config=None, sweep_before_listener=True):
        self.lua = lupa.LuaRuntime(unpack_returned_tuples=True)
        self.lua.execute(PRELUDE)
        self.g = self.lua.globals()
        self.mode = mode
        self.tick_every = tick_every
        self.per_tick = per_tick
        self.engine_starts_at = engine_starts_at
        self.sweep_before_listener = sweep_before_listener
        self.with_mod = with_mod
        self.mod = None
        for i in range(squads):
            # ids 100.., distance grows with id, every n-th squad has no target
            self.g.add_squad(100 + i, 10.0 * (i + 1), (i % searching_every) != 0)
        if with_mod:
            self.mod = self.g.load_script(MOD.read_text(encoding="utf-8"), MOD.name, clockless)
            if config:
                self.mod._alao_config(self.lua.table_from(config))
            self.mod.on_game_start()

    # -- driving ---------------------------------------------------------
    def load(self):
        """Frames 1..8: the first-update frame, with the engine's sweep in mode A."""
        self.g.FRAME = self.FIRST_UPDATE_FRAME
        if self.mode == "A" and self.sweep_before_listener:
            self.g.engine_sweep()
        self.g.fire("actor_on_first_update")
        if self.mode == "A" and not self.sweep_before_listener:
            self.g.engine_sweep()
        return self

    def frames(self, n):
        for _ in range(n):
            self.g.FRAME = self.g.FRAME + 1
            f = self.g.FRAME
            self.g.fire("actor_on_update")
            if f >= self.engine_starts_at and (f - self.engine_starts_at) % self.tick_every == 0:
                self.g.engine_visit(self.per_tick)
        return self

    # -- reading ---------------------------------------------------------
    def log(self):
        return [self.g.LOG[i] for i in range(1, len(self.g.LOG) + 1)]

    def updates(self):
        out = []
        for i in range(1, len(self.g.UPDATES) + 1):
            u = self.g.UPDATES[i]
            out.append({"id": u.id, "frame": u.frame, "first": bool(u.first),
                        "searched": bool(u.searched), "by": u.by})
        return out

    def frame_ms(self):
        return {int(k): float(v) for k, v in self.g.FRAME_MS.items()}

    def in_play_ms(self):
        return {f: ms for f, ms in self.frame_ms().items() if f > self.KEY_PROMPT_FRAME}

    def targets(self):
        return {int(i): o.target for i, o in self.g.OBJECTS.items() if o.first_update is not None}

    def first_cb(self):
        return {int(k): int(v) for k, v in self.g.FIRST_CB.items()}

    def state(self):
        s = self.mod._alao_state()
        return {k: s[k] for k in s}

    def squad_ids(self):
        return sorted(int(i) for i, o in self.g.OBJECTS.items() if o.first_update is not None)
