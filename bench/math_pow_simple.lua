-- @pattern math_pow_simple
-- @title math.pow(x,2) -> x*x
-- @status shipped
-- @doc 1.00 1.58
-- @setup
local acc = 0
-- @original
for i = 1, N do local x = D[i % 64 + 1] acc = acc + math.pow(x, 2) end
-- @rewrite
for i = 1, N do local x = D[i % 64 + 1] acc = acc + x * x end
-- @sink
acc
