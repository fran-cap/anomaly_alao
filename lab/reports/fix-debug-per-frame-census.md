# I-044 — `--fix-debug` in the live per-frame path: census, cost, verdict

Question: I-041 counted 34 `debug_statement` findings inside live per-frame vanilla bodies and
suggested an FPS arm of its own for `--fix-debug`. Is the mod side big enough to make that arm
worth 40 minutes of the game lock?

**Verdict: no. Count it and stop.** 57 debug calls sit inside live per-frame bodies across the
whole live stack; 9 of them are unguarded, and only 3 of those are reached at all in a
standing-still gammabaseline save. The site arithmetic puts the saving at **0.2 us (low) to
19.6 us (high) per 4770 us frame, i.e. 0.004%–0.41%**, under the 1%-of-frame bar for queuing an
in-game run and far under the ~2% the FPS harness can resolve. The absolute ceiling — every one
of the 57 sites an unguarded `printf` that reaches the engine log, once per frame — is 0.73%,
so no assumption about callback rates rescues this.

Two by-products that do matter:

* **`--fix --fix-debug` is not idempotent** on one corpus file (G5 = 1 on vanilla-db). New strict
  xfail `test_fix_debug_second_pass_changes_nothing` in `tests/test_transformer.py`.
* `--fix-debug` comments out **4864 statements** across the two corpora (2412 gamma live+shadowed,
  2452 vanilla) and **none of them has an argument that mutates anything** — checked call by call.

## 1. How "live" was resolved

Three layers, highest priority first (this is the correction from the organizer, mid-run):

1. the highest-priority enabled mod in the live G.A.M.M.A. modlist that ships the path
2. else the loose patched script in `<install>/Anomaly/gamedata/scripts` (68 files; GAMMA patches
   the game in place and these beat the `.db` archives)
3. else the vanilla db copy (`extracted/vanilla_db`)

`lab/tools/i044_debug_census.py --full-stack` builds that set: **1317 live files**. The loose
layer contributes 0 per-frame debug sites and shadows none of the 57 found below, so the
corrected number equals the two-layer number — but only by luck, and the tool now does it right.

**Known-positive validation:** run with `--bottom` against `extracted/vanilla_db` alone the tool
reproduces I-041's vanilla number exactly — 317 live files, **34** sites, and the same per-body
distribution (sim_squad_warfare 6, xr_animpoint 6, sr_monster 4, warfare 4, smart_terrain_warfare 3,
bind_stalker 2, txr_mines 2, and seven singletons). That is what makes the GAMMA figure quotable.

## 2. The count

`debug_statement` findings inside a per-frame body (name-based I-010/I-013 classifier), live stack:

| | sites |
|---|---:|
| **total** | **57** |
| from a mod (live winner) | 23 |
| from the vanilla db (nothing shadows it) | 34 |
| from the loose `Anomaly/gamedata/scripts` layer | 0 |
| callee `printf` / `printd` / `log` / `printe` | 44 / 11 / 1 / 1 |
| body jit_mode interpreted / mixed / compiled | 49 / 7 / 1 |
| behind an explicit debug flag (`self.Debug`, `debug_dump`) | 10 |
| behind some other condition (error path, init path, one-shot) | 38 |
| **unguarded (runs whenever the body runs)** | **9** |

Corpus totals for context: 2586 `debug_statement` findings on GAMMA (1503 files), 1246 on
vanilla-db. So 57 of 3832 debug findings — 1.5% — are anywhere near a frame.

Top bodies: `itms_manager ItemProcessor:update` 6 (all `self.Debug`-guarded),
`sim_squad_warfare squad_on_update` 6, `surge_manager CSurgeManager:update` 6 (all during a surge),
`xr_animpoint animpoint:update` 6 (all error paths), `sr_monster fake_monster:update` 4,
`tasks_placeable_waypoints UIPAWHUDPin:Update` 4 (`debug_dump`), `warfare actor_on_update` 4.

### The nine unguarded sites, and which are actually reached

| file | body | line | call | reached standing still? |
|---|---|---:|---|---|
| warfare.script | `actor_on_update` | 273 | `printd(0, "actor_on_update")` | **yes, 1/frame** — `warfare.on_game_start` registers `actor_on_update` unconditionally |
| sim_squad_warfare.script | `squad_on_update` | 53 | `printd(0, "squad_on_update: "..squad:name())` | **yes**, per squad dispatch (it is the first line, before the `IsWarfare()` bail-out) |
| smart_terrain_warfare.script | `smart_terrain_on_update` | 142 | `printd(0, "smart_terrain_on_update "..smart:name())` | **yes**, per smart-terrain dispatch, same reason |
| warfare.script | `actor_on_update` | 367, 424 | `printd(2/3, ...)` | no — after `if not (IsWarfare()) then return end` |
| sim_squad_warfare.script | `squad_on_update` | 149 | `printd(3, squad:name())` | no — same bail-out |
| smart_terrain_warfare.script | `smart_terrain_on_update` | 228 | `printd(2, smart:name())` | no — same |
| xr_detector.script | `actor_detector:update` | 44 | `printf("INTENCE %s", intence)` | no — two early `return`s above it (`init_time == -1`, and a `diffSec < idle_time` throttle) |
| lam2.script (FDDA Redone) | `actor_on_update` | 272 | `log("[CORE] LAM2 Update: ...", list_actions_count(), ...)` | only while an FDDA animation is queued; `actor_on_update` is Register/Unregistered around the action list |

## 3. What these calls actually do

Read out of the live winners, not assumed:

* **`printf`** (live `_g.script`, vanilla db — no mod and no loose file overrides it) is a plain
  Lua function with **no debug flag at all**: `tostring(fmt)`, then, when there is at least one
  vararg, a `string.gsub` over the format with a **closure** substitution function (one closure
  and one Lua call per `%s`), then `log(fmt)`. A reached `printf` always writes the engine log.
* **`log`** is wrapped by the loose `_g_patches.script` (line 729-735): `log = _G.log; _G.log =
  function(str) log(str); if DebuggerMode then LuaPanda.printToVSCode(...) end end`. So every
  `printf` pays one extra Lua call and a global read on top of the C call. My bench does not
  include that wrapper, so the printf numbers below are a touch optimistic.
* **`printd`** (warfare.script's own) reads `warfare_options.options.debug_logging` and returns.
  Nothing reaches the log with warfare's logging off — **but Lua evaluates the arguments first**,
  so `"squad_on_update: "..squad:name()` still costs an engine getter plus a BC_CAT allocation on
  every call.
* **`lam2.log`** is flag-guarded inside (`b_is_debug_enable`); its call site still evaluates
  `list_actions_count()` and two ternary chains per frame.

## 4. Microbench: what one site costs

`tools/microbench.py --bench-dir bench/i044`, repo protocol, no opt-out: LuaJIT 2.0 via
`lupa.luajit20`, fresh `LuaRuntime` per (case, arm, mode), `jit.off(f, true)` on the chunk with
the self-check (interpreted/compiled = 24.3x on this run), two warm-up calls, `collectgarbage()`
before every timed run, best of 9, `_G.__sink`, 64-float `D`. The "rewrite" arm is the statement
commented out, which is literally what `--fix-debug` emits, so the number is *the cost of keeping
the site*. Taken 2026-09-19 with no `game` lock held (other agents were active on the box; the
jitter column was clean).

| shape | N (on/off) | ns per call, JIT on | ns per call, interpreted |
|---|---|---:|---:|
| `printd(0, "actor_on_update")`, flag off | 2e6 / 3e5 | ~0 (hoisted out) | **10.5** |
| `printd(0, "lit"..obj:name())`, flag off | 2e6 / 3e5 | 38.0 | **29.5** |
| real `_g.printf` with 2 `%s`, `log` = empty Lua fn (**lower bound**) | 3e5 / 1e5 | 549 | **531** |
| same, `log` writes the line to a file (**upper bound**) | 2e5 / 6e4 | 635 | **609** |

Interpreted is the operative column: 49 of the 57 bodies are interpreted, 7 mixed, and the C
calls plus `..` abort any trace anyway. The `:name()` stub is a plain Lua method returning an
upvalue — the cheapest possible stand-in — so the concat row is a **lower bound**; a real luabind
getter is several times that. I cannot measure the engine's log write offline, hence the bracket;
the truth sits between the last two rows and above them whenever the log flush is on.

## 5. Site arithmetic against the 4770 us frame

Assumptions, stated because they carry the result:

* 20-40 online NPCs on the gammabaseline save (standing still), which is where per-object
  callbacks multiply. **This is an assumption, not a measurement.**
* `squad_on_update` / `smart_terrain_on_update` are dispatched from the alife update of server
  objects, which the engine cycles through a subset at a time. I bracket it at **5-100 squad
  dispatches and 1-30 smart dispatches per frame**. Nothing in this report measures that rate;
  I-048's profiler is the tool that could.
* high column uses 150 ns for the concat shape (5x the measured stub, standing in for a real
  luabind `:name()`).

| reached site | calls/frame (low-high) | ns/call (low-high) | us/frame low | us/frame high |
|---|---|---|---:|---:|
| warfare `printd(0, const)` | 1 - 1 | 10.5 - 10.5 | 0.01 | 0.01 |
| sim_squad_warfare `printd(0, concat)` | 5 - 100 | 29.5 - 150 | 0.15 | 15.0 |
| smart_terrain_warfare `printd(0, concat)` | 1 - 30 | 29.5 - 150 | 0.03 | 4.5 |
| lam2 `log(...)` (only during an FDDA anim) | 0 - 1 | 50 - 100 | 0.00 | 0.10 |
| **total** | | | **0.19 us** | **19.6 us** |
| **share of a 4770 us frame** | | | **0.004%** | **0.41%** |

Ceiling check, ignoring every guard: all 57 sites as unguarded `printf`s that reach the log, once
per frame each = 57 x 0.61 us = **34.7 us = 0.73%** of the frame. Still under the 1% gate.

**Decision: no overlays, no in-game request.** The deciding fact is not the per-call cost — a
reached `printf` at ~0.6 us is genuinely expensive — it is that **no reached live per-frame site
is a `printf`**. The 44 live per-frame `printf`s are all behind a condition that is false while
you stand still (error paths, init one-shots, surge/animpoint failures) or behind a `self.Debug`
flag; what is left unguarded is three `printd` calls whose callee returns immediately.

## 6. What `--fix-debug` does to real files

Corpus runs from this worktree (commit 52260e2 + this branch's tools/tests only — no analyzer or
transformer change), `--fix-flags "--fix --fix-debug" --keep-work`, under the `corpus` lock:

| run | files | modified | edits | analyze/fix s | G4 compile | G5 idempotence | G9 captures |
|---|---:|---:|---:|---|---:|---:|---:|
| `20260919-184233-gamma-i044-fixdebug` | 1503 | 678 | 8881 | 12.5 / 23.8 | 0 | 0 | 0 |
| `20260919-184338-vanilla-i044-fixdebug` | 826 | 261 | 4791 | 6.0 / 11.6 | 0 | **1** | 0 |
| base `20260919-182320-gamma-integ-gen3-base` (`--fix`) | 1503 | 571 | 6493 | 11.6 / 21.9 | 0 | 0 | 0 |
| base `20260919-182421-vanilla-integ-gen3-base` (`--fix`) | 826 | 217 | 3620 | 5.7 / 10.9 | 0 | 0 | 0 |

So `--fix-debug` adds 107 modified files and 2388 edits on GAMMA, 44 files and 1171 edits on
vanilla. Analyze/fix time moves +8%/+9% and +5%/+6% — inside G6, and expected, since the debug
edits are extra work on top of the same analysis.

`lab/tools/i044_fixdebug_risk.py` runs the real `ASTTransformer` in dry-run over every file and
reads the outcome **off the edit list** (priority 200 is `_edit_debug_statement` and nothing
else), not off a line-by-line diff of the output — the first version diffed text and called half
the corpus "declined", because cache-declaration insertions shift every line below them. It is
self-tested against a known-positive file that carries all three shapes (`--self-test`).

| | gamma | vanilla-db |
|---|---:|---:|
| debug call sites | 2583 | 2492 |
| commented out by `--fix-debug` | 2412 (93%) | 2452 (98%) |
| declined | 171 | 40 |
| commented, and the sole statement of an if/else branch | 702 | 726 |
| commented, with a non-pure call in the arguments | 79 | 70 |

Declines are the transformer's three guards: the call's result is bound (`local dumped =
string.dump(fn)`), the line carries a control-flow keyword (`if DEV_DEBUG then printf(fmt,...) end`
on one line — note this is what keeps one-line guards safe), or the previous line ends in an
expression continuation. None of the live per-frame sites is declined.

**Does commenting out remove the argument evaluation?** Yes — the whole statement goes, including
its argument list, which is exactly where the cost is for a `printd`-style no-op callee. Verified
on the real rewritten trees.

### Correctness risk review (step 6)

The dangerous shape is an argument with a side effect: `printf("%s", table.remove(t))` loses the
`table.remove` when the line is commented. I looked at **every distinct flagged site** (35 of
them, 149 occurrences) in both corpora. All are pure formatters or getters:
`utils_data.to_str` / `vector_to_string` / `print_table`, `time_to_str`, `strformat`, `size_table`,
`table_size`, `round_idp`, `clamp`, `character_community`, `:character_name`, `:weapon_slot`,
`load_var`, `collectgarbage("count")`, `public.format_entry` (a log-formatting helper),
`game_achievements.has_achievement`. **Zero mutate anything.** The only site I cannot decide
statically is `utils_catspaw_common.script:142`, `printf("%s %s (added %s): %s", ..., v.func())`,
where `v.func` is an arbitrary registered function — by shape a version getter, but it is a stored
callable, so a future registrant could put a side effect there.

The 702 + 726 "sole statement of a branch" sites leave an empty `if cond then ... end`, which is
legal Lua 5.1; G4 agrees (0 compile failures across 939 rewritten files).

The counter is known-positive validated: on a fixture holding `printf("popped %s", table.remove(t))`,
a sole-statement branch, a pure `printf("pure %s", o:name())` and a bound `local x = log("bound")`
it flags exactly the first two, leaves the third alone, and confirms the transformer declines the
fourth.

### The real defect: `--fix --fix-debug` is not idempotent

`VANILLA_DB/gamedata/scripts/sr_monster.script`, run `20260919-184338-vanilla-i044-fixdebug`.
Plain `--fix` on the same file is stable (pass 2 makes 0 edits); with `--fix-debug` pass 1 makes
7 edits and pass 2 makes 5 more:

```
+  local db_storage = db.storage          <- inserted only on the SECOND pass
-    if self.final_action and (db.storage[self.monster.id] == nil or ...
+    if self.final_action and (db_storage[self.monster.id] == nil or ...
```

Pass 1 comments the four `printf`s out and **declines** the `repeated_db_storage` hoist; pass 2,
reading its own output, applies it. The analyzer reports the identical finding on both inputs
(`db.storage called 4x in update`, lines 52/53/86/87 unchanged), so the divergence is in edit
generation, not analysis — the presence of the live `printf` statements in the body is what
suppresses the hoist. Both outputs compile and both are semantically correct; the bug is that the
file keeps moving, which is exactly what G5 exists to catch and what bites a user who reverts and
re-runs.

I did not fix it — the cause is in the interaction between the debug comment-out and the
`repeated_*` cache generator, which is not a small obviously-safe change. It is documented as a
strict xfail, `test_fix_debug_second_pass_changes_nothing` in `tests/test_transformer.py`, with a
25-line repro shrunk automatically out of the real file.

## 7. Files

* `lab/tools/i044_debug_census.py` — live-stack per-frame debug census (`--full-stack`,
  `--bottom`, `--top`); validated against I-041's 34.
* `lab/tools/i044_fixdebug_risk.py` — what `--fix-debug` comments out and which shapes are risky
  (`--self-test` for the known positives).
* `bench/i044/*.lua` — four microbench pairs (kept out of `bench/` so the default table and the
  GREEN coverage guard are untouched).
* `tests/test_transformer.py::test_fix_debug_second_pass_changes_nothing` — the new strict xfail.
