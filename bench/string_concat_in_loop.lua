-- @pattern string_concat_in_loop
-- @title s = s .. x in a loop -> counter + table.concat(parts, "", 1, n)
-- @status shipped
-- @doc 8.69 7.89
-- @iters 3 5 10 20 30 50 68 100 200 1000 2000
-- @doc_at 200
-- @n 200000 60000
-- @notes The rewrite arm is the form ALAO emits as of I-039's b4726fe: a hoisted counter plus an explicit table.concat range, NOT the older p[#p+1] shape. The explicit 1,n range is load-bearing, not cosmetic - with a counter a nil operand leaves a hole, and a bare table.concat(p) would stop at #p and silently return a truncated string, where the range raises like `..` does.
-- @notes @doc_at is 200, not the largest K, because the beam's 8.69x/7.89x never said what loop length it used and this is a curve, not a number. K=200 is the nearest sweep point to where the measured curve crosses the doc's pair - the crossing sits between K=100 and K=200 and the JIT side of it is jittery on a busy machine. At K=2000 the same transform is ~39x/~30x and at K=3 it is 0.44x. Treat the doc row as "the number you get at a couple of hundred iterations", not as the transform's value.
-- @notes Work here is O(K^2) per outer rep, so the budget is deliberately small. K=68 is in the sweep because it is the only literal trip count of this shape that occurs in the enabled GAMMA corpus (three `for i=1,68` sites in zzz_player_injuries.script).
-- @setup
local w = "stalker,"
local acc = 0
-- @original
for r = 1, N do
  local s = ""
  for i = 1, K do s = s .. w end
  acc = acc + #s
end
-- @rewrite
for r = 1, N do
  local _s_parts, _s_n = {}, 0
  for i = 1, K do
    _s_n = _s_n + 1; _s_parts[_s_n] = w
  end
  local s = table.concat(_s_parts, "", 1, _s_n)
  acc = acc + #s
end
-- @sink
acc
