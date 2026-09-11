-- @pattern bare_global_read
-- @title bare global read -> local copy
-- @status shipped
-- @doc 1.00 1.23
-- @notes same analyzer pass as uncached_globals_summary, different shape
-- @setup
_G.gamma_tweak = 3.5
local acc = 0
-- @original
for i = 1, N do acc = acc + gamma_tweak * D[i % 64 + 1] end
-- @rewrite
local gamma_tweak_l = gamma_tweak
for i = 1, N do acc = acc + gamma_tweak_l * D[i % 64 + 1] end
-- @sink
acc
