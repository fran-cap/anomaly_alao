-- @pattern pow_op_simple
-- @title x^2 -> x*x
-- @status shipped
-- @setup
local acc = 0
-- @original
for i = 1, N do local x = D[i % 64 + 1] acc = acc + x ^ 2 end
-- @rewrite
for i = 1, N do local x = D[i % 64 + 1] acc = acc + x * x end
-- @sink
acc
