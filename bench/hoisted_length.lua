-- @pattern hoisted_length
-- @title for i = 1, #t -> local n = #t; for i = 1, n
-- @status proposed
-- @doc 1.00 1.00
-- @iters 5 20 100 2000
-- @doc_at 2000
-- @notes Lua evaluates the numeric-for limit once anyway, so this should be a no-op; it is in the table as a negative control.
-- @setup
local t = {}
for i = 1, K do t[i] = D[i % 64 + 1] end
local acc = 0
-- @original
for r = 1, N do
  local s = 0
  for i = 1, #t do s = s + t[i] end
  acc = acc + s
end
-- @rewrite
for r = 1, N do
  local s = 0
  local n = #t
  for i = 1, n do s = s + t[i] end
  acc = acc + s
end
-- @sink
acc
