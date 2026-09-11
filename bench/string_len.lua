-- @pattern string_len
-- @title string.len(s) -> #s
-- @status shipped
-- @doc 1.00 1.63
-- @setup
local s = "the zone is quiet today, stalker"
local acc = 0
-- @original
for i = 1, N do acc = acc + string.len(s) + D[i % 64 + 1] end
-- @rewrite
for i = 1, N do acc = acc + #s + D[i % 64 + 1] end
-- @sink
acc
