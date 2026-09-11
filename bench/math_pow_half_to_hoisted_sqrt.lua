-- @pattern math_pow_half_to_hoisted_sqrt
-- @title math.pow(x,0.5) -> local sqrt = math.sqrt; sqrt(x)
-- @status proposed
-- @notes I-012: does hoisting the sqrt lookup to a local buy anything on top of
-- @notes the plain math.sqrt(x) retarget? If it does, the new call should be fed
-- @notes to the existing uncached_globals cacher rather than left as math.sqrt.
-- @setup
local acc = 0
local sqrt = math.sqrt
-- @original
for i = 1, N do local x = D[i % 64 + 1] acc = acc + math.pow(x, 0.5) end
-- @rewrite
for i = 1, N do local x = D[i % 64 + 1] acc = acc + sqrt(x) end
-- @sink
acc
