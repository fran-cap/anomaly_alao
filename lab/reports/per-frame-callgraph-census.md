# I-042 — call-graph-aware per-frame census

Report-only investigation plus two analyzer fixes. Question behind it: I-013/I-010 decide
"this body runs per frame" from its **name** (an engine `*_on_update` callback, or an
`:update` / `:Update` method), and I-041 showed that is the wrong set — 74 of 103 live
vanilla per-frame bodies have zero actionable ALAO findings, while the code that actually
burns the frame sits one or two calls further out. So: if per-frame status is propagated
along call edges, how many interpreted bodies enter the set, and do the I-040
(`repeated_time_global`), I-021 (`repeated_db_actor`, engine getters) and `debug_statement`
counts inside it rise enough to matter?

Tool: `lab/tools/per_frame_callgraph_census.py` (new). Analyzer at `8a80d0f`.

## 0. The live set, and why it has three sources

Everything below is restricted to the copy of each script the game actually loads. Three
sources, in priority order — highest-priority enabled mod, then GAMMA's in-place patches in
`Anomaly/gamedata/scripts`, then the `scripts.db0` vanilla file:

| source | live files |
|---|---:|
| enabled GAMMA mods (`extracted/gamma`, modlist order) | 974 |
| loose GAMMA patches (`Anomaly/gamedata/scripts`, read in place, read-only) | 61 |
| vanilla db (`extracted/vanilla_db`) | 315 |
| **total live** | **1350** |
| copies shadowed by something higher | 632 |

The loose patches are live code that exists in **neither** corpus; a census that reads only
the two extracted corpora silently uses the wrong `axr_main.script`. 19745 function bodies,
107250 call sites inside them, 0 analysis failures.

Spot-checked against the three files this has actually bitten someone on: the census resolves
`bind_monster.script` and `axr_main.script` to the loose copies, and `_g.script` to the db one
(no mod and no loose file ships it). Note that `build_overlay.py --bottom` has the
corresponding bug — agent-I043 found it scanning only enabled mod dirs when computing what is
already shipped, so `ref3-vanilla-bottom` installs the ALAO-rewritten **db** `bind_monster`
over the live loose one. This census does not share it; the fix there is to add the loose
script dir to `shipped` in `build_bottom()`.

## 1. Why this is a lab tool and not an analyzer pass

The per-file analysis runs in a `multiprocessing` pool of picklable workers under a per-file
timeout, and the whole G6 budget is ~12 s analyze / ~22 s fix on 1503 files. A cross-file
propagation pass inside that pipeline would have to either serialise the pool or add a second
full pass. It also cannot be made correct per file: which copy of `xr_logic.script` is live
depends on the MO2 modlist, which is not the corpus.

`whole_program_analyzer.py` was evaluated as a base and rejected. It tracks symbol
definitions and usages for dead-code detection — it has no notion of function bodies, call
sites inside a body, per-frame seeds, or JIT mode, and it is unimported and untested, so
building on it would mean writing the call graph anyway plus adopting an unverified module.
The census instead reads what `ASTAnalyzer` already records (`scopes`, `calls`, `jit_modes`,
findings), emits an edge list per file, and propagates in a post-pass — exactly the shape the
brief suggested, and zero risk to G6. Nothing in the analyzer changed to support it.

Winner resolution reuses `lab/coord/build_overlay.py`'s `read_modlist`.

## 2. The two `ENGINE_NYI_METHODS` gaps (deliverable 1)

`section_name` and `profile_name` are LuaBind exports on the server object, like `:id()` and
`:name()` — 222 sites in 82 of the 1350 live files — and both were missing, so a body whose only engine
call was one of them classified as `compiled`. Added, with two tests.

**12 of the 19745 live function bodies change classification**: 8 `compiled -> interpreted`,
4 `mixed -> interpreted` (7 gamma, 4 vanilla, 1 loose). **Zero of them are per-frame bodies**,
which is why the corpus gate shows no finding movement at all. The fix matters for
correctness and for anything that later gates on the mode, not for today's counts.

One test had to change: `test_unknown_method_makes_the_body_undecidable` used
`:section_name()` as its example of an unknown method. Swapped for a genuinely unknown name,
and the `section_name` case added back as its own test asserting `interpreted` (still RED, so
`--fix` output does not move).

## 3. The registration edge (deliverable 3), and a classifier bug it exposed

1734 `RegisterScriptCallback` sites in live files; **145 register for a per-frame event**
(`actor_on_update` 121, `npc_on_update` 13, `squad_on_update` 5, `monster_on_update` 4,
`smart_terrain_on_update` 2).

Before this work the name rule caught 69 of 133 resolvable handlers and missed 64. Splitting
the misses by cause found a plain bug rather than a gap:

- **36 were `local function actor_on_update(...)`.** `_visit_LocalFunction` never built a
  `PerFrameCallbackInfo`, so the commonest shape in mod scripts was invisible to the entire
  I-010 / I-013 classifier — not one-hop-invisible, *zero*-hop-invisible. Fixed (`8a80d0f`),
  two tests. The name-based seed set goes 274 -> 311 on the live tree; `--fix` output does
  not move.
- **29 remain**, and these are the genuinely unguessable half: `process_queue`
  (`demonized_time_events`), `batt_checker` (`battery_warning`), `check_nv_state`
  (`item_nvg`), `checkLedgeGrabbing` / `checkJump` (`demonized_ledge_grabbing`),
  `ssfx_change_blur`, `update_rain`, `tick`, `move_camera`, `change_fov`. 17 of the 29 are
  interpreted or mixed. Only registration analysis finds these; no naming rule will.

So after the fix the name rule covers 105 of 134 resolvable per-frame registrations, and the
registration edge is worth 29 more live bodies (plus 11 whose handler expression the census
cannot resolve inside the registering file).

## 4. The propagation (deliverable 2)

Seeds = 311 name-based bodies + 29 registration-only = 340. Edge kinds:

- `same-file` — a bare call resolving to a top-level function in the same script;
- `module.func` — `xr_logic.pick_section_from_condlist` -> `pick_section_from_condlist` in
  the live `xr_logic.script`;
- `global` — a bare call resolving into `_g.script` (or the loose `_g_patches.script`). X-Ray
  loads every other `.script` as its own module table, so a top-level `function foo()` there
  is `<module>.foo` and **not** a global: `options_builder.script` really does define
  `function vector(args)` and it does not clobber the engine constructor. Only `_g` is
  special, and that is how `SendScriptCallback` and `printf` become bare globals.

Resolution coverage of the 107250 in-body call sites: method `obj:m()` 40%, global 14%,
same-file 11%, `module.func` 9%, unresolved bare 14%, unresolved module 9%, unresolved
function 3%. **The 40% of calls that are `obj:method()` are unresolvable without type
inference** — that is the single biggest limit on everything below, and it biases every
count downward.

### Bodies in the per-frame set, by hop (strict = same-file + `module.func`)

| | bodies | interpreted | mixed | compiled |
|---|---:|---:|---:|---:|
| hop 0 (today's classifier, after the §3 fix) | 340 | 219 | 33 | 88 |
| + 1 hop | **795** | **424** | 110 | 261 |
| + 2 hops | 1100 | 563 | 138 | 399 |

Adding the `global` edge kind: 862 bodies at hop 1, 1197 at hop 2.

One hop **more than doubles** the set (340 -> 795) and takes interpreted bodies from 219 to
424. Origin split at hop<=1: gamma 552, vanilla 239, loose 4.

### GREEN / DEBUG findings inside the set, by pattern (strict edges, all live)

| pattern | hop 0 | hop<=1 | hop<=2 |
|---|---:|---:|---:|
| `debug_statement` (DEBUG) | 74 | **198** | 280 |
| `repeated_db_actor` (I-021) | 29 | 37 | 41 |
| `string_find_plain` | 14 | 34 | 41 |
| `uncached_globals_summary` | 12 | 20 | 29 |
| `repeated_time_global` (I-040) | 20 | **24** | 24 |
| `table_insert_append` | 2 | 8 | 11 |
| `pow_op_simple` | 1 | 8 | 8 |
| `redundant_not_eq` | 7 | 8 | 8 |
| `repeated_db_storage` | 3 | 5 | 6 |
| `distance_to_comparison` | 2 | 5 | 5 |
| `repeated_device` | 4 | 4 | 4 |
| all other GREEN patterns, summed | 5 | 17 | 22 |
| **GREEN total** | **103** | **178** | **206** |

By origin at hop<=1: gamma 129 GREEN / 89 `debug_statement`, vanilla 47 GREEN / 109
`debug_statement`.

For context, the same 1350 live files hold **2410** `debug_statement` and **2142** GREEN
findings in total, so one hop reaches 8.2% of the live debug statements and 8.3% of the live
GREEN findings, against 3.1% and 4.8% for the name-based set.

**The honest reading of the GREEN column: the reach doubles and the absolute numbers stay
tiny.** I-040 gains four sites. I-021 gains eight. The two patterns the callee hop was
supposed to unlock are the two that move least, because `xr_logic` and friends are *already*
written without repeated `db.actor` reads. What the hop actually reaches in quantity is
`debug_statement` (2.7x) and `string_find_plain` (2.4x, and `string_find_plain` is measured at
exactly 1.00x, i.e. worth nothing).

### Hottest one-hop callees, by number of distinct per-frame callers

| callee | per-frame callers | jit_mode | abort sites | origin |
|---|---:|---|---:|---|
| `_g.time_global` | 126 | interpreted | 1 | vanilla |
| `xr_logic.try_switch_to_another_section` | 26 | interpreted | 7 | vanilla |
| `_g.printf` | 23 | mixed | 2 | vanilla |
| `_g.clamp` | 16 | compiled | 0 | vanilla |
| `_g.alife_object` | 16 | interpreted | 2 | vanilla |
| `xr_logic.pick_section_from_condlist` | 10 | interpreted | 21 | vanilla |
| `_g.GetEvent` | 10 | compiled | 0 | vanilla |
| `_g.IsStalker` | 10 | mixed | 1 | vanilla |
| `_g.UnregisterScriptCallback` | 9 | compiled | 0 | vanilla |
| `_g.CreateTimeEvent` | 9 | mixed | 1 | vanilla |
| `_g.character_community` | 8 | mixed | 1 | vanilla |
| `_g.main_hud_shown` | 8 | compiled | 0 | vanilla |
| `_g.has_alife_info` | 7 | interpreted | 2 | vanilla |
| `xr_logic.issue_event` | 7 | interpreted | 1 | vanilla |
| `_g.SendScriptCallback` | 6 | compiled | 0 | vanilla |
| `_g.load_var` | 6 | interpreted | 1 | vanilla |
| `_g.IsWeapon` | 6 | mixed | 1 | vanilla |
| `_g.SYS_GetParam` | 6 | compiled | 0 | vanilla |
| `xr_sound.update` / `xr_sound.set_sound_play` | 6 / 6 | compiled | 0 | vanilla |
| `ui_options.get` | 6 | mixed | 1 | gamma |
| `_g.strformat` | 5 | interpreted | 1 | vanilla |
| `_g.get_object_squad` | 5 | interpreted | 5 | vanilla |
| `xr_logic.switch_to_section` | 4 | interpreted | 1 | vanilla |
| `_g.IsWounded` | 4 | interpreted | 4 | vanilla |
| `item_mine.evaluate_actor_mines` | 3 | interpreted | 10 | vanilla |

The head of that list is a small set of `_g.script` helpers, not mod code. `_g.time_global`
is worth its own line: **vanilla `_g.script:761` defines `function time_global() return
device():time_global() end`**, so the thing 126 live per-frame bodies call is a Lua wrapper
around two engine C calls, not the engine global that `ENGINE_NYI_GLOBALS` assumes. I-040's
per-body cache therefore removes more than the microbench stub suggests, and the microbench
number (below) is a floor.

### Known positives (validated before any count was quoted)

| function | in the set? |
|---|---|
| `xr_logic.pick_section_from_condlist` | **hop 1**, via `module.func`; interpreted, 21 abort sites, from `VANILLA_DB` |
| `axr_main.make_callback` | **hop 2**, via `_g.SendScriptCallback` -> `axr_main.make_callback`; from the **loose** `Anomaly/gamedata/scripts/axr_main.script` |

`make_callback` is **not** reachable in one hop and never will be: the chain is
`binder:update -> SendScriptCallback (a global in _g.script) -> axr_main.make_callback`.
One hop restricted to same-file + `module.func` misses it twice over — wrong hop count and
wrong edge kind. Reaching it needs hop 2 **and** the `global` edge. That is the single
strongest argument for the callee hop existing at all (agent-I043 measures that one dispatch
at 8.1 us compiled / 44.6 us interpreted at the live listener count of 73), and it is also
the clearest statement of how far a *one*-hop rule falls short.

Two caveats on `make_callback` specifically: the census classifies the loose copy as
`compiled` with 0 abort sites, which is wrong — it calls `spairs`, a Lua function whose body
sorts and builds a closure. The intra-procedural classifier cannot see through a Lua call, so
a "compiled" verdict on any body that calls user Lua is a guess (this is the same limit
`_calls_are_decidable` already documents for I-005). And per agent-I043, the live `spairs` is
the min-heap `hspairs` installed by loose `_g_patches.script:1049`, not `_g.script`'s.

## 5. Site arithmetic against the 4770 us frame (deliverable 5)

Per-site costs from `tools/microbench.py` / `lab/reports/microbench-baseline.json`
(LuaJIT 2.0.1774896119 x64, best of 25, `collectgarbage()` before every timed run,
N = 2e6 compiled / 3e5 interpreted). Interpreted arm, because the set is 53% interpreted:

| pattern | saved per execution (interpreted) | sites at hop<=1 |
|---|---:|---:|
| `string_literal_concat` | 15.8 ns | 3 |
| `table_insert_append` | 5.6–13.4 ns (K-dependent) | 8 |
| `repeated_db_actor` | 6.1 ns per removed duplicate read | 37 |
| `math_pow_simple` | 4.1 ns | 1 |
| `repeated_time_global` | 2.5–3.8 ns (Lua stub; see §4, the real one is a wrapper around two C calls, so this is a floor) | 24 |
| `distance_to_comparison` | 1.9 ns | 5 |
| `uncached_globals_summary` | 1.15 ns per call site | 20 |
| `pow_op_simple` | 0.2 ns | 8 |
| `string_find_plain`, `redundant_not_eq` | 0.0 ns (1.00x both modes) | 42 |

The in-game gate is **0.5% of frame time = 23.9 us**; the near-miss band worth flagging for
the profiler is 0.25–0.5%, i.e. **11.9–23.9 us**.

**Generous upper bound.** Give every one of the 178 GREEN sites at hop<=1 the best number in
the table (15.8 ns) and assume each executes once per frame: **2.8 us, 0.059% of the frame**.
At a realistic 6 ns average: 1.1 us, 0.022%. To clear 23.9 us at 6 ns per execution you need
~4000 site-executions per frame; there are 178 sites, so each would have to run 22 times
every frame. Hop 2 adds 28 more sites and changes nothing.

Two corrections from agent-I043's measured run `20260919-192703-I-043-3f2729`, both of which
tighten the margin and neither of which changes the verdict:

- **The real denominator for a script-side rewrite is now measured: total Lua is ~712 us of
  the 4770 us frame** (I-048's profiler, `gammabaseline`, standing still). The generous 2.8 us
  is 0.39% of *script* time, not just 0.059% of the frame. Still far under a frame-based gate,
  but the honest framing is that ALAO is competing for a 712 us slice, not a 4770 us one.
- **`tools/microbench.py` is a lower bound for allocation-heavy rewrites**, by I-043's
  argument: the protocol's mandatory `collectgarbage()` before each timed run plus best-of-9
  excludes exactly the GC cost that an allocating rewrite removes, and their bench
  under-predicted the measured saving by 2.2–3.7x. Most patterns in the table above are
  scalar caching that allocates nothing, so the factor should not apply to them — but if it
  applied in full to all of them, the generous bound becomes ~11 us, which lands **just under
  the 11.9 us near-miss floor**. That is a thinner margin than a 40x gap and it is worth
  saying out loud.

**Nothing in the GREEN set clears 0.5% of the frame, before or after the propagation, and
nothing is in the 0.25–0.5% near-miss band** — though under the worst-case reading of the
microbench caveat the generous bound arrives within ~7% of that floor rather than an order of
magnitude below it. The realistic estimate (1.1 us, or ~4.4 us with a 4x GC factor) stays well
clear. This is the same verdict I-021 and I-040 already got in-game, now with the enlarged
set. If anyone wants to overturn it, the thing to attack is the allocation question, not the
site count.

`debug_statement` is the one family where the arithmetic is not obviously dead, and only
because its per-call cost is two orders of magnitude larger. Measured Lua-side cost of one
vanilla `_g.printf("... %s ...", a, b)` with `log()` stubbed to a counter (fresh `LuaRuntime`
per arm, `jit.off(chunk, true)` for the interpreted arm, warm-up 1000 calls,
`collectgarbage()` before each timed run, best of 9, N = 2e6 / 3e5):

| arm | compiled | interpreted |
|---|---:|---:|
| `printf` with two `%s` args | 776 ns/call | 763 ns/call |
| `printf` with no args | 0.4 ns/call | 25 ns/call |
| the same line commented out (what `--fix-debug` leaves) | 0.2 ns | 2.2 ns |

The formatted form costs the same compiled and interpreted because it never compiles: it
builds a closure (`BC_FNEW`) and calls `string.gsub` with it, per call. `jit.off` did take
effect — the empty-loop arm is 0.2 ns compiled against 2.2 ns interpreted, 11x, which is the
self-check for these three rows. And **vanilla
`printf` has no `DEV_DEBUG` guard** — it always reaches the engine's `log()`, which writes to
`xray_*.log`. That write is not measurable from here and is certainly the larger half.

So, for I-044: 198 `debug_statement` sites at hop<=1 (gamma 89 / vanilla 109), 280 at hop<=2.
At 770 ns of Lua alone, **31 of them executing once per frame is 23.9 us = the 0.5% gate**,
and 16 puts it in the 0.25–0.5% near-miss band; the engine log write pushes both thresholds
lower still. If all 198 ran every frame it would be 152 us, 3.2% of the frame. So
`debug_statement` is the **only** family in this census that can clear 0.5%, and whether it
does turns entirely on how many of the 198 are behind `if DEV_DEBUG`-style guards and how
many NPCs are online — which a static census cannot answer. Classify it as
**near miss / above gate, pending measured call frequency from the I-048 profiler**: the
per-call cost is measured, the frequency is not.

The 770 ns is itself a **lower** bound, for two independent reasons that both point the same
way. The engine `log()` write is stubbed out here. And by I-043's microbench argument the
protocol's `collectgarbage()` excludes GC cost, which `printf` generates in quantity — a
`{...}` varargs table, the `sr` closure and the `string.gsub` result string, per call. So the
threshold count is at most 31 and realistically lower.

### What the profiler (I-048) should instrument

The census cannot see call frequency at all: a one-hop callee may run 0 or 200 times per
frame. Named, in priority order:

1. **`_g.printf` call count and total ms per frame.** Decides I-044 outright.
2. **`axr_main.make_callback`** — total ms per frame and per event name, at the live listener
   count. Decides I-043.
3. **`xr_logic.try_switch_to_another_section`** (26 per-frame callers) and
   **`pick_section_from_condlist`** (10 callers, 21 abort sites) — invocations per frame.
   These are the two bodies the whole callee hypothesis rests on.
4. **`_g.time_global`** — 126 per-frame callers, and it is a Lua wrapper, so its per-call cost
   is not the `ENGINE_NYI_GLOBALS` assumption.
5. The 29 registration-only handlers (`process_queue`, `batt_checker`, `check_nv_state`, …):
   are they throttled internally, or do they run every frame?

## 6. Gates

Two analyzer changes, both report-only. Corpus runs under the `corpus` lock, both corpora,
compared against the gen-3 baselines `20260919-182320-gamma-integ-gen3-base` /
`20260919-182421-vanilla-integ-gen3-base`.

### `0261a19` — the two `ENGINE_NYI_METHODS` entries

Full corpus runs, `20260919-183816-gamma-0-9-4-fix` and `20260919-183934-vanilla-db-fix`:

| gate | result |
|---|---|
| G4 compile failures | 0 (gamma), 0 (vanilla) |
| G5 idempotence violations | 0, 0 |
| G6 analyze / fix | gamma 12.0 s / 22.7 s vs baseline 11.6 / 21.9 (+3.4% / +3.7%) |
| G7 findings | **no per-pattern change on either corpus**; findings total 10688 / 4665, files_modified 571 / 217, edits_applied 6493 / 3620, all unchanged |
| byte-identity | **0 of 1503 and 0 of 828 fixed files differ** from the baseline trees (sha256) |
| G8 pytest | 478 passed, 7 skipped, 4 xfailed |
| G9 capture scan | 0 captures over 788 originals, 1203 inserted declarations |

### `8a80d0f` — the local-function per-frame fix

**Its full corpus run did not happen: the `corpus` lock waits on the `game` lock, and the
FPS runner held the game continuously (I-048's validation run, then I-043's) for the rest of
my slot.** I did not run an 8-worker corpus job alongside a frametime capture. G7 for this
commit is therefore established by a targeted check instead, which is cheap enough to run
without the lock and is in fact *tighter* than the corpus diff for this particular change:

- Only a file containing `local function <per-frame name>` can be affected. Of the baseline's
  rewritten files, **43 contain one; transforming all 43 at HEAD reproduces the baseline's
  fixed file byte for byte, 0 differ.**
- Of the files the baseline did **not** rewrite, 10 MO2-layout files contain one;
  **0 of them become modified** at HEAD.

So no file changes and no file starts changing. That is what G7 would have measured. What is
*not* covered: G4/G5/G6 for this commit (they cannot regress from appending to a list, but
they are unmeasured), and the new `per_frame_callback` / `jit_mode` finding counts, which
will rise — that rise is the expected and only G7 movement. The organizer should re-run

```
coord run corpus --ttl 900 -- py -3.12 tools\corpus_run.py --corpus ...\extracted\gamma --corpus-name gamma-0.9.4 --fix-flags=--fix --keep-work
```

on both corpora at merge time. G8 (478 passed) and G9 (0 captures) above were both run at
`8a80d0f` or later.

G6 on the vanilla corpus is **unresolved at 10% resolution and not attributable**: my runs
came in at 6.8/12.8 and 6.4/13.8 against a 5.7/10.9 baseline, but a control run of the
*unmodified* main-checkout code taken minutes later on the same machine gave 6.8/12.2, i.e.
the baseline was simply measured on a quieter box. Five agents share this machine. The GAMMA
corpus, which is three times the size and therefore the better timer, is clean at +3.4% /
+3.7%. Neither change can cost time by construction: one adds two entries to a frozenset, the
other appends to a list.

## 7. Verdict

**Keep the two analyzer fixes. Do not build a propagating classifier into ALAO yet.**

The propagation works and doubles the reach, and the `local function` bug it exposed is worth
the whole exercise on its own. But the thing the enlarged set was supposed to unlock —
more GREEN sites on the frame path — gains 75 sites worth about 1 us of a 4770 us frame,
against a 23.9 us gate. The set is only worth wiring into the analyzer once there is a
transform whose per-site value is large enough to care where it lands, and today the only
candidate is `--fix-debug` at ~770 ns plus a log write per site: 31 of its 198 in-set sites
firing once per frame clears the gate, which needs I-048 to settle.

A one-hop rule would also miss the single most valuable target: `axr_main.make_callback` is
two hops away across a `_g.script` global. Any future gating rule has to be hop-2 with global
edges, or it is gating on the wrong set again.
