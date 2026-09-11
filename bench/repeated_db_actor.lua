-- @pattern repeated_db_actor
-- @title repeated db.actor index -> local actor
-- @status shipped
-- @notes one loop iteration = one callback invocation, so the cache is per-iteration
-- @setup
_G.db = {actor = {health = 0.5, power = 0.25}}
local acc = 0
-- @original
for i = 1, N do
  acc = acc + db.actor.health + db.actor.power * D[i % 64 + 1] + db.actor.health
end
-- @rewrite
for i = 1, N do
  local actor = db.actor
  acc = acc + actor.health + actor.power * D[i % 64 + 1] + actor.health
end
-- @sink
acc
