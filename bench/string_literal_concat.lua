-- @pattern string_literal_concat
-- @title "a" .. "b" -> "ab" (fold at parse time)
-- @status shipped
-- @setup
local acc = 0
-- @original
for i = 1, N do local s = "sim_" .. "default_" .. "stalker" acc = acc + #s + D[i % 64 + 1] end
-- @rewrite
for i = 1, N do local s = "sim_default_stalker" acc = acc + #s + D[i % 64 + 1] end
-- @sink
acc
