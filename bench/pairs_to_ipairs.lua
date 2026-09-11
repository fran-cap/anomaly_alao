-- @pattern pairs_to_ipairs
-- @title pairs(t) -> ipairs(t) over a pure array
-- @status proposed
-- @doc 5.90 0.29
-- @iters 5 20 100 2000
-- @doc_at 2000
-- @notes the sharpest mode split in the beam: a big JIT win and a big interpreter loss. The array is read-only, so it is built in @setup and never enters the timed region.
-- @notes Re-measured 2026-09-11 (I-005): jit_on 25.1x/30.0x at K=1/2 (the loop barely exists), then
-- @notes 3.29x at K=3, 3.79x at K=5, 5.71x at K=20, 7.23x at K=100, 6.02x at K=2000.
-- @notes jit_off 0.70x at K=1 falling monotonically to 0.28x at K=2000 - the interpreted arm NEVER
-- @notes crosses 1.0x, so there is no crossover K to quote. G2 therefore fails at every K, and the
-- @notes pattern is only allowed where the body provably compiles. See pairs_to_ipairs_nyi_body.lua
-- @notes for the companion case: with an aborting call already in the loop the swap is 0.97x-0.98x.
-- @notes Every figure above was taken twice, once before and once after the 19:35-20:45Z FPS capture
-- @notes held the game lock, and reproduced within noise (3.79/3.82, 5.71/5.71, 7.23/7.21, 6.02/6.02).
-- @setup
local t = {}
for i = 1, K do t[i] = D[i % 64 + 1] end
local acc = 0
-- @original
for r = 1, N do
  local s = 0
  for _, v in pairs(t) do s = s + v end
  acc = acc + s
end
-- @rewrite
for r = 1, N do
  local s = 0
  for _, v in ipairs(t) do s = s + v end
  acc = acc + s
end
-- @sink
acc
