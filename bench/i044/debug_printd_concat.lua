-- @pattern debug_printd_concat
-- @title live per-frame shape 2: printd(0, "squad_on_update: "..squad:name()) -> statement removed
-- @status proposed
-- @notes Shape taken from sim_squad_warfare.script squad_on_update line 53 (and smart_terrain_warfare line 142). The callee returns immediately - the flag is off - but Lua evaluates the arguments first, so the engine getter and the BC_CAT happen on every call regardless.
-- @notes The `:name()` stub is a plain Lua method returning an upvalue string, the CHEAPEST possible stand-in. The real one is a luabind C call that also aborts the trace, so this is a LOWER bound on the site's cost.
-- @notes The concat allocates a new string every call, so this arm also pays GC pressure that best-of-9 with a collect before each run does not fully expose.
-- @setup
_G.warfare_options = { options = { debug_logging = false } }
function _G.printd(e, msg)
  if warfare_options.options.debug_logging then
    _G.__d = msg
  end
end
local squad = { nm = "stalker_sim_squad_novice_2" }
function squad:name() return self.nm end
local acc = 0
-- @original
for r = 1, N do
  printd(0, "squad_on_update: " .. squad:name())
  acc = acc + D[r % 64 + 1]
end
-- @rewrite
for r = 1, N do
  --printd(0, "squad_on_update: " .. squad:name())
  acc = acc + D[r % 64 + 1]
end
-- @sink
acc
