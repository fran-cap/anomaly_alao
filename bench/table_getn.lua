-- @pattern table_getn
-- @title table.getn(t) -> #t
-- @status shipped
-- @setup
local t = {}
for i = 1, 64 do t[i] = D[i] end
local acc = 0
-- @original
for i = 1, N do acc = acc + table.getn(t) + D[i % 64 + 1] end
-- @rewrite
for i = 1, N do acc = acc + #t + D[i % 64 + 1] end
-- @sink
acc
