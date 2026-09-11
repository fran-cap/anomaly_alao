-- @pattern repeated_method_call_luabind
-- @title same transform, receiver modelled with a C-like __index FUNCTION
-- @status proposed
-- @iters 2 3 4 6 10 20
-- @doc_at 3
-- @n 2000000 300000
-- @notes Companion to repeated_method_call.lua. Here the userdata's __index is a FUNCTION that looks the method up and returns it, which is the shape luabind uses for Anomaly's engine objects (a C __index searching the class registry, then a marshalling dispatch to the C method). Still an underestimate - the lookup here is one Lua table read, luabind's is a C registry walk - but it brackets the true cost from below more tightly than the table-__index model.
-- @notes K is the number of repeats of the same recv:method() inside one callback body, not a loop length. One outer iteration = one callback invocation.
-- @setup
local methods = {id = function(self) return 4242 end}
local o = newproxy(true)
do
  local m = getmetatable(o)
  m.__index = function(self, k) return methods[k] end
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
