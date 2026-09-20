# I-043 — `axr_main.make_callback`: sorted-pairs dispatch -> sorted array

agent-I043, 2026-09-19, branch `agent/gen3-I043`.

**Verdict: SHIP AS A HAND PATCH / UPSTREAM PR. Not an ALAO transform — n = 1.**

**Measured in game** (`20260919-192703-I-043-3f2729`, 4 rounds x 2 arms, arms differing
in exactly one file): script time **712.3 -> 607.3 us/frame, -110.4 us (-15.45%)**, with
no overlap between the arms' per-run means. **The gate is 25 us/frame; this clears it by
4.4x.** `actor_on_update` alone accounts for 96.7 us of it.

**And fps did not move** — -1.37% average, +3.41% on the 1% low, -2.1% p99, all inside
the locked +-2% noise band and disagreeing in sign. The saving is real and reproducible
in script-ms; no frame-rate win is demonstrated. Both halves of that sentence are the
result.

Three things I got wrong, all instructive. My site arithmetic priced **3** dispatches
per frame when `make_callback` is the funnel for **7.49** — patching it makes every
callback cheaper, not just the per-frame ones. The microbench under-predicted the big
dispatch by 1.5-3.5x, with allocation the leading but untested suspect (§6). And I
published a headline saving from a **contended** bench run: 43.1 us at K=73, where the
clean run says **27.6** — a 56% over-statement that stood for several hours, and exactly
what the lab's "never quote a bench taken under a lock" rule is for (§3).

What this is *not* is a pattern: exactly one `spairs(` site in the whole live GAMMA
script set runs per frame, and it is this one.

---

## 1. Which copy is live

This decides everything downstream, so it comes first. Three layers, highest priority
first: enabled MO2 mods in modlist order, then the loose `Anomaly/gamedata/scripts`
(GAMMA patches the base install in place: 62 `.script` files plus 4 `.lua`), then the
Anomaly db archives.
`lab/tools/i043_callback_census.py` does the resolution; 1350 files win (1317 `.script` +
33 `.lua`).

| script | copies that exist | **winner** |
|---|---|---|
| `axr_main.script` | mod `420- Tactical Compass - explorerbee` (**disabled**, line 603 of modlist is `-`), loose, db | **loose** `Anomaly/gamedata/scripts/axr_main.script` |
| `_g.script` | mods `No logs` / `Log spam remover` (both **disabled**), db | **db** (`extracted/vanilla_db`) |
| `_g_patches.script` | loose only | **loose** |

The loose and db copies of `axr_main.script` are *different dispatchers*: the db one
still iterates plain `pairs(intercepts[name])`. Benching the db copy would have
measured code the game never runs.

### The dispatcher, verbatim from the winner

`Anomaly/gamedata/scripts/axr_main.script`, lines 280-293:

```lua
function make_callback(name,...)
	if (intercepts[name]) then
		for func_or_userdata, v in spairs(intercepts[name], sort_func_values_ascend) do 
			if (type(func_or_userdata) == "function") then 
				func_or_userdata(...)
			elseif (func_or_userdata[name]) then
				func_or_userdata[name](func_or_userdata,...)
			end
		end
	else
		printf("![axr_main make_callback] can't make callback to non existing intercept %s!",name)
		callstack()
	end
end
```

with, at lines 235-239 and 256-278 (the Kutez callback-priority system,
[xray-monolith PR #339](https://github.com/themrdemonized/xray-monolith/pull/339)):

```lua
local next_index = {}
for name,v in pairs(intercepts) do
	next_index[name] = 1
end
...
function callback_set(name,func_or_userdata)
	...
	if (intercepts[name]) then
		intercepts[name][func_or_userdata] = next_index[name]
		next_index[name] = next_index[name] + 1
	else ...
```

So the sort key is a **per-callback monotone counter**: the order is registration
order, ties are impossible, and re-registering an existing listener moves it to the
back. `sort_func_values_ascend` (`_g_patches.script:402`) is `t[a] < t[b]`.

### `spairs` is not the `spairs` you think it is

**The correction that mattered.** `_g.script:1923` defines the well-known `table.sort`
`spairs`. The loose `_g_patches.script:1049` then overwrites it:

```lua
-- If some script called spairs without order function, use merge spairs, much faster, otherwise use heap spairs
_G.spairs = function(t, order)
	if order and order ~= sort_func_keys_ascend then
		return hspairs(t, order)
	else
		return mspairs_default(t)
	end
end
```

`make_callback` passes `sort_func_values_ascend`, so the live path is `hspairs`
(`_g_patches.script:802`), a min-heap. `_g.script`'s `spairs` is dead code in GAMMA.
My first bench pair and my first four differential tests were written against it, and
one of them failed when I swapped in the real one — see §4.

### What one dispatch costs, structurally

Per `SendScriptCallback`, before a single listener runs: a `keys` array allocation, a
`pairs()` call through `_g_patches`' metatable-aware wrapper, a `safe_order` **closure
allocation**, an O(K) heapify, and an iterator **closure allocation**; then per listener
an O(log K) sift-down whose every comparison is two nested Lua calls
(`safe_order` -> `sort_func_values_ascend`) and four table index ops. To walk a table
that changes only when someone registers or unregisters.

---

## 2. Listener census (the N that matters)

`py -3.12 lab/tools/i043_callback_census.py` over the 1350 live winners (1317 `.script`
+ 33 `.lua`; agent-I042's independent implementation agrees on the set, and the whole
1350 - 1317 gap was `.lua` inclusion — nothing in the live tree sits in a subdirectory of
`gamedata/scripts`, so basename keying is safe). The 33 `.lua` files hold 22
`RegisterScriptCallback` sites and **0** `spairs(` sites, none of them for a per-frame
callback, so they move nothing that matters here.
1818 `RegisterScriptCallback` sites total. "permanent" = registered from the module's
`on_game_start` (which `axr_main` auto-runs for every script) and never unregistered
anywhere in that file; the rest is churn, registered on demand or dropped again. The
true K sits between the two columns and only the profiler can pin it.

| callback | register sites | permanent | churn | unregister | send sites |
|---|---|---|---|---|---|
| actor_on_first_update | 158 | 140 | 18 | 5 | 1 |
| on_option_change | 152 | 142 | 10 | 2 | 2 |
| save_state | 129 | 109 | 20 | 8 | 1 |
| **actor_on_update** | **125** | **73** | 52 | 47 | 1 |
| load_state | 119 | 108 | 11 | 2 | 1 |
| on_game_load | 74 | 53 | 21 | 0 | 1 |
| on_key_press | 65 | 51 | 14 | 8 | 1 |
| on_key_release | 53 | 38 | 15 | 9 | 1 |
| **npc_on_update** | **12** | **11** | 1 | 1 | 1 |
| **monster_on_update** | **4** | **4** | 0 | 0 | 1 |

The plan's guess of 5 / 20 / 60 was low for the actor and high for the NPC. Real
K ≈ **73-125**, **12**, **4**.

`actor_on_first_update` is the largest table in the game (158 sites) but fires once, so
it is a load-time cost, not a frame cost.

### Who calls it per frame, and how many times

Three `SendScriptCallback` sites, none throttled, all inside an unconditional binder
`update`:

| callback | call site | calls per frame |
|---|---|---|
| `actor_on_update` | `bind_stalker_ext.script:104`, in `actor_on_update(binder,delta)` | **1** |
| `npc_on_update` | `xr_motivator.script:486`, in `motivator_binder:update()`, above the `tg < self.__tmr` throttle | **1 per online stalker** |
| `monster_on_update` | `bind_monster.script:54` (loose), in `generic_object_binder:update()`, first statement after `object_binder.update` | **1 per online monster** |

How many stalkers and monsters are online at the `gammabaseline` save is not something
static analysis answers — it depends on the level, which smart terrains have spawned and
the alife switch distance. I-048's A/A run bounds it instead: `actor_on_update` is 94.5%
of all script time, so everything else together, listeners included, fits in 40 us/frame
and the online count is single digits. See §5 — it is what kills the compiled case.

---

## 3. Microbenchmark

`tools/microbench.py`, LuaJIT 2.0.1774896119 x64 via `lupa.luajit20`, opt flags
`fold cse dce fwd dse narrow loop abc sink fuse` (matches Anomaly's 2.0.4). Fresh
`LuaRuntime` per (case, arm, mode), `jit.off(chunk, true)` for the interpreted arm with
the harness self-check (interpreted/compiled measured 24.8-25.3x, threshold 3x),
`collectgarbage('collect')` before every timed run, 2 warm-up calls at N=1000, **best of
9**, N = 2e6 compiled / 3e5 interpreted total inner iterations with the outer count
scaled so total work is flat across K. `speedup = t(original)/t(rewrite)`.

Both arms live in one chunk. `@setup` (untimed) builds the intercepts table and both
dispatchers from source copied **verbatim** out of the live `_g_patches.script` and
`axr_main.script`: the patched `pairs`, `safe_order`, both `sift_down`s, `hspairs`, the
`spairs` selector, `sort_func_values_ascend`, `intercepts`/`next_index`/`callback_set`/
`make_callback`. K is the listener count.

* `bench/make_callback_dispatch.lua` — trivial listeners: the pure dispatch share.
* `bench/make_callback_dispatch_work.lua` — half the listeners bail on a cheap guard,
  half run ~16 float ops: what the change is worth end to end.

**`@corpus_k 4:1 12:1 73-125?:1` / `@corpus_src agent-I043-live-census-2026-09-19`.**
The `?` is load-bearing: 4 and 12 are counted register sites, 73-125 is a *range*
because 52 of the 125 actor_on_update registrations are churn and nothing static says
how many are live at once.

### Three runs, and only the third one counts

Run **A** (22:41Z) ran with agent-I044's `corpus` lock held. Run **B** (23:57Z) ran with
the fps-runner's `game` lock, which was taken for `20260919-193557-I-048-b1325e` while
the bench was already in flight. Run **C** (00:05Z) ran with **no lock held before,
during or after** — that is the one to quote, and the lab rule is why A and B are kept
here only as a consistency check.

B and C agree to within 4% on the absolute saving. **A is the outlier, and it is the one
I had published**: 43.1 us at K=73 interpreted against 26.5 (B) and 27.6 (C). The
contended run over-stated the headline saving by 56%, which is exactly the failure the
"no locks" rule exists to catch, and I quoted it for several hours before I could get a
clean window. Corrected: **the interpreted saving at K=73 is 27.6 us**, not 43.1.

### Speedups (A / B / **C**)

| K | listeners | JIT on | JIT off |
|---|---|---|---|
| 4 (monster_on_update) | trivial | 64.8 / 60.1 / **62.0x** | 10.8 / 8.2 / **9.0x** |
| 12 (npc_on_update) | trivial | 4.3 / 3.9 / **3.5x** | 16.2 / 13.1 / **13.5x** |
| 20 | trivial | 4.4 / 4.3 / **4.8x** | 13.1 / 15.8 / **15.5x** |
| 60 | trivial | 5.1 / 6.6 / **7.4x** | 25.8 / 20.1 / **20.5x** |
| **73 (actor, permanent)** | trivial | 4.9 / 4.7 / **6.0x** | 30.6 / 20.5 / **20.6x** |
| **125 (actor, all sites)** | trivial | 5.8 / 6.9 / **8.0x** | 27.2 / 25.9 / **27.2x** |
| 4 | working | 5.2 / 4.7 / **4.7x** | 2.5 / 2.6 / **2.6x** |
| 12 | working | 3.2 / 3.2 / **2.8x** | 3.7 / 3.7 / **3.6x** |
| 20 | working | 3.4 / 3.3 / **3.0x** | 4.3 / 4.2 / **4.3x** |
| 60 | working | 3.3 / 3.2 / **3.3x** | 5.7 / 5.7 / **5.9x** |
| 73 | working | 3.0 / 3.4 / **3.2x** | 6.2 / 5.8 / **5.6x** |
| 125 | working | 4.0 / 3.7 / **3.4x** | 7.2 / 6.7 / **6.6x** |

The 60-65x at K=4 compiled is an artifact — at four trivial listeners the candidate's
whole loop folds into one trace — and is not the monster_on_update win; the absolute
figure below is the honest one.

G2 (>= 1.15x both modes) passes at every K, in both pairs, in both runs.

### Absolute cost per dispatch — the number the site arithmetic needs

`best_s / n_outer`, microseconds per `SendScriptCallback`. Note the saving is nearly
identical in the trivial and working pairs (6.4 vs 5.8 us at K=73 compiled): dispatch
overhead is **additive**, independent of what the listeners do. That is what makes it
legitimate to price it per call.

| K | mode | A (us) | B (us) | **C, clean (us)** | in game |
|---|---|---|---|---|---|
| 4 | compiled | 0.58 | 0.52 | **0.55** | |
| 4 | interpreted | 0.88 | 0.60 | **0.71** | 2.72 (monster_on_update, cpf 0.05, noisy) |
| 12 | compiled | 1.17 | 0.74 | **0.74** | |
| 12 | interpreted | 3.58 | 2.77 | **3.03** | **2.80** (npc_on_update) |
| 73 | compiled | 6.44 | 5.46 | **7.48** | |
| 73 | interpreted | 43.13 | 26.48 | **27.63** | **96.75** (actor_on_update) |
| 125 | compiled | 11.95 | 14.06 | **17.17** | |
| 125 | interpreted | 60.55 | 57.71 | **62.65** | **96.75** (same, if K is 125) |

The "in game" column is from §6, put here to be read against the prediction rather than
to replace it. **At K=12 the clean bench is accurate to 8%** (3.03 predicted, 2.80
measured) — which is the strongest evidence that the bench models the right thing. The
`actor_on_update` gap is 1.5x if its true listener count is 125 and 3.5x if it is 73;
since K is only bounded, so is the gap.

---

## 4. Semantics, and the differential tests

`tests/test_make_callback_dispatch.py`, 15 tests, both dispatchers in **one** LuaJIT 2.0
runtime over the same `intercepts` table, so any divergence is the dispatcher alone.

The candidate is a **copy-on-write sorted array**: `order_list[name]` is rebuilt in
`callback_add` / `callback_set` / `callback_unset` and **replaced**, never edited in
place, so a dispatch already in flight keeps iterating its own snapshot — the same thing
`hspairs` gets from collecting its keys before the first listener runs.

| property | shipped (`hspairs`) | candidate | test |
|---|---|---|---|
| order | registration order (monotone `next_index`, ties impossible) | `table.sort` on the same values | `test_plain_dispatch_order_and_args`, `test_many_listeners_keep_registration_order` (K=125) |
| listener unregistered mid-pass | **skipped this pass** | skipped, via `t[func_or_userdata] ~= nil` | `test_unregister_during_dispatch_skips_the_victim`, `test_listener_unregistering_itself` |
| listener registered mid-pass | not called this pass | not called (old snapshot) | `test_register_during_dispatch_not_called_this_pass` |
| existing listener re-registered mid-pass | fires once | fires once | `test_reregister_during_dispatch_does_not_double_fire` |
| duplicate register | moves to the back, fires once | same | `test_duplicate_register_moves_to_end_and_fires_once` |
| unregister of unknown function | silent no-op | same | `test_unregister_unknown_function_is_a_noop` |
| unknown callback name | two `printf` errors, nothing dispatched | same | `test_unknown_callback_name_reports_the_same_error` |
| `callback_set(name, nil)` | printf, registers nothing | same | `test_setting_a_nil_listener_reports_and_registers_nothing` |
| table listener | `t[name](t, ...)`; a table without the method is skipped | same | `test_table_listeners_are_called_as_methods` |
| empty callback | nothing, no error | same | `test_empty_callback_dispatches_nothing_without_error` |
| listener that errors | **aborts the whole pass** (no `pcall` anywhere) | same | `test_dispatcher_survives_a_listener_that_errors` |
| churn sequence | — | identical trace | `test_full_churn_sequence_stays_in_lockstep` |

**The unregister-during-dispatch row is the one that caught the wrong `spairs`.**
`_g.script`'s `table.sort` version returns `keys[i], t[keys[i]]` and the loop body only
uses the key, so a listener removed mid-pass *still fires*. `hspairs` re-reads
`t[best_key]` on every step and skips the entry when it has gone nil, so it *does not*.
Written against `_g.script` my test asserted `a|b|c|--|a|b`; against the live `hspairs`
it is `a|b|--|a|b`. A candidate tuned to the first one would have introduced a real
behaviour change — a listener that unregisters a peer and expects it not to run in the
same frame is a normal thing for a mod to do. No return-value or early-exit convention
exists: `make_callback` ignores what listeners return, there is no way to stop a pass.

No `pcall`: **not** added. It would be an improvement and it is a behaviour change, and
this patch has to be exactly one thing.

---

## 5. Site arithmetic, and why the profiler is the right instrument

Frame budget 4770 us. **The in-game gate is 25 us saved per frame** (~0.5% of the frame;
lowered from 1% by the user, 2026-09-19). Using the K=73 (permanent-only) figures:

```
compiled:     1 x  6.44          =  6.4 us   actor_on_update
            + Nnpc x 1.17                    npc_on_update   (K=12)
            + Nmon x 0.58                    monster_on_update (K=4)
interpreted:  1 x 43.13          = 43.1 us   actor_on_update
            + Nnpc x 3.58
            + Nmon x 0.88
```

| scenario | saved per frame | % of frame | vs the 25 us gate |
|---|---|---|---|
| compiled, **actor_on_update alone** | 6.4 us | 0.13% | **fails** |
| compiled, + 5 stalkers / 2 monsters | 13.4 us | 0.28% | fails |
| compiled, + 15 stalkers / 5 monsters | 26.9 us | 0.56% | passes |
| **interpreted, actor_on_update alone** | **43.1 us** | **0.90%** | **passes** |
| interpreted, + 15 / 5 | 101.3 us | 2.12% | passes |
| interpreted, + 30 / 10, K=125 | 176.6 us | 3.70% | passes |

### I-048's A/A run removes the second unknown

Measured in-game, both arms identical, warm rounds only, run-to-run **cv 1.64%**:

| quantity | value |
|---|---|
| total script time | **749 us/frame** (15.7% of the frame) |
| `actor_on_update`, inclusive | **709 us/frame** — 94.5% of all script time |
| `actor_on_update` calls/frame | **1** |

Two things follow, and they are the reason this idea is now a clean binary rather than a
two-parameter guess.

**The per-online-NPC terms are negligible at this save.** Everything that is not
`actor_on_update` — `npc_on_update`, `monster_on_update` and every event callback
together — fits in 40 us/frame *inclusive of the listeners*. A single `npc_on_update`
dispatch costs 1.5 us compiled / 3.8 us interpreted before any listener runs, so the
online stalker count at `gammabaseline` is single digits, not the 15-30 my compiled rows
needed. **The compiled case therefore has no escape hatch: it is ~6.4 us against a 25 us
bar, and it fails.**

**The experiment is well posed.** At 709 us/frame with cv 1.64%, the resolvable
difference is about 11.6 us:

| case | predicted drop in `actor_on_update` | vs 11.6 us resolution |
|---|---|---|
| interpreted | 43.1 us = **6.1%** of the callback | ~3.7x the noise — clearly visible |
| compiled | 6.4 us = **0.9%** of the callback | below the noise — invisible |

So a null result is not an inconclusive result. If the profiler shows no change, the
dispatcher JITs and the saving is below the bar; if it shows a ~6% drop, it does not and
the saving clears the bar by 1.7x. Either outcome decides the idea.

(For context on the size of what is being optimised: 709 us/frame across ~73 listeners
is ~9.7 us per listener per frame. The dispatcher is 1-6% of that. The other 94% is
listener bodies — which is I-044's and I-042's territory, not mine.)

Structurally I expect it not to trace: per call the shipped path allocates two closures
and a table, calls a Lua comparator through two nested frames per heap comparison, and
then calls listeners that make engine calls. agent-I042's call-graph census labels the
body `compiled` with 0 abort sites, but they flagged that themselves as an artifact —
their jit_mode classification is intra-procedural, so the call into `spairs`/`hspairs`
is invisible to it. I am not going to settle it by reading code.

### The queued run

**`20260919-192703-I-043-3f2729`** (priority 2, 4 x 120 s, readout
`result.profiler.script_ms_per_frame_warm` with round 1 of each arm dropped). **It has
returned — §6 has the result, and it did not match the prediction above.**

My first submission, `20260919-184412-I-043-b1d614`, went in before I-048's runner change
was merged; that runner ignores `profiler_overlay`, so neither installed arm actually
contained `zzz_alao_profiler.script` and it can only return fps — which at 6-43 us of a
4770 us frame is unresolvable against the +-2% fps noise floor. The organizer caught it,
merged I-048 (b875c9c) and requeued the identical arms.

The two arms differ in exactly one file:

* variant top `lab/coord/overlays/agent-I043-b` — byte copy of `ref3-alao-b` (324 files)
  plus the patched `gamedata/scripts/axr_main.script`
* baseline top `lab/coord/overlays/ref3-alao-b`
* both bottoms `lab/coord/overlays/ref3-vanilla-bottom`
* both arms carry `lab/coord/overlays/alao-profiler`

Neither reference overlay ships `axr_main.script`, so nothing in the reference is
overwritten — the build script asserts that rather than trusting it. The reason is
structural, and worth stating: `ref3-alao-b` is built from the **gamma** corpus (enabled
mods only, and the only mod that ships `axr_main.script` is disabled), and
`ref3-vanilla-bottom` from **vanilla_db** (where ALAO left `axr_main.script` untouched).
The loose `Anomaly/gamedata/scripts` tree — `extracted/vanilla` / `VANILLA_SCRIPTS`, the
one that actually wins — is in **neither** gen-3 corpus. So the baseline arm runs the
stock loose `axr_main.script` and the delta is purely my patch. It also means the live
winners of all 62 loose scripts are outside the gen-3 reference entirely, which the
organizer may want to know for its own sake. The profiler wraps
`axr_main.make_callback` at runtime, so it composes with the patch instead of colliding
with it.

The patch is produced by `lab/coord/i043_axr_main_patch.py`: five asserted-unique string
substitutions on the read-only live file, CRLF and cp1251 preserved, +27 lines.
LuaJIT 2.0 compile-checks clean.

---

---

## 6. The in-game result

**Queue item `20260919-192703-I-043-3f2729`** — 4 rounds x 2 arms x 120 s, arms differing in
exactly one file, I-048's profiler installed in both, warm windows only (round 1 of each
run dropped).

| | baseline | variant | delta |
|---|---|---|---|
| **script time** | **712.3 us/frame** | **607.3 us/frame** | **-110.4 us/frame, -15.45%** |
| `actor_on_update` | 667.9 us | 571.2 us | -96.7 us (-14.5%) |
| per-run means | 700 / 713 / 716 / 720 | 588 / 593 / 607 / 622 | **no overlap** |
| fps avg | 213.31 | 210.38 | -2.93 (-1.37%) |
| fps 1% low | 163.71 | 169.30 | +5.59 (+3.41%) |
| frametime p99 | 5.71 ms | 5.59 ms | -0.12 ms (-2.1%) |

**Against the 25 us/frame gate: 110.4 us saved. Passes by 4.4x.**

### My pre-registered rule fired, and I was wrong, not the data

Before the run I put on the board: *"a change in actor_on_update larger than ~10% would
mean something other than the dispatcher moved and I would treat the arms as
contaminated."* It came in at 14.5%, and every other callback moved too — including
`imgui_on_render` (-58%) and `smart_terrain_on_update` (-49%), which have nothing to do
with per-frame binder code. So I checked the three ways this could be an artifact before
accepting a number that flatters my patch.

**1. Timer scaling.** All 8 runs calibrated to the same units: `units_per_ms` 996.77 to
999.50, a 0.27% spread, `overhead_ns=200.0` in every `hdr` line. Not a units artifact.

**2. Denominator drift.** `ms_per_frame` is per 30 s window; the variant contributed 10
usable windows to the baseline's 7, which is where the 62763-vs-45322 "frames" gap comes
from — it is a count of windows used, not a difference in frame rate. The check that
matters is `calls_per_frame`, which is computed off the same denominator: it agrees
between arms to within **3.5%** on every callback above 0.1 calls/frame. Not a
denominator artifact.

**3. Contamination.** This is where I was wrong, and the way I was wrong is the
interesting part. `make_callback` is the single funnel every `SendScriptCallback` goes
through, so patching it makes **every** callback cheaper, not only the three per-frame
ones I priced. There are **7.49 dispatches per frame**, not 3. And the per-dispatch
saving tracks the bench across fifteen independent callbacks at listener counts from 1
to 125:

| callback | K (perm/all) | calls/frame | saved/dispatch | bench predicts |
|---|---|---|---|---|
| actor_on_update | 73 / 125 | 1.000 | **96.75 us** | 27.6 us (K=73) / 62.7 (K=125) |
| npc_on_update | 11 / 12 | 0.502 | 2.80 us | **3.03 us — within 8%** |
| squad_on_update | 1 / 5 | 0.434 | 3.57 us | ~0.9 us |
| npc_on_hear_callback | 1 / 3 | 0.842 | 2.74 us | ~0.9 us |
| actor_on_update_pickup | 0 / 3 | 1.000 | 2.27 us | ~0.9 us |
| npc_on_choose_weapon | 2 / 3 | 1.515 | 1.95 us | ~0.9 us |
| imgui_on_render | 1 / 1 | 1.000 | 0.93 us | ~0.9 us |
| monster_on_update | 4 / 4 | 0.052 | 2.72 us | ~0.9 us |

Fifteen callbacks, all in the 0.3-4 us band the bench predicts for small K, none of them
things my patch could reach except through the funnel. That is the mechanism confirming
itself, not contamination. The rule was right to fire; what it caught was that **my site
arithmetic modelled 3 of the 7.49 dispatches per frame**.

### What the bench got wrong

`actor_on_update` is the one real outlier: **96.75 us saved per dispatch against a clean
bench prediction of 27.6 us at K=73 or 62.7 us at K=125** — a factor of 1.5x to 3.5x,
and the range is that wide only because K itself is bounded rather than known.

Everything else lines up. `npc_on_update`, the one other callback whose listener count I
know well (11 permanent of 12 static), comes in at 2.80 us against a predicted 3.03 —
**within 8%**. So the bench is not systematically wrong; it models the dispatcher
correctly at the listener counts where I can check it.

The obvious structural candidate for the large-K gap is allocation: `hspairs` builds a
K-element `keys` array plus a `safe_order` closure plus an iterator closure **on every
dispatch**, so at K=73-125 that is a kilobyte-scale table created and discarded every
frame. The microbench protocol runs `collectgarbage('collect')` immediately before every
timed run and reports **best of 9** — exactly right for comparing steady-state work, and
it systematically excludes the GC cost that churn creates in a live frame, while the
profiler's inclusive wall time inside `make_callback` includes any GC step that fires
during a dispatch.

**I did not test that hypothesis and the evidence for it is weaker than I first wrote.**
My earlier draft claimed the under-prediction "scales with K"; with the clean run C the
ratios are 0.92x at K=12, then 1.5x or 3.5x at the top depending on the true K, which is
not a clean monotone trend — it is one outlier plus one good match. The honest statement
is: *the bench is accurate where I can check it, it under-predicts the one callback
whose K I do not know, and allocation is the leading suspect.* Testing it properly means
a bench variant reporting the mean of all 9 runs rather than the best, or tracking
`collectgarbage('count')` deltas per arm. That is the first thing I would do next.

If it holds up, the lesson for the lab is that `collectgarbage()` before every timed run
makes `tools/microbench.py` a **lower bound** for allocation-heavy rewrites — the rule
exists because its absence once produced a 23% over-claim on `table.insert`, and it
would be worth knowing that it can err the other way. I am not proposing to change the
protocol; a bench that included GC would be far noisier.

### The honest part: fps did not move

Script time fell 15.45%, and **fps did not follow**: -1.37% average, +3.41% on the 1%
low, -2.1% on p99. Every one of those is inside the locked +-2% noise band, and the
average and the low disagree in sign. 110 us off a ~4750 us frame is 2.3%, right at the
edge of what this harness can resolve, and the locked per-round spread on identical arms
is 203-222 fps.

**So: a large, reproducible, mechanistically-explained saving in script time, and no
demonstrated frame-rate win.** The gate is stated in script-ms and it passes by 4.4x. If
the gate is meant to predict fps, this run is evidence that at ~15% of a frame, script
time is not on this scene's critical path. I would not quote a single fps number from
this run in either direction.

Also settled, as a by-product: **the dispatcher does not JIT in the real engine.** The
measured per-dispatch saving is above even the interpreted bench arm, and nowhere near
the 5-6 us the compiled arm predicts. That was the open binary in §5 and it is closed.

## 7. Could this ever be an ALAO transform?

**No, and the count is the argument.**

There are **99 `spairs(` call sites across 44 of the 1350 live winner scripts** (all 99
in `.script` files; the 33 live `.lua` files have none). Of
those, **0** sit in a body today's name-based per-frame classifier recognises, and
**exactly 1 — this one — is per-frame in reality**, reached through
`SendScriptCallback -> axr_main.make_callback`, an edge only a call-graph classifier
(I-042) can see. The rest are MCM option menus, `print_r`, inventory/UI builders,
`ui_debug_weather`, trade managers: cold code where a sorted iteration costs nothing
anybody can feel. That asymmetry is itself a useful result for I-042 — the one `spairs`
that matters is invisible to name-based classification, and the 99 that are visible do
not matter.

agent-I042 confirmed the site against their call-graph census independently: it appears
only at **hop 2** and only over a **bare-global** edge
(`binder:update -> _g.SendScriptCallback -> axr_main.make_callback`), so a strict
one-hop rule over same-file and `module.func` edges misses it twice over. They also
report that their `jit_mode` classifier calls this body `compiled` with 0 abort sites,
and that this is wrong — the classification is intra-procedural, so a call into a user
Lua function (`spairs` -> `hspairs`) is invisible to it. Their number should not be used
to pick between my compiled and interpreted columns; that stays open until the profiler
answers it.

A transform would also have to prove things ALAO structurally cannot:

* that `intercepts` is mutated **only** in the three named functions of the same file
  (whole-program aliasing; `whole_program_analyzer.py` is not even imported yet);
* that the sort key is monotone per key, so `table.sort`'s order is total and
  deterministic;
* that snapshot semantics are preserved under mutation during iteration, including the
  nil-skip that `hspairs` does and `_g.script`'s `spairs` does not — i.e. the
  transform's correctness depends on **which of two globally-overridable `spairs`
  implementations wins at load time**, in a different file, decided by mod load order.

Any ALAO rule narrow enough to be safe here would be a special case keyed on
`axr_main.script`, which the repo's own conventions warn against, over a single site.

**The right home is upstream.** The lines being changed are already the Kutez callback
priority patch (xray-monolith PR #339) — the dispatcher has an owner and a recent change
history. A PR to Anomaly's `axr_main.script`, or a GAMMA `_g_patches`-style override,
reaches every player without ALAO in the loop. The overlay here is the evidence for that
PR, not a product.

Recommendation for the beam: **keep I-043's finding, prune it as an ALAO transform,
re-file the patch as an upstream/hand-patch item.**

---

## 8. What I did not do

* No corpus run, no G4-G9: this branch adds a bench pair, a test module and two lab
  tools. It does not touch `ast_analyzer.py`, `ast_transformer.py` or `reporter.py`, so
  ALAO's output is byte-identical and those gates have nothing to compare.
* Run C is clean and is the one quoted; A and B were taken under locks and A was wrong
  by 56% at K=73. I published A's number before I could get a clean window. Nothing from
  any of the three should go into `microbench-baseline.json` without an independent
  re-run — one clean run is one clean run.
* The listener count K is still **static** — 73 and 125 are bounds, never observed
  running, and `@corpus_k` marks the bucket estimated. The profiler reports calls/frame,
  not listeners/dispatch, so the run does not pin K either.
* **Why the in-game saving is 1.5-3.5x the bench is argued, not proven**, and with the
  clean run the evidence is weaker than my first draft claimed — it is one outlier
  (`actor_on_update`) plus one good match (`npc_on_update`, within 8%), not a trend.
  The GC explanation is a hypothesis; the test is a mean-of-9 bench variant or
  `collectgarbage('count')` deltas per arm. Biggest loose end here.
* No corpus / G4-G9 numbers: nothing here changes ALAO's output.
* My first queue item `20260919-184412-I-043-b1d614` ran on the pre-I-048 runner with no
  profiler installed, so it measures fps only and I read nothing into it.
* One run, one save, standing still. `actor_on_update` being 94% of script time is a
  property of `gammabaseline`, not of the game; in a firefight with 30 NPCs online the
  npc_on_update term grows and the actor term does not.

## 9. Unrelated bug found on the way (organizer)

**`ref3-vanilla-bottom` ships the wrong `bind_monster.script`.** `build_overlay.py
--bottom` decides a db script is live by scanning only the enabled MOD directories for
the same path. It never looks at `Anomaly/gamedata/scripts`, so a db file that GAMMA has
shadowed with a loose patched copy still gets taken. `bind_monster.script` is the one
hit: the overlay installs the ALAO-rewritten **db** version (477 lines) over the live
loose **GAMMA-patched** version (528 lines). That is a content downgrade of a per-frame
monster binder, not a rewrite, in every arm that uses that overlay — including both of
mine, where it at least cancels out. 1 of 139 files. Fix: add the loose script directory
to the `shipped` set in `build_bottom()`.
