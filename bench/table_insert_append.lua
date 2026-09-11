-- @pattern table_insert_append
-- @title table.insert(t,v) -> t[#t+1]=v
-- @status shipped
-- @doc 1.00 1.35
-- @iters 5 20 100 2000
-- @doc_at 2000
-- @notes doc gives the interpreted figure as a range 1.2-1.5x; 1.35 is the midpoint
-- @setup
local acc = 0
-- @original
for r = 1, N do
  local t = {}
  for i = 1, K do table.insert(t, D[i % 64 + 1]) end
  acc = acc + #t
end
-- @rewrite
for r = 1, N do
  local t = {}
  for i = 1, K do t[#t + 1] = D[i % 64 + 1] end
  acc = acc + #t
end
-- @sink
acc
