-- @pattern string_concat_in_loop
-- @title s = s .. x in a loop -> counter + table.concat(parts, "", 1, n)
-- @status shipped
-- @doc 8.69 7.89
-- @iters 3 5 10 20 30 50 68 100 200 1000 2000
-- @doc_at 200
-- @corpus_k 3-10:15 68:3
-- @corpus_src 20260911-111300-i039-audit
-- @n 200000 60000
-- @notes The rewrite arm is the form ALAO emits as of I-039's b4726fe: a hoisted counter plus an explicit table.concat range, NOT the older p[#p+1] shape. The explicit 1,n range is load-bearing, not cosmetic - with a counter a nil operand leaves a hole, and a bare table.concat(p) would stop at #p and silently return a truncated string, where the range raises like `..` does.
-- @notes The 18 rewritable sites in the enabled GAMMA corpus are bimodal (agent-I039, run 20260911-111300-i039-audit): 15 short UI string builders at K=3-10 with trip counts bounded by pairs/ipairs/expressions, plus 3 literal `for i=1,68` message-padding loops in zzz_player_injuries.script (shipped by 184- Body Health System, 479- Voiced Actor Refined and G.A.M.M.A. Medications Balance). The 68 sites are the only ones with a literal trip count, they are the only ones I-039's gate can reason about, and they clear G2 - STRING_CONCAT_BREAKEVEN_ITERS is 30 precisely so they still get rewritten. The prune rests on the 15, not on "it never wins": those sit below every measured breakeven, and the 3 that win are cold injury-message builders that never reach a frame.
-- @notes @doc_at is 200, not the largest K, because the beam's 8.69x/7.89x never said what loop length it used and this is a curve, not a number. K=200 is the nearest sweep point to where the curve crosses the doc's pair. Two independent sweeps bracket that crossing loosely and do not agree on where it is: this one puts it just under 200, I-039's puts it between 200 and 500 on this form and between 200 and 1000 on the older p[#p+1] form. Both land in the low hundreds, which is all the doc comparison needs; the exact constant is not worth chasing on a shared machine. At K=2000 the same transform is ~39x/~37x and at K=3 it is 0.44x. Treat the doc row as "the number you get at a couple of hundred iterations", not as the transform's value.
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
