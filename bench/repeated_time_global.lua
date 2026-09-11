-- @pattern repeated_time_global
-- @title N x time_global() in one body -> local tg = time_global()
-- @status shipped
-- @iters 2 3 4 7
-- @doc_at 7
-- @notes K here is the number of time_global() calls in ONE callback body, not a loop length: the corpus n distribution is 2:130 3:43 4:22 5:4 6:2 7:4 on GAMMA. One outer iteration = one callback invocation.
-- @notes the stub is a plain Lua closure returning an upvalue, which is the CHEAPEST possible stand-in: the JIT can inline and CSE it, and the interpreter still pays only a Lua call. The engine's time_global() is a lua_CFunction reading Device.dwTimeGlobal, which costs more and additionally aborts the trace. So these numbers are a LOWER bound on the real saving.
-- @setup
local now = 12345
_G.time_global = function() return now end
local acc = 0
-- @original
for r = 1, N do
  local s = 0
  for j = 1, K do s = s + time_global() end
  acc = acc + s * D[r % 64 + 1]
end
-- @rewrite
for r = 1, N do
  local tg = time_global()
  local s = 0
  for j = 1, K do s = s + tg end
  acc = acc + s * D[r % 64 + 1]
end
-- @sink
acc
