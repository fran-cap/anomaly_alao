-- @pattern distance_to_comparison
-- @title pos:distance_to(t) < n -> distance_to_sqr(t) < n*n
-- @status shipped
-- @doc 1.03 1.46
-- @notes engine vectors are C-side; this is the sqrt proxy, as in the doc
-- @setup
local sqrt = math.sqrt
local mt = {}
mt.__index = mt
function mt:distance_to(o)
  local dx, dy, dz = self.x - o.x, self.y - o.y, self.z - o.z
  return sqrt(dx * dx + dy * dy + dz * dz)
end
function mt:distance_to_sqr(o)
  local dx, dy, dz = self.x - o.x, self.y - o.y, self.z - o.z
  return dx * dx + dy * dy + dz * dz
end
local function vec(x, y, z) return setmetatable({x = x, y = y, z = z}, mt) end
local a, b = vec(0, 0, 0), vec(1, 2, 3)
local hits = 0
-- @original
for i = 1, N do
  b.x = D[i % 64 + 1] * 10
  if a:distance_to(b) < 5 then hits = hits + 1 end
end
-- @rewrite
for i = 1, N do
  b.x = D[i % 64 + 1] * 10
  if a:distance_to_sqr(b) < 25 then hits = hits + 1 end
end
-- @sink
hits
