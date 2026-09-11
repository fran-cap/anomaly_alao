-- @pattern vector_alloc_in_loop
-- @title vector() allocated per iteration -> one reused scratch vector
-- @status proposed
-- @doc 1.01 10.47
-- @notes I-009. ALAO reports vector_alloc_in_loop today but never rewrites it.
-- @setup
local mt = {}
mt.__index = mt
function mt:set(x, y, z) self.x = x; self.y = y; self.z = z; return self end
local function vector() return setmetatable({x = 0, y = 0, z = 0}, mt) end
local acc = 0
-- @original
for i = 1, N do
  local v = vector():set(D[i % 64 + 1], 1, 2)
  acc = acc + v.x + v.z
end
-- @rewrite
local scratch = vector()
for i = 1, N do
  local v = scratch:set(D[i % 64 + 1], 1, 2)
  acc = acc + v.x + v.z
end
-- @sink
acc
