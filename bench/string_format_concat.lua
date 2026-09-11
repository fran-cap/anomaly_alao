-- @pattern string_format_concat
-- @title string.format("%s: %d", ...) -> .. concat
-- @status proposed
-- @doc 0.56 0.57
-- @n 200000 100000
-- @notes a negative control: the doc says this "optimization" is a 1.8x regression in both modes, so it must stay out of ALAO.
-- @setup
local parts = {}
-- @original
for i = 1, N do parts[i] = string.format("%s: %d", "hp", i) end
-- @rewrite
for i = 1, N do parts[i] = "hp" .. ": " .. i end
-- @sink
#parts + #parts[1]
