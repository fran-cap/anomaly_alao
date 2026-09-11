-- @pattern pairs_to_ipairs_nyi_body
-- @title pairs(t) -> ipairs(t) when the loop body already aborts the trace
-- @status proposed
-- @iters 5 20 100
-- @doc_at 20
-- @notes The gate question for I-005. The loop body calls string.format, which is NYIFF on
-- @notes LuaJIT 2.0, so the loop cannot be recorded either way. If ipairs still loses here,
-- @notes the transform is only safe where the pairs call is the ONLY abort in the body.
-- @setup
local t = {}
for i = 1, K do t[i] = D[i % 64 + 1] end
local sfmt = string.format
local acc = 0
-- @original
for r = 1, N do
  local s = 0
  for _, v in pairs(t) do s = s + #sfmt('%d', v) end
  acc = acc + s
end
-- @rewrite
for r = 1, N do
  local s = 0
  for _, v in ipairs(t) do s = s + #sfmt('%d', v) end
  acc = acc + s
end
-- @sink
acc
