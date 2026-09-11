-- @pattern pow_op_half_to_hoisted_sqrt
-- @title x^0.5 -> local sqrt = math.sqrt; sqrt(x)
-- @status proposed
-- @notes I-012: the residual step, with the lookup hoisted.
-- @setup
local acc = 0
local sqrt = math.sqrt
-- @original
for i = 1, N do local x = D[i % 64 + 1] acc = acc + x ^ 0.5 end
-- @rewrite
for i = 1, N do local x = D[i % 64 + 1] acc = acc + sqrt(x) end
-- @sink
acc
