-- @pattern string_concat_in_loop
-- @title s = s .. x in a loop -> counter + table.concat(parts, "", 1, n)
-- @status shipped
-- @doc 8.69 7.89
-- @iters 3 5 10 20 30 50 68 100 200 1000 2000
-- @doc_at 200
-- @corpus_k 3-10?:15 68:3
-- @corpus_src 20260911-111300-i039-audit
-- @n 200000 60000
-- @notes The rewrite arm is the form ALAO emits as of I-039's b4726fe: a hoisted counter plus an explicit table.concat range, NOT the older p[#p+1] shape. The explicit 1,n range is load-bearing, not cosmetic - with a counter a nil operand leaves a hole, and a bare table.concat(p) would stop at #p and silently return a truncated string, where the range raises like `..` does.
-- @notes agent-I039's finding verbatim (run 20260911-111300-i039-audit): three sites have a literal bound of 68 and pass; the other fifteen have statically unknowable bounds over UI collections that are small by inspection, and none of the eighteen sits in a hot path.
-- @notes The two buckets have DIFFERENT evidential status, which is why 3-10 carries a `?`. The 68:3 bucket is measured - three `for i=1,68` message-padding loops in zzz_player_injuries.script (shipped by 184- Body Health System, 479- Voiced Actor Refined and G.A.M.M.A. Medications Balance), literal bounds read off the source. The 3-10 bucket is domain inference: what that run established is that those fifteen trip counts are statically unknowable (bounds are `bars`, `amt`, `#p-1`, `#t`, `size_table(warnings)`, and pairs/ipairs over `parts`, `t`, `cs.mats`, `blacklist_tbl`, `categories`); 3-10 is a judgement about what those tables hold in Anomaly - weapon parts, ammo recipe ingredients, repair materials, MCM warning lines. Good inference, not a measurement, and the run id does not back it. If anyone instruments the game and finds one of those pairs() loops running 300 times on a modded inventory, the verdict flips for that site.
-- @notes The 68 sites clear G2 (1.31x-2.05x interpreted) and STRING_CONCAT_BREAKEVEN_ITERS is 30 precisely so they keep getting rewritten. The prune rests on the 15, not on "it never wins".
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
