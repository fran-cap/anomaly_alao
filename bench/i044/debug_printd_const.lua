-- @pattern debug_printd_const
-- @title live per-frame shape 1: printd(0, "actor_on_update") with the flag off -> statement removed
-- @status proposed
-- @notes Shape taken from warfare.script actor_on_update line 273, which IS registered in a stock GAMMA profile (warfare.on_game_start registers actor_on_update unconditionally) and therefore runs once per frame. `printd` is warfare.script's own Lua function: it reads warfare_options.options.debug_logging and returns. Nothing reaches the engine log.
-- @notes The "rewrite" arm is what --fix-debug produces: the whole statement commented out. So the speedup column is "cost of keeping this site", not a transform ratio.
-- @notes Interpreted is the operative mode: every live per-frame body holding one of these is classified interpreted or mixed.
-- @setup
_G.warfare_options = { options = { debug_logging = false } }
function _G.printd(e, msg)
  if warfare_options.options.debug_logging then
    _G.__d = msg
  end
end
local acc = 0
-- @original
for r = 1, N do
  printd(0, "actor_on_update")
  acc = acc + D[r % 64 + 1]
end
-- @rewrite
for r = 1, N do
  --printd(0, "actor_on_update")
  acc = acc + D[r % 64 + 1]
end
-- @sink
acc
