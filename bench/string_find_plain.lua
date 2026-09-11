-- @pattern string_find_plain
-- @title string.find(s, lit) -> string.find(s, lit, 1, true)
-- @status shipped
-- @setup
local s = "this is a long stalker line with the needle somewhere near the end"
local acc = 0
-- @original
for i = 1, N do local p = string.find(s, "needle") acc = acc + p + D[i % 64 + 1] end
-- @rewrite
for i = 1, N do local p = string.find(s, "needle", 1, true) acc = acc + p + D[i % 64 + 1] end
-- @sink
acc
