-- @pattern pairs_to_ipairs
-- @title pairs(t) -> ipairs(t) over a pure array
-- @status proposed
-- @doc 5.90 0.29
-- @iters 5 20 100 2000
-- @doc_at 2000
-- @notes the sharpest mode split in the beam: a big JIT win and a big interpreter loss. The array is read-only, so it is built in @setup and never enters the timed region.
-- @setup
local t = {}
for i = 1, K do t[i] = D[i % 64 + 1] end
local acc = 0
-- @original
for r = 1, N do
  local s = 0
  for _, v in pairs(t) do s = s + v end
  acc = acc + s
end
-- @rewrite
for r = 1, N do
  local s = 0
  for _, v in ipairs(t) do s = s + v end
  acc = acc + s
end
-- @sink
acc
