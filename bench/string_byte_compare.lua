-- @pattern string_byte_compare
-- @title string.sub(s,1,1) == "c" -> string.byte(s,1) == 99
-- @status proposed
-- @doc 1.00 1.10
-- @setup
local words = {"stalker", "zone", "sun", "artifact"}
local hits = 0
local acc = 0
-- @original
for i = 1, N do
  local w = words[i % 4 + 1]
  if string.sub(w, 1, 1) == "s" then hits = hits + 1 end
  acc = acc + D[i % 64 + 1]
end
-- @rewrite
for i = 1, N do
  local w = words[i % 4 + 1]
  if string.byte(w, 1) == 115 then hits = hits + 1 end
  acc = acc + D[i % 64 + 1]
end
-- @sink
hits + acc
