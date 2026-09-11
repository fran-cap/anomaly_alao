-- @pattern string_concat_in_loop
-- @title s = s .. x in a loop -> table.concat
-- @status shipped
-- @doc 8.69 7.89
-- @iters 3 20 100 2000
-- @doc_at 2000
-- @n 200000 60000
-- @notes I-039 found this is a regression at small K (0.57x at 3 iterations) and only reaches the doc figure at thousands; that is the whole point of @iters. Work here is O(K^2) per outer rep, so the budget is deliberately small.
-- @setup
local w = "stalker,"
local acc = 0
-- @original
for r = 1, N do
  local s = ""
  for i = 1, K do s = s .. w end
  acc = acc + #s
end
-- @rewrite
for r = 1, N do
  local buf = {}
  for i = 1, K do buf[#buf + 1] = w end
  local s = table.concat(buf)
  acc = acc + #s
end
-- @sink
acc
