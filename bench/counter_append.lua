-- @pattern counter_append
-- @title t[#t+1]=v -> hoisted counter n=n+1; t[n]=v
-- @status proposed
-- @doc 12.44 4.80
-- @iters 5 20 100 2000
-- @doc_at 2000
-- @notes I-001. #t is an O(log n) array-boundary search on every append.
-- @setup
local acc = 0
-- @original
for r = 1, N do
  local t = {}
  for i = 1, K do t[#t + 1] = D[i % 64 + 1] end
  acc = acc + #t
end
-- @rewrite
for r = 1, N do
  local t = {}
  local n = 0
  for i = 1, K do n = n + 1; t[n] = D[i % 64 + 1] end
  acc = acc + n
end
-- @sink
acc
