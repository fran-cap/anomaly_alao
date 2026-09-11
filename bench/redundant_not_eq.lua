-- @pattern redundant_not_eq
-- @title not (a == b) -> a ~= b
-- @status shipped
-- @setup
local hits = 0
-- @original
for i = 1, N do local x = D[i % 64 + 1] if not (x == 0.5) then hits = hits + 1 end end
-- @rewrite
for i = 1, N do local x = D[i % 64 + 1] if x ~= 0.5 then hits = hits + 1 end end
-- @sink
hits
