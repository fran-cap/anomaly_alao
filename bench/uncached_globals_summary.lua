-- @pattern uncached_globals_summary
-- @title math.floor -> cached local mfloor
-- @status shipped
-- @doc 0.90 1.23
-- @notes the one row the doc has below 1.00x under the JIT
-- @setup
local acc = 0
-- @original
for i = 1, N do acc = acc + math.floor(D[i % 64 + 1] * 1000) end
-- @rewrite
local mfloor = math.floor
for i = 1, N do acc = acc + mfloor(D[i % 64 + 1] * 1000) end
-- @sink
acc
