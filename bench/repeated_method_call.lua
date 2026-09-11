-- @pattern repeated_method_call
-- @title K repeats of obj:id() in one body -> one call + K-1 local reads
-- @status proposed
-- @iters 2 3 4 6 10 20
-- @doc_at 3
-- @corpus_k 3-6:265 10-20:8
-- @corpus_src 20260911-i021-census
-- @n 3000000 400000
-- @notes Receiver model: a `newproxy(true)` userdata whose metatable __index is a TABLE of Lua closures. That is a LOWER BOUND on the real cost. Anomaly's objects are luabind userdata: __index is a C function that searches the class registry, and the method itself is a C function reached through luabind's argument-marshalling dispatch. So the real `obj:id()` is strictly more expensive than this arm, and the measured speedup understates the transform. See repeated_method_call_luabind.lua for the closer model.
-- @notes One outer iteration = one callback invocation; K is how many times the same `recv:method()` appears in that body, not a loop length. The corpus buckets are bodies, from the I-021 census at threshold 3.
-- @setup
local mt = {__index = {id = function(self) return 4242 end}}
local o = newproxy(true)
do
  local m = getmetatable(o)
  m.__index = mt.__index
end
local acc = 0
-- @original
for r = 1, N do
  local s = 0
  for i = 1, K do s = s + o:id() end
  acc = acc + s + D[r % 64 + 1]
end
-- @rewrite
for r = 1, N do
  local s = 0
  local o_id = o:id()
  for i = 1, K do s = s + o_id end
  acc = acc + s + D[r % 64 + 1]
end
-- @sink
acc
