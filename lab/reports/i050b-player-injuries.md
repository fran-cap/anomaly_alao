# I-050b: where zzz_player_injuries' 74 us/frame goes

agent-I050b, gen-4, 2026-09-19/20. Branch `agent/gen4-I050b`.

## Which copy is live

Three corpus mods ship `zzz_player_injuries.script`. In the live G.A.M.M.A modlist
they rank **479- Voiced Actor Refined - SaloEater (280)** < *G.A.M.M.A. Medications
Balance (377)* < *184- Body Health System - Grokitach (627)*, so **479's copy wins**,
and that is the one `lab/coord/overlays/ref3-alao-b` carries - i.e. the 74 us was
already measured *with* plain ALAO `--fix` applied, and question (a) is spent. Line
1503 in that rewritten copy is exactly `function actor_on_update()`.

`zzz_player_injuries_mcm.script` is a different mod's file; the live winner there is
*G.A.M.M.A. Medications Balance*. The live MCM values (read from
`GAMMA/mods/G.A.M.M.A. MCM values .../gamedata/configs/axr_options.ltx`) are
`TEXT_BASED_PATCH = false` and `NEW_LIMB_PENALTIES_FEATURE = true`, so the **picture**
HUD branch runs and all four limb-penalty helpers do real work.

## What plain ALAO already bought

The rewritten copy differs from upstream in `actor_on_update` by `local tg =
time_global()` and `local pairs_ = pairs`, plus `local actor = db.actor` and `local
mrandom = math.random` hoists elsewhere. Those are already inside the 74 us. ALAO did
**not** touch the two things that dominate the frame, because both are cross-function:
`get_hud()` repeated in `HUDUpdate` *and* in the `ParamBar` closure it calls 14 times,
and `time_global()` once per `ParamBar` call.

## Where the 74 us goes

Measured offline with `lab/tools/i050b_injuries_env.py`: the live rewritten file loaded
into LuaJIT 2.0 with every engine global replaced by a counting Lua stub, driven to the
steady state the profiler saw (healthy, standing still, all HUD statics created).

**104 Lua->C boundary crossings per frame**, and essentially nothing else:

| crossings | what |
|---:|---|
| 29 | `hud:GetCustomStatic` - 2 per `ParamBar` call x14, plus 1 for the main static |
| 16 | `get_hud()` - once per `ParamBar`, twice in `HUDUpdate` |
| 16 | `time_global()` - once per `ParamBar`, plus `actor_on_update` and `bhs_concussion` |
| 14 | `bar:SetProgressPos` - the actual HUD work |
| 7 | `actor:get_movement_speed()` and 6 vector component reads |
| 6 | `ini:section_exist`/`line_exist`/`r_string` from two MCM reads |
| 5 | `ActorMenu.get_maingame` + `m_ui_hud_states` get/set x2 |
| 3 | `actor.health` reads (one of them dead) |
| 2 | `level.set_pp_effector_factor` |
| 2 | `speed.add_speed` |
| 4 | `game.get_game_time`, `CTime:diffSec`, `actor.power`, `cs:wnd` |

Classification: **no** string building and **no** allocation of consequence on the live
path - the `for i=1,68` padding loops I-039 called cold really are cold, they live in
`PartialDamage`'s both-legs-broken branch and in the *text* HUD branch, neither of which
a healthy actor in the picture-HUD configuration reaches. The frame is boundary
crossings, and about two thirds of them re-fetch something that cannot change inside one
frame or re-ask a question this script already knows the answer to.

Two structural bugs found on the way:

* `ini_file_ex:r_value` (in `_g.script`) caches with `if (cache_result) then`, so an
  option whose stored value is **false** never hits the cache. `TEXT_BASED_PATCH` is
  false, so each of the two per-frame `get_config("TEXT_BASED_PATCH")` calls pays
  `section_exist` + `line_exist` + `r_string`. This is general: any MCM boolean that is
  off is uncached everywhere in GAMMA.
* `hidehudonce` at line 1575 is declared `local` *inside* `actor_on_update`, so the
  "do this once" latch resets every frame and the HUD-bar suppression re-runs forever.

## The patch

`lab/tools/i050b_injuries_patch.py`, byte-exact string surgery on the live winner with
every anchor asserted to hit exactly once. **104 -> 45 crossings/frame.**

| change | crossings |
|---|---:|
| one `get_hud()` per HUD pass instead of 16 | -15 |
| one `time_global()` threaded from `actor_on_update` instead of 16 | -15 |
| `bhs_garbage` lookup: provably dead branch (nothing anywhere adds that name) | -6 |
| `<bar>_bg` removal decided from a Lua-side set; the *add* path still asks the engine | -8 |
| `TEXT_BASED_PATCH` read once at load, as `hide_default_hud`/`showtexthud` already do | -6 |
| dead `actor:get_movement_speed()` + its 6 component reads | -7 |
| dead `actor.health` read, dead `hud_d:wnd()` | -2 |

`bar:SetProgressPos` - the only call in the frame that does real UI work - is untouched,
deliberately.

The `bhs_garbage` claim was checked twice: over `extracted/gamma` (1503 mod scripts) and
then over the whole live `GAMMA/mods` tree. The only `.script` files that mention the
name are the three copies of this file, all in this same dead branch. It also appears in
three `configs/ui/ui_custom_msgs.xml` - that is where custom statics are *declared* for
`AddCustomStatic` to find, not code that adds one - and no script ever adds it, so
`GetCustomStatic("bhs_garbage")` is always nil. Same check for the `<bar>_bg` names: only
`ui_custom_msgs.xml` declarations, no other writer.

### Not in the patch: the optional variants

* **HUD-bar suppression once instead of every frame** (-5 crossings). Fixing the
  `hidehudonce` latch would stop re-asserting `m_ui_health_bar_show = false` every
  frame. Today, if another mod turns those bars on, this script turns them off again
  within one frame; with the latch fixed it would not. Observable, so it stays out.
* **skip `level.set_pp_effector_factor` when the factor has not changed** (-2). Safe
  only if nothing else drives effectors 99123/99133.
* **skip `SetProgressPos` when the value is unchanged** (-12 standing still, and these
  are the expensive ones). Defensible - a progress bar's position setter is idempotent -
  but it is the one call that actually reaches the UI, so proving it offline is not
  possible. This is the obvious next step if the in-game run confirms the model.

## Evidence

`lab/tests/test_i050b_injuries.py`, 27 tests. Both arms are driven through the same
scripted sequence of stubbed states - healthy idle, taking damage, healing with two
different items, limbs breaking and healing, actor health/stamina moving, the HUD key
cycling all three modes, inventory preview bars, MCM option change, sleep, death,
footsteps with limping legs - in **both** HUD configurations, and every engine call that
changes anything must come out byte-identical, in order, with identical arguments. It
does. The script's own `health` / `timedhp` / `hud_blink_timer` tables must also match at
the end. They do.

The patched file LuaJIT-compiles (`loadstring` under `lupa.luajit20`).

## The timing bench: NOT RUN

`lab/tools/i050b_bench.py` exists and is wired up, but **no number from it is quoted
anywhere in this report**. The `game` lock was held continuously by the gen-4 in-game
queue (four items back to back, ~2.5 h) and beam-ideas.md section 2 forbids quoting a
bench taken under a lock. Run it on a quiet box with no lock held before or after:

```
py -3.12 C:\code\GIT\anomaly_alao\lab\tools\i050b_bench.py --iters 20000 --json i050b-bench.json
```

It builds the patched file itself, checks that `jit.off` actually took, and prints
us/frame for both arms with JIT on and off (fresh `LuaRuntime` per arm per mode,
`collectgarbage()` before every timed run, best of 9). What it would add: the split
between Lua work and boundary cost. If the Lua-only frame turns out to be a large part
of 74 us, the proportional attribution below is too optimistic and the band should move
down; if it is a few us, as the crossing census suggests, the band stands. Everything
below is a count, not a timing, so it is load-independent.

## Arithmetic

The offline harness can count crossings exactly but cannot price them: a stub call is a
Lua call, the real thing marshals through luabind into C++. So the price comes from the
in-game measurement - 74 us over 104 crossings, ~0.7 us each - and the prediction is
proportional attribution:

* **59 of 104 crossings removed = 57%**, i.e. **~42 us/frame** if every crossing costs
  the same.
* Pessimistic read: the removed calls are mostly trivial getters (`get_hud`,
  `time_global`) and the retained ones include the 14 `SetProgressPos`. If the removed
  ones cost half the average, **~21 us/frame**.
* Central estimate **~34 us/frame**, band **21-42**. The bar is 25 us.

That is 0.5-0.6% of a 4.7 ms frame and 3-6% of the ~712 us of script time per frame.

## Other listeners in the ranking (no patches, read only)

Ranked by *safe* payoff:

| file / listener | crossings/frame | zero-behaviour-change opportunity | est. saved |
|---|---:|---|---:|
| `actor_effects.script:1641 actor_on_update` (13 us) | ~100-135 | `Update_Fog` calls `HUD_fog(false)` every frame when breathing fog is on but no helmet: `get_hud()` plus a 4x10 loop of `Frect():set` + `SetWndRect`, ~80 crossings, for a no-op. Latch it. | ~9-11 us |
| `battery_warning.script:39 batt_checker` (8-10 us) | ~6-8 | The 10 s throttle sits *below* the pda/inventory menu guard. Moving the `tg < tg_update` early return above it is bit-identical and makes the 4-5 menu crossings run once per 10 s. | ~5-6 us |
| `fluid_aim.script:33 actor_on_update` (13 us) | ~20-25 | `wpn:section()` 3-4x and `SYS_GetParam(...,"kind")` 3-4x with identical args; `get_state()` twice; `aim_toggle` read from options every frame though `on_option_change` is already registered. | ~5-6 us |
| `light_gem_mcm.script:20 light_gem` (8-10 us) | ~11-13 | `get_hud()`+`GetCustomStatic` every frame only to detect first init; MCM `icon` read every frame; `Show()`/`SetTextureColor` re-set with unchanged values. | ~4-5 us, but the cached static must be invalidated on level change or it is a stale pointer |
| `sound_ambient.script:277 actor_on_update` (8-10 us) | ~12-18 | Only the two `level.*` rain reads when both wind and rain effects are off. The volume-dedupe would be bigger but is not zero-behaviour. | ~1 us safe |
| `liz_inertia_expanded.script:196 actor_on_update` (12 us) | ~16-20 | `actor.power` read twice, `is_overriden` called twice per lerp. The 5 `game.play_hud_anm` calls are the bulk and cannot go. | ~1-1.5 us |

Note for whoever picks these up: three of the six (`light_gem_mcm`, `battery_warning`,
`liz_inertia_expanded`) are **not** in `ref3-alao-b` - ALAO never rewrote them - so their
baseline arm is the stock file.

## For ALAO the tool (described, not implemented)

1. **Repeated stable engine getter across a function and its nested closures.** ALAO
   already hoists `time_global` and `db.actor`; `get_hud()` is the same shape and it is
   the single biggest item here. Safety: `get_hud()` takes no arguments and returns the
   process-wide `CUIGameCustom`; caching it for the duration of one function body is
   safe as long as the body cannot yield or reload the level, which no Lua body can.
   **But the corpus does not justify it**: a scan of `extracted/gamma` finds only 8
   functions with >=2 `get_hud()` calls, 11 redundant calls, in 3 files - and all three
   are copies of this script. `device()` is the interesting one at **121 functions / 177
   redundant calls / 59 files**, though most are UI construction rather than per-frame.
2. **Dead local whose initialiser is a known-pure engine getter.** `local speedvector =
   actor:get_movement_speed()` with no reader is 7 crossings a frame. ALAO has
   `dead_code_*` but nothing for an unused local. Safe only against a whitelist of
   pure getters - which `ast_analyzer.py` already half has in its `CACHEABLE_*` /
   `EXPENSIVE_INDEXES` tables.
3. **A RED finding ALAO cannot currently reach: a callee invoked N times per call of a
   per-frame body.** `ParamBar` has exactly one `time_global()` and one `get_hud()` in
   its body, so no intra-body repetition exists to detect; the cost only appears because
   `HUDUpdate` calls it 14 times. This is the same interprocedural blind spot I-041
   named ("the hottest live code is in callees the name-based classifier never sees").
