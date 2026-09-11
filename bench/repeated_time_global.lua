-- @pattern repeated_time_global
-- @title N x time_global() in one body -> local tg = time_global()
-- @status shipped
-- @iters 2 3 4 7
-- @doc_at 7
-- @notes K here is the number of time_global() calls in ONE callback body, not a loop length: the corpus n distribution is 2:130 3:43 4:22 5:4 6:2 7:4 on GAMMA. One outer iteration = one callback invocation.
-- @notes the stub is a plain Lua closure returning an upvalue, which is the CHEAPEST possible stand-in: the JIT can inline and CSE it, and the interpreter still pays only a Lua call. The engine's time_global() is a lua_CFunction reading Device.dwTimeGlobal, which costs more and additionally aborts the trace. So these numbers are a LOWER bound on the real saving.
-- @notes G2 reads 'fail' only because of the JIT-on column, and that column does not exist for this pattern: time_global() is an engine C call, LuaJIT 2.0 aborts on any non-fastfunc C call, so a body that reads the clock never compiles. The corpus agrees - 127 findings on GAMMA, 122 in interpreted bodies, 5 mixed, 0 compiled. The interpreted column is the whole verdict.
-- @notes where the win is NOT: the modal throttle shape 'if time_global() - last > 500 then last = time_global() end' keeps its second read inside the branch, so on the frames the throttle does not fire there is nothing to save - measured at 1.00x in both modes with a real C-function stub. 45 of the 97 GAMMA findings are that shape; the other 52 have two or more reads on the unconditional path. The guard itself, with a real C-function stub, is 35 ns per frame against 5 ns for the same body with no guard, so the clock read is ~85% of a throttle guard's cost.
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
