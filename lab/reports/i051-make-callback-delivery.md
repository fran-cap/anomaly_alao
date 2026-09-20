# I-051 — shipping the `make_callback` dispatch patch

agent-I051, 2026-09-20, branch `agent/gen4-I051`. Delivers I-043
(`lab/reports/make-callback-dispatch.md`).

**Verdict: SHIP AS A MONKEY-PATCH MOD, with one documented behaviour difference.**

Three results, in order of how much they change what I-043 said:

1. **The semantic audit found a real divergence I-043 missed, and it is broader than any
   of the cases I was asked to check.** `hspairs` drains a heap whose comparator reads the
   table's live values, so **any** register or unregister for the callback that is
   currently dispatching reorders the listeners that have not run yet — and because that
   moves *when* each listener is visited, which listeners run in that pass can change too.
   I-043 tested four specific churn cases, they all happened to agree, and it concluded
   the two were equivalent. They are not. The guarantee that does hold: **no mutation
   during a pass ⇒ bit-identical, 600/600.** And in the window where they differ, **the
   shipped order is not deterministic either** — 14 distinct orders over 30 replays of one
   scenario, because `hspairs` seeds its heap from the hash order of a table keyed by
   function pointers. The change replaces an allocation-dependent order with the declared
   priority order.
2. **Delivery is a monkey patch, not a replacement file**, and the census says that is
   free: the 1350 scripts a live GAMMA profile loads contain exactly 5 references to the
   four functions being swapped, **all 5 through the module table at call time, 0 bound to
   a local**.
3. **Upstream is located and verified.** `themrdemonized/xray-monolith`, default branch
   `all-in-one-vs2022-wpo`, `gamedata/scripts/axr_main.script`. The live GAMMA copy and
   the upstream copy differ in **one comment line**, and the region the patch touches is
   **byte-identical** (1758 bytes, sha256 `0ded82c3…`).

**Nothing has been published.** The mod folder and the PR draft exist; opening, pushing or
posting anything is the user's call.

---

## 1. The semantic audit

Run against the **real** `Anomaly/gamedata/scripts/axr_main.script` (sliced from
`local intercepts = {` through the end of `make_callback`, anchored on source text so a
GAMMA update fails the tests loudly) with the loose `_g_patches.script` `hspairs` prelude,
two module tables in one LuaJIT 2.0 runtime, the shipped mod installed on one of them.
`tests/test_i051_dispatch_delivery.py`, 58 tests.

### What was asked, and what came back

| case | shipped | patched | same? |
|---|---|---|---|
| listener unregisters itself | skipped this pass and after | same | yes |
| listener unregisters a peer that has not run | peer skipped this pass | same | yes |
| listener unregisters a peer that already ran | peer already fired | same | yes |
| listener registers a new listener mid-pass | not called this pass | same | yes |
| same function registered twice | moves to the back, fires once | same | yes |
| equal priorities | **impossible**: `next_index` is a per-name monotone counter, so the order is registration order and ties cannot occur | same | n/a |
| a listener that errors | aborts the whole pass, no `pcall` anywhere | same | yes |
| re-entrant `make_callback` of the same name | inner pass sees the current set, outer resumes | same | yes |
| zero listeners | nothing, no error | same | yes |
| unknown callback name | two error lines | same (delegated to the stock function) | yes |
| **any churn during the pass, order of the rest** | **heap artifact** | **priority order** | **NO** |

### The divergence, precisely

`hspairs` builds a min-heap from a snapshot of the keys, then pops. Its comparator
(`safe_order`) reads `t[a]` and `t[b]` — the table's **current** values. Unregistering
nils a value; re-registering gives it a new, larger one. Either way the heap invariant is
now false with respect to the values the comparator is reading, and the pop order of
everything still in the heap becomes an artifact of the heap's internal layout. The cached
array keeps the declared priority order.

Minimal reproduction, 8 listeners, #2 re-registers #7 which has not run yet:

```
shipped:  1|2|3|4|5|6|8|7        <- 7 pushed behind 8 for the rest of this pass
patched:  1|2|3|4|5|6|7|8
next pass (both):  1|2|3|4|5|6|8|7
```

And because order changes visit time, and a listener unregistered mid-pass is skipped only
if that happened before its visit, the *set* can differ too. 600 randomised scenarios per
mode, up to 30 listeners, 3 passes each, built to churn mid-pass as hard as possible —
an upper bound on pathology, not a rate in a real frame:

| churn during the pass | identical | reordered | different set |
|---|---|---|---|
| **none** (mutation only between passes) | **600** | 0 | 0 |
| unregister | 480 / 467 | 96 / 94 | 24 / 39 |
| register | 140 | 460 | 0 |
| both | 251 | 294 | 55 |

The printed error lines are identical in **every** scenario of all four modes.

### And the shipped order in that window is not deterministic

Two numbers in the unregister row above because **two runs of the same census disagree**,
and chasing that down is the result that decides how seriously to take any of this.

`hspairs` seeds its heap from `pairs(t)` over a table keyed **by the listener function**,
so the initial array order is hash order — pointer order. While nothing mutates during the
pass that is invisible: the heap sorts it out and the output is the priority order
whatever the layout was. Once a listener churns mid-pass, the invariant breaks and the pop
order of everything left depends on that initial layout, i.e. on where the closures happen
to have been allocated.

Replaying **one** scenario thirty times in one runtime:

```
SHIPPED : 14 distinct orders over 30 replays
PATCHED :  1
```

and in every one of those 14, the passes *after* the churning one are identical — the
divergence never outlives the pass it happened in. The patch's order is one of the
fourteen.

So the change does not replace a defined behaviour with a different one. It replaces an
**unspecified, allocation-dependent** order with the priority order the Kutez system
declares. A mod that depended on the shipped order in that window would already be
depending on where LuaJIT put its closures. Pinned as
`test_the_shipped_order_is_nondeterministic_once_a_listener_churns`; if it ever stops
holding, this whole section needs revisiting.

### Does any real script do this? Yes — one, and it is bounded

`lab/tools/i051_delivery_census.py` resolves every
`RegisterScriptCallback("X", <name>)` in the 1350 live winners to a top-level
`function <name>` in the same file and searches that body for a register or unregister of
`"X"`. Over the whole live tree that finds **two sites, both the same listener**:

```
drx_da_main.script:1679  actor_on_update <- drx_da_actor_on_update_callback (register)
drx_da_main.script:1697  actor_on_update <- drx_da_actor_on_update_callback (register)
```

(`234- Dynamic Anomalies Overhaul - Demonized`, the same file I-049 is rewriting.) The
listener runs inside the `actor_on_update` pass, calls `unregister_drx_da()` — which
unregisters **itself**, and it has already run, so both dispatchers agree — and then
registers a *different* function for `actor_on_update`, which is in neither dispatcher's
snapshot and so is not called this pass either. Both dispatchers therefore call **the same
set of listeners exactly once**. What differs is the order of the ~72 `actor_on_update`
listeners that have not run yet, for that one frame, on a path that fires after a surge or
a level change rather than every frame.

The scan's blind spots, stated as counts rather than glossed: **586** registrations whose
listener name does not resolve to a top-level function in the same file, and **64**
registrations of an inline closure. It also cannot see a listener that churns its own
callback through a function it calls. So "two" is a floor on same-file, directly resolvable
self-churn, not a proof that nothing else does it.

### Why I did not fix it

I tried. A generation counter plus "re-sort the remaining tail by current value when
something churns" moves the full-churn corpus from 10 to 7 failures out of 40 and then
stops: it cannot reproduce a partially-invalid heap's pop order, because that order is not
sorted by anything. Exact parity means running `hspairs`, which is the work the patch
exists to remove. I reverted it and documented the difference in the script header, the
mod README, the PR draft, and a pinned test (`test_the_documented_divergence`) so it
cannot drift silently.

And once the nondeterminism above was measured, "fixing" it stopped being the right goal:
there is no single shipped order to match.

### A smaller thing, corrected

I posted on the board that I-043's test module shared one `intercepts` table across
scenarios. **That was wrong** — its `_run()` builds a fresh `LuaRuntime` per arm. The
accumulated-state bug was in *my* module, in its first form, and for one round it made the
fuzz output meaningless. Fixed by resetting `intercepts`/`next_index` through
`debug.getupvalue` between plays (captured before the patch is installed). The divergence
finding is independent of it and reproduces on fresh runtimes.

## 2. Delivery form: (B) monkey patch

`lab/tools/i051_delivery_census.py`, over the 1350 live winners:

```
references to axr_main.{make_callback,callback_add,callback_set,callback_unset}:
  {'called': 5, 'bound': 0, 'other': 0}
  _g.script:105 axr_main.callback_set(...)      <- RegisterScriptCallback
  _g.script:109 axr_main.callback_unset(...)    <- UnregisterScriptCallback
  _g.script:119 axr_main.make_callback(...)     <- SendScriptCallback
  _g.script:128 axr_main.callback_add(...)      <- AddScriptCallback
  aol_mp412_monkeypatches.script:98 axr_main.callback_add("actor_on_item_upgrade")

axr_main.script copies:  loose yes, db yes, ENABLED mods 0, extracted/gamma 0
```

Every caller goes through the module table **at call time**, so swapping four fields
catches all of them. That is what makes form B possible at all, and it is why the
framework README's claim ("nothing caches `axr_main.make_callback` into a local") is now
checked rather than assumed.

| | (A) replacement `axr_main.script` | (B) monkey patch |
|---|---|---|
| conflicts | with any mod shipping `axr_main.script` (0 enabled here, but a user's list is not this machine's) and with GAMMA's own loose copy, which it would have to override | **none possible** — replaces no file |
| redistributes Anomaly code | **yes**, the whole 18 KB file | no, ~170 lines of new code |
| survives a GAMMA/exes update | no: silently reverts the update's changes to that file | yes, unless the update renames `intercepts` |
| per-dispatch cost vs the other | identical (see §3) | identical |
| install cost | none | one `debug.getupvalue` scan, once |
| failure mode | a stale copy of the whole file | declines and logs, leaving the game untouched |

So: **(B) for players, (A) for upstream** (which owns the file, where a monkey patch would
be the wrong shape). A generator (`lab/tools/i051_upstream_patch.py`) builds (A) from any
copy of the file, so nobody has to redistribute one to reproduce it.

Composition with the I-048 profiler is the one ordering constraint: the profiler *wraps*
`make_callback`, this mod *replaces* it, so the mod has to install first.
`axr_main.on_game_start()` walks the scripts root in file-listing order, and
`zzz_alao_callback_dispatch.script` sorts before `zzz_alao_profiler.script`. Validated
offline against the real profiler source — the test asserts the profiler's
`orig_make_callback` upvalue **is** the patched dispatcher
(`test_the_profiler_still_times_the_patched_dispatcher`).

The profiler's per-listener mode (`WRAP_LISTENERS`) rewrites `intercepts` keys through
`debug.getupvalue`, which no wrapper can see. With the mod installed, those listeners go
**silently quiet** until something re-registers — nothing crashes, they are simply skipped
by the nil guard. `invalidate()` is the escape hatch and is public on the mod's module
table. **Anyone pairing this mod with `alao-profiler-listeners` must call it.**

## 3. Bench

**One run exists and it is not quotable.** It started in a clean window (game free, corpus
free) and finished with the `corpus` lock held by agent-I052, so it fails the lab's own
rule and I am recording it as evidence of nothing:

| K | JIT on | JIT off |
|---|---|---|
| 4 | 0.89x | 0.94x |
| 12 | 0.91x | 0.88x |
| 73 | 1.00x | 0.97x |
| 125 | 0.97x | 0.97x |

(`speedup = t(form A) / t(form B)`, so below 1.00 means the monkey patch measured
*slower*.) The same run's `make_callback_dispatch` rows came in at 26.11x interpreted at
K=73 against I-043's clean 20.6x — the contamination is visible in the pair whose clean
value we know, which is exactly why this one does not count. A `--quick` smoke test taken
earlier with both locks free read 0.93-1.02x. **A clean re-run of the delivery pair is
queued behind the corpus lock; until it lands, treat A vs B as unmeasured.**

The structural argument, which both readings are consistent with, is that forms A and B
are the same code: both are reached as
`axr_main.make_callback(...)` (same module-table index), in both the dispatcher's
`intercepts` and `order_list` are upvalues, and `debug.getupvalue` runs once at install,
not per dispatch. Form B's only extra work is a wrapper on `callback_set`/`callback_unset`
— registration cost, ~1800 times at load and rarely after, not dispatch cost.

`bench/make_callback_dispatch_delivery.lua` is the pair that settles it. **Its pass
condition is 1.00x, not the usual 1.15x**, so a G2 "fail" is the wanted result.

The dispatch saving itself is I-043's, from a clean run: 0.55 us/dispatch at K=4 up to
27.6 us at K=73 interpreted, and 105.1 us/frame measured in game (warm rounds; 110.1 over all four).

## 4. The stock-baseline run

Queue **`20260919-211347-I-051-21408e`**, priority 4, submitted, not yet drained.

* baseline: **stock GAMMA** + `alao-profiler` (no rewrite overlay, no bottom overlay)
* variant: stock GAMMA + `lab/coord/overlays/agent-I051-b` (the mod) + `alao-profiler`
* 4 × 120 s, warm-up 30 s, save `gammabaseline`, readout
  `result.profiler.script_ms_per_frame_warm` with round 1 of each arm dropped

**No runner change was needed.** `fps_runner.process()` treats a null `baseline_overlay` as
"stock" simply by skipping that slot, and `profiler_overlay` is already installed into both
arms. A variant overlay that is just a mod folder works directly — `install_overlay` only
requires a `gamedata/` directory, so `build_overlay.py` is not in the path at all. Since
the mod replaces no file, both arms load the identical loose `axr_main.script` and the only
difference between them is the monkey patch.

I-043 measured −105.1 us/frame **on top of full ALAO**. This run asks whether a plain GAMMA
player sees the same thing. It should — nothing ALAO rewrites is in the dispatch path — but
"should" is why it is being run.

## 5. Files

| path | what |
|---|---|
| `lab/mods/alao-make-callback-dispatch/` | the mod: `meta.ini`, `gamedata/scripts/zzz_alao_callback_dispatch.script`, `README.md` (what it does, the numbers with protocol, the behaviour difference, install/uninstall, compatibility) |
| `lab/tools/i051_delivery_census.py` | the two census questions: who references the four functions, and who ships `axr_main.script` |
| `lab/tools/i051_upstream_patch.py` | builds form (A) and the upstream diff from any copy of the file, or fetches upstream; five asserted-unique substitutions |
| `lab/docs/i051-upstream-pr-draft.md` | the PR draft: target, verification table, diff, body, and what is *not* verified |
| `tests/test_i051_dispatch_delivery.py` | 58 differential tests against the real file |
| `bench/make_callback_dispatch_delivery.lua` | form A vs form B, pass condition 1.00x |
| `lab/coord/overlays/agent-I051-b` | the mod, as the variant overlay |

## 6. What I did not do

* **No quotable bench** (§3). The one full run began clean and ended with agent-I052's
  `corpus` lock held; its `make_callback_dispatch` rows read 27% above I-043's clean value
  at K=73, so the contamination is not hypothetical. Re-run queued.
* **No corpus run / G4-G9.** This branch touches no analyzer, transformer or reporter
  code, so ALAO's output is byte-identical and those gates have nothing to compare.
* **The divergence is characterised, not eliminated**, and the 600-scenario rates are from
  a synthetic corpus designed to churn mid-pass. The real-world census (§1) found one
  listener that does it, with a bounded effect, but its resolution is same-file and
  name-based: 586 registrations have an unresolvable listener name and 64 register an
  inline closure, and nothing follows a listener into a function it calls. Closing that
  properly needs I-042's call-graph, not a regex.
* **Upstream conventions are unverified.** There is no `CONTRIBUTING.md`; whether the
  maintainers want this as an edit to `axr_main.script` or as a restored
  `axr_main_patches.script` in PR #339's style is a question for them, not a guess for me.
* One save, one spot, standing still, for every in-game number quoted here and in I-043.
