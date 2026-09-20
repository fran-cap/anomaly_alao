# alao-prewarm — move player-triggered hitches into the loading screen (I-063)

One script, `gamedata/scripts/zzz_alao_prewarm.script`. It replaces no game file
and patches no function: it calls two public entry points of two other mods and
constructs sound objects. Disable it and nothing changes except *when* that work
happens.

## The measurement it is built on

Attended hitch run `20260920-125528-I-058-f5edb8`, four captures, the I-058
hitch profiler in listener mode. Worst single call per capture, milliseconds:

| listener | c1 | c2 | c3 | c4 | shape |
|---|---:|---:|---:|---:|---|
| `ActorMenu_on_before_init_mode#ui_inventory.script:93` | 11.4 | 10.1 | 18.5 | 8.4 | the worst call was the first inventory open in all four captures; every later open 3.2-6.4 ms. **This reading did not survive: see "Retraction" below.** In the I-063 runs the worst open is sometimes the first and sometimes not, and the cause is not what this mod assumed. |
| `actor_on_leave_dialog#ui_pda_encyclopedia_tab.script:407` | 9.5 | 7.0 | 6.5 | 6.4 | one call per capture (the one that unlocked an article) |
| `actor_on_footstep#footstep_sounds.script:87` | 4.5 | 2.7 | 0.9 | 0.8 | first-on-a-new-surface, settles under 1 ms |
| `actor_on_jump#eft_jump_sounds.script:71` | 3.5 | 3.1 | 0.9 | 0.9 | same |
| `actor_on_land#eft_jump_sounds.script:89` | 3.0 | 1.2 | 0.8 | 1.2 | same |

In capture 1 the first jump, the first land and the first footstep landed within
~170 frames of each other: 3.5 + 3.0 + 4.5 = 11 ms of cold cost in one burst,
and new surfaces repeat it as the player crosses the map.

## What is actually cold

All five are the same shape — a resource built lazily on the frame the player
asked for something.

**`ui_inventory.start()`** does `if (not GUI) then GUI = UIInventory() end`.
`UIInventory:__init` runs `InitControls`, which is one
`CScriptXmlInit():ParseFile("ui_inventory.xml")` plus several hundred widget
constructions and six `utils_ui.UICellContainer`s, then `InitCallbacks`, then
twelve `RegisterScriptCallback`s. Nothing in `__init`, `InitControls` or
`InitCallbacks` touches `db.actor`, `level` or `alife()` — checked line by line
in the live copy (`G.A.M.M.A. Accurate Defense Values`, the top enabled mod that
ships the file; three other mods ship a shadowed copy). `GUI` is only set back
to nil by `UIInventory:actor_on_net_destroy`, i.e. on a level change.

**That analysis is correct and irrelevant on this install.** Two other mods
already build `GUI` at `actor_on_first_update`, before this mod's listener runs,
so `start()` never takes that branch and the per-open cost is something else
entirely. See "Retraction" and "Why an inventory open is bimodal 4-20 ms" below.
The inventory prewarm stays in the mod because it is free when `GUI` already
exists (one truthiness test) and it is the correct thing to do on an install
that does not have those two mods.

**`ui_pda_encyclopedia_tab.set_article()`** opens with `local guide = get_ui()`,
and `get_ui` is `SINGLETON = SINGLETON or pda_encyclopedia_tab()`. That
constructor is `InitControls` (`pda_encyclopedia.xml`) + `InitCallbacks` +
`InitCategories()` over every encyclopedia category, building one
`pda_encyclopedia_entry` (a `CUIListBoxItem` with a font and a translated
string) per visible category. The first article unlock of the session pays for
it; `unlock_article` reaches `set_article` through `actor_on_leave_dialog` ->
`detect_intereaction` -> `create_interaction`. That is why exactly one
leave-dialog per capture is expensive and the other is under 0.1 ms. Live copy:
`287- G.A.M.M.A. Massive Text Overhaul Project`.

**`eft_jump_sounds` / `footstep_sounds`** build a fresh `sound_object(path)` on
every call and drop it. The Lua side keeps nothing at all between calls, so the
only thing that can explain "first call ~4 ms, later calls under 1 ms" is
engine-side state keyed by the sound path. Both live copies come from
`Oleh's Extended MovementSFX`. The per-call cost that is *not* cold is the
material raycast — `get_material()` builds a `demonized_geometry_ray` object and
casts five rays per jump/land — and this mod does not touch it.

| target | what is cold (first call only) | what is paid every call | Lua vs engine |
|---|---|---|---|
| `ui_inventory.script:93` | `UIInventory()`: xml parse + widget tree + 6 cell containers — **but already built at frame 7 by two other mods on this install, so nothing here is cold in practice** | `Reset()`, `IMode_Init`, `ParseInventory` over the actor's ruck, `UICellContainer:Reinit` (sort + per-item grid scan + `UICellItem` construction when the pool grows), `ShowDialog` | mostly engine (xml, widget ctors); the Lua part is the ~600 lines of `InitControls` |
| `ui_pda_encyclopedia_tab.script:407` | `pda_encyclopedia_tab()` via `get_ui()` | `InitCategories()` + `SelectCategory` + `SelectArticle` per unlock, `size_table(locked_articles)` twice per `unlock_article`, `actor_menu.set_notification` | engine (xml, list widgets, `game.translate_string`) |
| `eft_jump_sounds.script:71/:89` | the engine's load of each `jump\jump_*` / `landing\landing_*` path | 5 geometry raycasts, one ray object per ray, `oleh_sound_utils.get_outfit_class()` | engine both ways |
| `footstep_sounds.script:87` | the engine's load of each `footstep\n_*`, `cloth\*`, `gear_rattle\*`, `ladder\*` path | queue shuffle, `db.actor:get_total_weight()`, and on ladders 4 more raycasts | engine |

## What the mod does

All of it at `actor_on_first_update` — frame 7 of the level load, with the
loading screen still up (`on_loading_screen_key_prompt` is at frame ~62 in every
capture, so there are ~55 frames of screen left), synchronously, in one go:

1. `ui_inventory.GUI = ui_inventory.UIInventory()` if `GUI` is nil.
2. `ui_pda_encyclopedia_tab.get_ui()`.
3. Construct every sound path the three sound scripts can ask for (~160).

Every construction is `pcall`ed, so a path that does not exist on someone's
install costs one failed call and is counted. There is a once-per-session latch
on step 3 (sound resources are engine-global) and none on steps 1 and 2, because
a level change nils `ui_inventory.GUI`. The mod registers **no** `actor_on_update`
listener.

### Why there is no time-slicing (measured, and it was wrong first)

Version 1.0 sliced step 3 onto `actor_on_update` with a 0.5 ms/frame
construction budget, on the theory that ~160 independent items should not land
in one frame. The attended run `20260920-165259-I-063-953526` killed that:

| capture 2 (cold OS file cache), `actor_on_update#zzz_alao_prewarm.script` | |
|---|---|
| calls over the 0.1 ms floor | 109 |
| log2 histogram | `[0,0,39,2,37,27,3,1,…]` — 37 frames at 1.6-3.2 ms, 27 at 3.2-6.4, 3 at 6.4-12.8 |
| worst | 17.7 ms |
| spread | ~110 frames from frame 7, so roughly half after the key prompt |

Blind to the arm order, the player called that capture the worst of the four.

The reason is structural, not a tuning mistake: **one cold `sound_object(path)`
costs 2-18 ms**. A time budget only decides whether to *start* another item; it
cannot bound the one it starts. So a 0.5 ms budget does not cap a slice — it
guarantees exactly one cold load per frame, for a hundred frames. A smaller
budget gives the same hitches, just more frames of them.

Synchronous costs ~250-300 ms cold and near zero warm, on an
`actor_on_first_update` that already costs ~1.3 s behind a screen nobody is
looking at. `SLICE_SOUNDS` in the script keeps the old path as an escape hatch;
it defaults off and should stay off.

The path set is read out of the **live** tables rather than hardcoded:
`eft_jump_sounds.available_materials` (7 distinct materials x 5 jump + 3 landing
samples), `footstep_sounds.available_materials` / `.limit` / `.ladder_materials`
(10 materials x 6 footstep samples, `cloth_` and `gear_rattle_` x 6, 2 ladder
materials x foot/hand x 6). The only literals are the `math.random(5)` and
`math.random(3)` from `eft_jump_sounds.script` and the two exo-suit class names,
which are file-locals this mod cannot reach.

## Behavioural non-identities

1. **`UIInventory:__init` registers twelve listeners.** Building `GUI` early
   makes them exist from the loading screen instead of from the first inventory
   open. Three of them (`actor_item_to_ruck`, `actor_item_to_slot`,
   `actor_on_item_drop`) do work *outside* their `if self:IsShown()` guard — the
   "move artefacts / helmet / backpack to the ruck when the outfit goes away"
   rules — and two (`npc_on_use`, `physic_object_on_use_callback`) call
   `start("loot", obj)`. So with this mod the **first** corpse looted and the
   **first** outfit removed in a session behave the way the second one already
   did. That is the mod author's steady-state behaviour, reached one event
   earlier, and it is the one change here a player could in principle notice.
2. **The prewarmed sound objects are never played.** They are held in a table so
   the engine keeps the resource, and nothing reads them. In particular this is
   *not* a sound-object cache handed back to the scripts: the scripts keep
   constructing their own, so overlapping footsteps still get their own objects
   and nothing can be restarted mid-play. That question — whether a cached
   `sound_object` can be re-`play`ed while still playing — is deliberately
   sidestepped rather than answered.
3. **`get_ui()` builds the encyclopedia tab with the article set as of load.**
   Every later `set_article` calls `guide:InitCategories()`, which rebuilds the
   category list from scratch, so nothing goes stale.
4. If a level change happens, `UIInventory:actor_on_net_destroy` sets
   `ui_inventory.GUI = nil` and the next level's `actor_on_first_update` rebuilds
   it. The sound queue is built once per session.

## Measured in game

Attended run `20260920-165259-I-063-953526`: captures 1 and 3 baseline
(`agent-I057-b`), 2 and 4 variant (`agent-I063-b`), the I-058 routine, hitch
profiler in listener mode. Worst single call, milliseconds:

Two runs: `20260920-165259-I-063-953526` (v1.0, sliced) and
`20260920-171714-I-063-ef2deb` (v1.1, synchronous). Captures 1 and 3 baseline
(`agent-I057-b`), 2 and 4 variant (`agent-I063-b`), the I-058 routine, hitch
profiler in listener mode.

| row | baseline | variant | verdict |
|---|---:|---:|---|
| `actor_on_footstep#footstep_sounds.script:87` | 0.81 max, 15 calls over the floor | **no call reaches the 0.1 ms floor at all** | **confirmed** |
| `actor_on_jump#eft_jump_sounds.script:71` | 0.56 | **0.16** | confirmed |
| `actor_on_land#eft_jump_sounds.script:89` | 0.50 | **0.15** | confirmed |
| `actor_on_leave_dialog#ui_pda_encyclopedia_tab.script:407` | 8.8 / 7.7 (run 1), 7.39 / 7.08 (run 2) | 1.9 / 1.9 (run 1 only) | **partial** — the v1.1 variant capture with a dialog recorded no leave-dialog row, so this rests on run 1 |
| `ActorMenu_on_before_init_mode#ui_inventory.script:93` | — | — | **RETRACTED, see below** |
| `actor_on_first_update#zzz_alao_prewarm.script` | — | 31.7 / 32.4 at frame 7 (v1.1, whole bundle) | pass |
| `actor_on_update#zzz_alao_prewarm.script` | — | **row absent** in v1.1 | pass |

The sound rows settle the one assumption the design rested on: **holding a
constructed `sound_object` does keep the engine resource, and every later
`sound_object(path)` for that path is cheap.** The footstep row disappearing
below the floor entirely is as clean a confirmation as this instrument gives.

### Retraction: the inventory row was never this mod's to claim

An earlier version of this file reported the inventory first open going
19.6 / 17.8 ms → 5.5 / 7.2 ms and credited the prewarm. **That was wrong.** Every
variant `xray.log` of both runs prints, at frame 7:

```
[alao_prewarm]   inventory: GUI already built, nothing to do
```

`ui_inventory.GUI` already exists before `zzz_alao_prewarm`'s listener runs, so
the inventory prewarm has never executed on this install. The numbers either
side of it are two different hand-driven captures of a cost that is bimodal
between 4 and 20 ms, i.e. noise, not a result. Run 2 makes that obvious:
baseline capture 1 has a max of **5.19** ms with the first open *being* that
5.19, while baseline capture 3 has a max of **19.26** ms with the first open
only **8.30**. The first open is not reliably the worst one, which also
undercuts the original I-058 reading this mod was designed from.

### Who builds `ui_inventory.GUI` first

Grepping the live modlist winners (1346 scripts, top enabled copy of each name,
then the loose `Anomaly/gamedata/scripts` patches, then the db) for
`ui_inventory.GUI =` gives 8 hits, 7 live. Two of them run at
`actor_on_first_update`, both before this mod (script load order is
alphabetical, so both register their listener before `zzz_alao_prewarm`):

| script | mod | when |
|---|---|---|
| `custom_functor_autoinject.script:421`, inside `process_queue()` called from `actor_on_first_update` at :435 | *447- FDDA Redone - lizzardman* | **first update** — and it is already in the I-058 hitch data at 50.35 ms |
| `zzz_rax_sortingplus_mcm.script:112`, directly in `actor_on_first_update` at :108 | *110- SortingPlus - RavenAscendant* | **first update** |
| `rax_dynamic_custom_functor.script:40`, in `add_functor_now` | *110- SortingPlus* | lazy, on demand |
| `custom_functor_autoinject.script:511/522/530` | *FDDA Redone* | lazy, same file |
| `zz_ui_inventory_better_stats_bars.script:1614`, in `actor_on_before_hit` | *G.A.M.M.A. Keybinds fixes* | on first hit |

So GAMMA already prewarms the inventory object behind the loading screen, and
has done since long before this mod. Deliverable (a) of I-063 was solving a
problem the modpack had already solved — which is worth knowing, and is exactly
why the log line that says "nothing to do" was worth printing.

### Why an inventory open is bimodal 4-20 ms

With `GUI` pre-built, `start()` never runs `UIInventory()`. The whole cost is
`IMode_Init()` → `Reset()` + `IMode_ResetInventories()` + `UpdateInfo(true)`,
and `IMode_ResetInventories` is one line that matters:

```lua
self.CC["actor_bag"]:Reinit( self:ParseInventory(db.actor) )
```

`UICellContainer:Reinit` (live copy: *G.A.M.M.A. Guns Have No Condition*'s
`utils_ui.script`) does `self:Reset()`, then `spairs(t, sort_order)` — which on
GAMMA is the `hspairs` min-heap from `_g_patches.script`, not `table.sort` — and
then `AddItem` per item. Per item that is:

* `SYS_GetParam(0, sec, "kind")` in `ParseInventory`, plus
  `SYS_GetParam(2, sec, "inv_grid_width")` and `..."inv_grid_height"` in
  `FindFreeCell` — **three uncached ltx reads**, because GAMMA's Modded Exes
  `_g_patches.script` leaves `SYS_GetParam` uncached;
* `FindSimilar` (a hash lookup when `stack_all` is on, which
  `enable_item_picker` sets for `actor_bag`), else
* `FindFreeCell`, which scans `for r = rKind.row, #self.grid do for c = 1, cols do
  IsFreeRoom(r, c, w, h)` — restarting from `rKind.row` for **every** item, so
  placement is O(N · rows · cols · w · h), and calls `Grow()` + recurses when the
  grid fills;
* `AddItemInCell`, which constructs a `UICellItem` **only** `if (not self.cell[indx])`,
  and each new one runs four `xml:InitStatic` calls in `InitControls`;
* `self.cell[indx]:Set(obj, area)`, which is where the icon texture is bound.

The load-bearing detail: **`UICellContainer:Reset()` never removes a cell.** It
calls `ci:Reset()` on each one and clears the index tables, so `self.cell` and
`self.grid` are *high-water marks that only grow*, and they live as long as the
`GUI` object — the whole session, until a level change nils it.

That gives exactly two modes:

* **cheap (~4-6 ms)** — the pool and the grid already cover this ruck, so the
  open is `Reset` + a sort + N grid scans + N `Set`s and no construction;
* **expensive (15-20 ms, once 37.4)** — `indx` passes the high-water mark, so new
  `UICellItem`s get built (4 `InitStatic` each) and/or `Grow()` extends the grid,
  and every later scan is over a bigger grid.

The high-water mark rises whenever the ruck gains distinct stacks — which is
what looting does. That explains the first open usually being worst (empty
pool), *and* baseline capture 3's 8.30 first / 19.26 later (the player looted in
between), *and* the per-window series 6.4, 37.4, 15.2, 6.8, 20.1, 6.0, 4.0, 4.2:
a ~4-6 ms floor with spikes on the growth opens. GC is not needed to explain any
of it, and neither is the icon cache — though both would ride along on the same
opens.

### The cut this points at, and what it needs first

Three candidates, cheapest first:

1. **Memoise the three per-item `SYS_GetParam` reads.** `kind`,
   `inv_grid_width` and `inv_grid_height` are static per section and are read
   uncached on every item of every open. A section-keyed table removes 3N
   uncached ltx crossings per open. Reachable as a monkey patch on the
   `UIInventory` / `UICellContainer` class tables — no file replaced. Helps both
   modes, proportional to N, does not touch the spikes.
2. **Prewarm the cell pool, not the object.** Behind the loading screen, after
   whoever built `GUI`, run the `Reinit` once against the actor's current ruck so
   the pool and grid are sized before the first open. Moves the first-open spike
   only; later growth after looting still costs. Non-identity to check first:
   `AddItem` fires `Callback("On_CC_Add", ...)`, which other mods subscribe to.
3. **Fix the quadratic placement.** `FindFreeCell` restarts at `rKind.row` for
   every item; a per-row first-free-column cursor, or resuming from the last
   successful position, makes placement near-linear. Biggest win, but it is a
   change to `utils_ui.script` — 13 mods deep, and the live winner is *Guns Have
   No Condition* — so it is a byte-asserting patcher, not a monkey patch.

**None of this should be built before it is measured**, because the hypothesis is
a correlation and the hitch profiler cannot show a correlation: it keeps only
max, first and a log2 histogram per listener, so it cannot say which open was
expensive or what the pool looked like at the time. That is what
`alao-profiler-hitch-trace-inv` is for (below): one line per inventory open with
`cells` / `grid` / `idxer` sampled immediately before and after the call. If the
expensive opens are the ones where `cells` or `grid` grows, candidates 2 and 3
are the right targets and 1 is a rounding error; if they are not, the
explanation above is wrong and the next suspect is the icon texture bind in
`UICellItem:Set`.

## What could not be verified offline (and how it turned out)

- **That the engine's sound cost is path-keyed and survives with the object
  merely held** — the biggest assumption. **Confirmed in game**, see above.
- **The size of each win.** These are engine-side costs and there is no honest
  microbenchmark for them; no lupa timing is quoted anywhere in this mod or its
  tests. What the tests count is what *moves*: one `UIInventory()`, one
  `pda_encyclopedia_tab()` and ~160 `sound_object()` constructions, out of play
  and into the loading screen. The milliseconds come from the profiler.
- Whether the loading screen is still being drawn at frame 7 on every install.
  It was in all four captures of `20260920-125528-I-058-f5edb8`, where
  `actor_on_first_update` itself costs 1272 ms at frame 7 and the key prompt is
  at frame 62.

## The rest of the cold-signature sweep, and what was left out

Over all four I-058 captures, every callback/listener whose worst single call
reached 3 ms, excluding the ones that run behind the loading screen by
construction (`actor_on_first_update`, `load_state`, `on_game_load`,
`on_loading_screen_key_prompt`, `on_option_change`). Maxes are per capture.

| listener | maxes, ms | diagnosis | in this mod? |
|---|---|---|---|
| `actor_on_update#lam2.script:271` | 54.1 / 10.6 / 10.2 / 9.2 (and 25.6 / 9.2 / 9.0 / 9.8 in the I-063 run, unchanged by this mod as expected) | FDDA Redone's action machine. `go_to_next_action` -> `set_current_action` -> `get_template_action_play_animation`'s `enter`, which does `game.get_motion_length(sec, anm, speed)`, `game.play_hud_motion(...)`, `level.add_cam_effector(ini_sys:r_string_ex(sec,"cam"), 2190, ...)` and `sound_object(ini_sys:r_string_ex(sec,"snd"))`. That is the engine loading a HUD model, a motion set, a camera `.anm` and a sound the first time you use a given item section. Registered on `actor_on_update` only while a sequence runs (6-8% of frames). | **no** - see below |
| `npc_on_update#aaaa_script_fixes_mp.script:741` | 6.3 / 5.1 / 6.1 / 5.2 | not cold: 23 calls above the floor out of 87k, in every capture. A periodic sweep, not a first-call. Needs its own idea. | no |
| `actor_on_info_callback#info_portions.script:58` | 2.0 / 4.5 / 5.4 / 2.9 | `if (info == "ui_pda") then pda.calculate_rankings() end`, i.e. every PDA open recomputes the rankings. Genuinely recomputed, not lazy-built; caching it would make the rankings stale. Not a prewarm. | no |
| `on_key_release#ui_hud_dotmarks.script:5924` | 0.2 / 0.2 / 7.3 / - | `do_use_release_action_manually` -> `use_obj_by_id` / `xr_effects.force_talk`, i.e. the first time a *different* UI singleton gets built by an interaction. Same family; whichever singleton it is, it is not one of the three here. | no |
| `actor_on_update#sound_ambient.script:277` | 9.2 / 2.7 / 2.2 / 2.5 | cold in every capture (`first_ms == max_ms`), and it is the ambient-track sound objects. Prewarmable in principle, but the path set is level- and weather-dependent rather than a fixed table, so it needs a different mechanism. | no |
| `actor_on_update#logic_enforcer.script:56` | 5.2 once | a one-shot; fires once per session at an arbitrary frame. | no |
| `actor_on_update#drx_da_main.script:1656` / `:1665` | 21.1 / 20.8 | one call each, at frame 7 - already behind the loading screen. | n/a |
| `actor_on_update#demonized_ledge_grabbing.script:443` | 3.4 / 0.6 | I-057's territory. | no |

### lam2.script:271 in more detail, and a proposed cut

The 54 ms frame in capture 1 (frame 13033) and the 9-10 ms ones in the other
three are the same event, not an outlier: it is the **first FDDA-animated use of
a given item section in the session**. Capture 1 was the first game launch of
the sitting, so the `.ogf` / `.omf` / `.ogg` were cold in the OS file cache too;
captures 2-4 launched minutes later and hit that cache, which is exactly the
same 5x that the footstep row shows (4.5 / 2.7 / 0.9 / 0.8). So the rate is
"once per distinct consumable you use, per session", which for a normal play
session is several times, at 9-54 ms each.

What could be prewarmed is only the part with no visible effect:

  * `sound_object(ini_sys:r_string_ex(sec, "snd"))` for every animated section -
    safe, same mechanism as the footstep queue here.
  * `game.get_motion_length(sec, anm, speed)` - a query, and plausibly what
    forces the motion set to load. Unverified: it may or may not touch the
    resource, and it cannot be checked offline.

What cannot: `game.play_hud_motion` draws, and `level.add_cam_effector` moves
the camera. So a full prewarm is out; the honest options are (a) prewarm the
sound and the motion length only, which leaves the cam effector and the hud
visual cold, or (b) time-slice nothing and instead have FDDA warm the *next*
likely section in the background while the current animation plays, which is a
change to someone else's state machine. Neither is in this mod. The section set
also has to come from somewhere - it is driven by which item the player uses -
so (a) needs an enumeration pass over the FDDA config first.

## The instrument the inventory question needs

`lab/profiler-hitch` gained a `TRACE_LISTENERS` flag (default `nil`, so the
shipped behaviour and the two locked overlays are unchanged): name a listener,
or a prefix of its label, and every call of it emits

```
ALAOPROF|1|trace|n=..|name=..|units=..|frame=..|t=..|pre=..|post=..
```

`pre` and `post` are cheap covariates sampled immediately before and after the
call, **outside the timer**, so the trace cannot inflate the duration it
reports. The probe for this question returns
`cells=<#CC["actor_bag"].cell>,grid=<#grid>,idxer=<idxer>`, and returns `"na"`
rather than erroring if `ui_inventory.GUI` is not there. `TRACE_MAX_LINES`
(400) caps the output so a mis-aimed pattern cannot flood the engine log.

`py -3.12 lab/tools/i063_build_trace_overlay.py` builds exactly one new overlay,
`alao-profiler-hitch-trace-inv` (listener mode + I-051 invalidate + the trace
pointed at `ActorMenu_on_before_init_mode#ui_inventory.script`). It refuses to
write any of the five existing `alao-profiler*` overlays — those carry every
locked gen-3/gen-4/gen-5 measurement — and unlike `i058_build_overlays.py` it
never rebuilds them. `aalo/profiler.py` ignores `kind` values it does not know,
so a traced log parses exactly as before; read the trace lines by grepping.

## Tests

`lab/tests/test_i063_prewarm.py` (18), stub engine under `lupa.luajit20`
(`lab/tests/i063_prewarm_harness.py`): differential arms with and without the
mod over the real `eft_jump_sounds.script` and `footstep_sounds.script`, the
GUI-nil assumption, the twelve-listener non-identity pinned, "the whole queue is
built inside first update", "no `actor_on_update` listener is ever registered",
"every construction lands on frame 7", the pcall containment of a bad path, the
level-change rebuild, and one test that the `SLICE_SOUNDS` escape hatch still
works when it is asked for.
