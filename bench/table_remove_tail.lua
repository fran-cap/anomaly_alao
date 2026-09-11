-- @pattern table_remove_tail
-- @title table.remove(t) tail pop -> t[n]=nil; n=n-1
-- @status proposed
-- @doc 15.57 4.31
-- @iters 5 20 100 2000
-- @doc_at 2000
-- @notes the arm destroys the table, so the K-element refill has to sit inside the timed region in BOTH arms. That shared cost dilutes the ratio - read this row as a lower bound on the win, not as the doc's undiluted 15.57x.
-- @setup
local acc = 0
-- @original
for r = 1, N do
  local t = {}
  for i = 1, K do t[i] = D[i % 64 + 1] end
  local s = 0
  while #t > 0 do s = s + table.remove(t) end
  acc = acc + s
end
-- @rewrite
for r = 1, N do
  local t = {}
  for i = 1, K do t[i] = D[i % 64 + 1] end
  local s = 0
  local n = K
  while n > 0 do s = s + t[n]; t[n] = nil; n = n - 1 end
  acc = acc + s
end
-- @sink
acc
