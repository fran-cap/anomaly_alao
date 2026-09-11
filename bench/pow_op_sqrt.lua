-- @pattern pow_op_sqrt
-- @title x^0.5 -> math.sqrt(x)
-- @status shipped
-- @doc 1.00 2.96
-- @setup
local acc = 0
-- @original
for i = 1, N do local x = D[i % 64 + 1] acc = acc + x ^ 0.5 end
-- @rewrite
for i = 1, N do local x = D[i % 64 + 1] acc = acc + math.sqrt(x) end
-- @sink
acc
