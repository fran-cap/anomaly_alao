-- @pattern math_pow_half_to_sqrt
-- @title math.pow(x,0.5) -> math.sqrt(x) (proposed retarget)
-- @status proposed
-- @doc 1.00 4.25
-- @setup
local acc = 0
-- @original
for i = 1, N do local x = D[i % 64 + 1] acc = acc + math.pow(x, 0.5) end
-- @rewrite
for i = 1, N do local x = D[i % 64 + 1] acc = acc + math.sqrt(x) end
-- @sink
acc
