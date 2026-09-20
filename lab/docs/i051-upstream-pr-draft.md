# Upstream PR draft — cache `make_callback`'s dispatch order

**Status: DRAFT. Nothing has been opened, pushed, forked or posted anywhere. Publishing is
the user's call.** This file is the text to paste if and when they want it opened, plus the
homework behind it.

Written 2026-09-20 by agent-I051 (idea I-051, delivering I-043).

---

## 1. Where the lines live, and what I could and could not verify

| question | answer | how |
|---|---|---|
| Target repo | `themrdemonized/xray-monolith` ("X-Ray Monolith Edition", the Anomaly modded exes) | its GitHub page |
| Target branch | `all-in-one-vs2022-wpo` (the default branch) | repo landing page |
| Target file | `gamedata/scripts/axr_main.script` | GitHub contents API listing of `gamedata/scripts` (101 files; `axr_main.script` and `_g_patches.script` present) |
| Where the priority system came from | PR #339 "Callback Priority System" by **Kutez**, merged 2025-08-23 into branch `themrdemonized:kutez`. It added **two** files: `gamedata/configs/unlocalizers/unlocalizer_modded_exes_axr_main.ltx` (+2) and `gamedata/scripts/axr_main_patches.script` (+86). It did **not** touch `axr_main.script`. | GitHub PR files API |
| Where they live now | inlined into `gamedata/scripts/axr_main.script` on the default branch, under the comment `-- Kutez: Callback Priority System (…/pull/339)`. `axr_main_patches.script` is **not** in the default branch's `gamedata/scripts` listing. | raw file + contents API |
| Is the file we measured the same file? | **Yes, for the region that matters.** The loose `Anomaly/gamedata/scripts/axr_main.script` in the GAMMA install and the upstream default-branch file differ in **exactly one line** — a comment listing the params of `map_spot_menu_property_clicked` — and the region this patch touches is **byte-identical**: 1758 bytes, sha256 `0ded82c38b765181b60c58508a1b59d2364c9681c9ed1a8e209ba8e997d7a99b`. Upstream is LF, the shipped GAMMA copy is CRLF. | `lab/tools/i051_upstream_patch.py`, scratch diff |

**Not verified.** Whether the maintainers would rather have this as a change to
`axr_main.script` or as a new/restored `axr_main_patches.script` in the PR-#339 style
(which would let it ship without editing a vanilla Anomaly file — that is evidently the
convention the unlocalizer mechanism exists for). No `CONTRIBUTING.md` exists in the repo;
there is no stated PR process beyond "open one". Also not verified: whether upstream's
`_g_patches.script` `spairs` override is identical to the loose GAMMA one (the measurement
assumed the GAMMA copy, which is where the `hspairs` min-heap comes from). **Ask the
maintainers which form they want before opening anything.**

Whether Anomaly proper (the base game, not the modded exes) also wants this is a separate
question: base Anomaly's `axr_main.script` dispatches with a plain `pairs()` loop and has
no priority system at all, so this change does not apply there unmodified.

## 2. The diff

Regenerate with:

```
py -3.12 lab/tools/i051_upstream_patch.py --src upstream --diff
```

Every substitution is asserted to hit exactly once, so a file that has drifted fails loudly
rather than emitting a half-patched result. The patched file compiles under LuaJIT 2.0.

```diff
--- a/gamedata/scripts/axr_main.script
+++ b/gamedata/scripts/axr_main.script
@@ -238,6 +238,35 @@
 	next_index[name] = 1
 end
 
+-- Cached dispatch order.
+-- make_callback used to call spairs() on every SendScriptCallback. With the
+-- modded-exes _g_patches loaded that is hspairs, a min-heap: per dispatch a
+-- keys array, a safe_order closure, an iterator closure, an O(K) heapify and
+-- then an O(log K) sift-down per listener whose every comparison is two nested
+-- Lua calls - all to walk a table that only changes when somebody registers or
+-- unregisters. actor_on_update has ~100 listeners and fires every frame.
+-- So keep the sorted order and rebuild it where the table is mutated.
+-- order_list[name] is REPLACED, never edited in place, so a dispatch that is
+-- already running keeps iterating its own snapshot - the same thing hspairs
+-- gets from collecting its keys before the first listener runs.
+local order_list = {}
+
+local function rebuild_order(name)
+	local t = intercepts[name]
+	if (not t) then
+		order_list[name] = nil
+		return
+	end
+	local keys, n = {}, 0
+	for k in pairs(t) do
+		n = n + 1
+		keys[n] = k
+	end
+	table.sort(keys, function(a,b) return t[a] < t[b] end)
+	order_list[name] = keys
+	return keys
+end
+
 -----------------------------------------------------------
 -- Global Callback Register
 -- param 1 - name as type<string> (ie. intercepts[name])
@@ -247,6 +276,7 @@
 	if (not intercepts[name]) then
 		intercepts[name] = {}
 		next_index[name] = 1
+		rebuild_order(name)
 	else
 		printf("![axr_main callback_add] callback %s already exists!",name)
 		callstack()
@@ -262,6 +292,7 @@
 	if (intercepts[name]) then
 		intercepts[name][func_or_userdata] = next_index[name]
 		next_index[name] = next_index[name] + 1
+		rebuild_order(name)
 	else
 		printf("![axr_main callback_set] callback %s doesn't exist!",name)
 		callstack()
@@ -271,6 +302,7 @@
 function callback_unset(name,func_or_userdata)
 	if (intercepts[name]) then
 		intercepts[name][func_or_userdata] = nil
+		rebuild_order(name)
 	else
 		printf("![axr_main callback_unset] callback %s doesn't exist!",name)
 		callstack()
@@ -278,12 +310,20 @@
 end
 
 function make_callback(name,...)
-	if (intercepts[name]) then
-		for func_or_userdata, v in spairs(intercepts[name], sort_func_values_ascend) do 
-			if (type(func_or_userdata) == "function") then 
-				func_or_userdata(...)
-			elseif (func_or_userdata[name]) then
-				func_or_userdata[name](func_or_userdata,...)
+	local t = intercepts[name]
+	if (t) then
+		local list = order_list[name] or rebuild_order(name)
+		for i = 1,#list do
+			local func_or_userdata = list[i]
+			-- hspairs re-reads t[key] on every step and skips the entry when it
+			-- has gone nil, so a listener unregistered by an earlier listener in
+			-- THIS pass never fires. Same guard here.
+			if (t[func_or_userdata] ~= nil) then
+				if (type(func_or_userdata) == "function") then 
+					func_or_userdata(...)
+				elseif (func_or_userdata[name]) then
+					func_or_userdata[name](func_or_userdata,...)
+				end
 			end
 		end
 	else
```

---

## 3. PR body (the text to paste)

### Title

`axr_main: cache make_callback's dispatch order instead of re-sorting every dispatch`

### Body

`_g.SendScriptCallback` funnels every scripted callback in the game into
`axr_main.make_callback`, and that function sorts its listener table on **every** call:

```lua
for func_or_userdata, v in spairs(intercepts[name], sort_func_values_ascend) do
```

With `_g_patches.script` loaded, `spairs` is `hspairs` — a min-heap — because the order
function is not `sort_func_keys_ascend`. So per dispatch, before a single listener runs:
a K-element keys array is allocated, a `safe_order` closure is allocated, an iterator
closure is allocated, the array is heapified in O(K), and then each listener costs an
O(log K) sift-down whose every comparison is two nested Lua calls and four table index
operations. All of it to walk a table that only changes when somebody calls
`RegisterScriptCallback` or `UnregisterScriptCallback`.

The sort key is `next_index`, a per-callback-name monotone counter, so the order is
registration order and ties are impossible. That order can simply be kept.

This PR adds `order_list`, rebuilt in `callback_add` / `callback_set` / `callback_unset`
and **replaced** rather than edited in place, and turns the dispatcher into a numeric
`for` over it.

#### Why it is worth doing

Measured on a S.T.A.L.K.E.R. GAMMA 0.9.4 install with a script-side profiler that wraps
`make_callback` and reads the engine's `profile_timer`. 4 launches per arm, 120 s each,
first round of each arm dropped, standing still on one fixed save, the two arms differing
in exactly one file:

| | before | after | delta |
|---|---|---|---|
| total script time | 712.3 us/frame | 607.3 us/frame | **-105.1 us/frame (-14.8%)** |
| `actor_on_update` | 667.9 us | 571.2 us | -96.7 us (-14.5%) |
| per-run means | 700 / 713 / 716 / 720 | 588 / 593 / 607 / 622 | no overlap between the arms |

(Including the first round of each arm, which runs ~10% high and the protocol drops, the
same run reads 712.6 -> 602.5, -110.1 us, -15.45%.)

The saving is not confined to the per-frame callbacks: `make_callback` is the funnel, so
**all 7.5 dispatches per frame** get cheaper. Fifteen different callbacks moved, each by
0.9-3.6 us per dispatch at small listener counts, and `actor_on_update` — which has
73-125 registered listeners — by 96.75 us per dispatch.

**And the frame rate did not move**: -1.37% average, +3.41% on the 1% low, -2.1% on the
p99, all inside that harness's ±2% noise band and disagreeing in sign. I am not claiming
an fps win. 105 us off a ~4750 us frame is 2.2%, at the edge of what the harness resolves,
and this was one scene, standing still. What is demonstrated is a reproducible 15%
reduction in scripted CPU time, which is headroom rather than frames on that scene.

#### Semantics

I tried to keep this exactly equivalent, and it is not quite, so here is the whole story.

Unchanged: priority order, arguments, table/userdata listeners dispatched as
`t[name](t, ...)`, all three `printf` + `callstack()` error paths, and the absence of
`pcall` (a listener that errors still aborts the pass — changing that would be a second,
separate change).

Handled deliberately: `hspairs` re-reads `t[key]` on every step and skips an entry whose
value has gone nil, so **a listener unregistered by an earlier listener in the same pass
never fires**. That is the `t[func_or_userdata] ~= nil` guard in the new loop. A patch
without it would change real behaviour — a mod unregistering a peer and expecting it not
to run in the same frame is a normal thing to do.

**The one difference.** `hspairs` drains a heap, and its comparator reads the table's
current values. The moment a listener registers or unregisters anything *for the callback
that is currently dispatching*, the heap invariant is broken and the order of the
listeners that have not run yet becomes an artifact of the heap rather than the priority
order anybody declared. The cached array keeps the priority order. Because that changes
*when* a listener is visited, and a listener unregistered mid-pass is skipped only if the
unregistration happened before its visit, the set of listeners that ran in that one pass
can differ too. From the next dispatch on, the two agree again, and the log lines printed
are identical in every case I could construct.

Measured over 600 randomised scenarios per mode, built to churn mid-pass as hard as
possible (an upper bound on pathology, not a frequency in a real frame):

| listeners churn during the pass | identical | reordered | different set |
|---|---|---|---|
| no mutation during the pass | **600** | 0 | 0 |
| unregister during the pass | 480 | 96 | 24 |
| register during the pass | 140 | 460 | 0 |
| both | 251 | 294 | 55 |

One more thing about that window, which I think settles how much it matters: **the current
order in it is not deterministic.** `hspairs` seeds its heap from `pairs(t)` over a table
keyed by the listener function, so the initial array is in hash (pointer) order. That is
invisible while nothing mutates, because the heap sorts it out regardless. Once the
invariant is broken the pop order of the rest follows that initial layout. Replaying one
scenario thirty times in a single Lua state gives the current dispatcher **14 distinct
orders**, and the cached array **1** — and in all fourteen, the passes after the churning
one are identical. So this is not a defined behaviour being changed; it is an
allocation-dependent order being replaced by the priority order the system declares.

For what it is worth in practice: resolving every `RegisterScriptCallback("X", <name>)` in
the 1350 script files a live GAMMA profile loads to a top-level `function <name>` in the
same file, and searching that body for a register or unregister of `"X"`, finds **two
sites, both the same listener** (`drx_da_main.script` in Dynamic Anomalies Overhaul). It
unregisters itself after it has already run — where both dispatchers agree — and registers
a different function, which is in neither dispatcher's snapshot, so both call the same set
of listeners exactly once and only the order of the rest of that one pass differs. That
scan cannot see inline closures (64 registrations) or listener names it cannot resolve
(586), so it is a floor, not a proof.

Matching the shipped order bit for bit would mean reimplementing the heap, i.e. keeping
the work this change exists to remove. I think the cached array's behaviour is the more
defensible of the two — it is the declared priority order, and the current behaviour is a
partially-invalid heap's output — but it is a change, and it is yours to accept or not.

#### Testing

Differential tests in LuaJIT 2.0 against the current dispatcher (the real
`axr_main.script` source, and `hspairs`/`safe_order`/both `sift_down`s copied verbatim out
of `_g_patches.script`), both dispatchers over the same `intercepts` table so any
difference is the dispatcher alone: dispatch order and arguments at K up to 125, table and
userdata listeners, a table without the method, listeners unregistering themselves and
each other, registration during a dispatch, re-registration, duplicate registration,
re-entrant dispatch of the same name, `callback_add` of a new name during a dispatch, the
`hspairs` one-listener special case, empty callbacks, unknown callback names,
`callback_set(name, nil)`, an erroring listener, and 600-scenario randomised fuzzes per
churn mode. The patched file compiles under LuaJIT 2.0.

Tests and the patch generator: <link to the ALAO repo, if the user wants it linked>.

---

## 4. Notes for us, not for the PR

* **The end-user mod is a different artefact.** `lab/mods/alao-make-callback-dispatch/`
  does the same thing as a monkey patch at `on_game_start` so it redistributes no game
  file and cannot conflict with anything on MO2 priority. The file edit above is only for
  upstream, which owns the file.
* **The mod is lazy, the PR is eager.** The mod sets `order_list[name] = nil` and rebuilds
  on the next dispatch; the PR rebuilds inside `callback_set`. Same dispatch cost, same
  observable behaviour; lazy is cheaper across the ~1800 registrations at load (one sort
  per callback name instead of one per registration) and it is what the mod does because
  it wraps the setters rather than editing them. If the maintainers prefer, the PR can use
  the lazy form too — it is a two-line change.
* **If a maintainer asks for the fps number**, the honest answer is the one in the body:
  no demonstrated frame-rate win on the measured scene, a demonstrated 15% cut in script
  time. Do not let that get softened in a comment thread.
* Standing-still, single-scene, single-save caveat applies to every number above. The
  listener mix in a firefight with 30 NPCs online is different, and this change gets
  *better* there, not worse (more dispatches per frame), but that is an argument, not a
  measurement.
