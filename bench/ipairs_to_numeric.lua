-- @pattern ipairs_to_numeric
-- @title ipairs(t) -> numeric for i = 1, #t
-- @status proposed
-- @doc 1.05 2.89
-- @iters 5 20 100 2000
-- @doc_at 2000
-- @setup
local t = {}
for i = 1, K do t[i] = D[i % 64 + 1] end
local acc = 0
-- @original
for r = 1, N do
  local s = 0
  for _, v in ipairs(t) do s = s + v end
  acc = acc + s
end
-- @rewrite
for r = 1, N do
  local s = 0
  for i = 1, #t do s = s + t[i] end
  acc = acc + s
end
-- @sink
acc
