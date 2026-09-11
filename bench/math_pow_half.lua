-- @pattern math_pow_half
-- @title math.pow(x,0.5) -> x^0.5 (what ALAO does today)
-- @status shipped
-- @doc 1.00 1.05
-- @notes compare against math_pow_half_to_sqrt: same source, far better target
-- @setup
local acc = 0
-- @original
for i = 1, N do local x = D[i % 64 + 1] acc = acc + math.pow(x, 0.5) end
-- @rewrite
for i = 1, N do local x = D[i % 64 + 1] acc = acc + x ^ 0.5 end
-- @sink
acc
