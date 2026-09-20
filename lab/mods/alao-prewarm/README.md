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
| `ActorMenu_on_before_init_mode#ui_inventory.script:93` | 11.4 | 10.1 | 18.5 | 8.4 | **the worst call was the first inventory open in all four captures**; every later open 3.2-6.4 ms |
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
to nil by `UIInventory:actor_on_net_destroy`, i.e. on a level change. So the
cost is once per level load and it lands on the frame you pressed I.

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
| `ui_inventory.script:93` | `UIInventory()`: xml parse + widget tree + 6 cell containers | `Reset()`, `IMode_Init`, `ParseInventory` over the actor's items, `ShowDialog` | mostly engine (xml, widget ctors); the Lua part is the ~600 lines of `InitControls` |
| `ui_pda_encyclopedia_tab.script:407` | `pda_encyclopedia_tab()` via `get_ui()` | `InitCategories()` + `SelectCategory` + `SelectArticle` per unlock, `size_table(locked_articles)` twice per `unlock_article`, `actor_menu.set_notification` | engine (xml, list widgets, `game.translate_string`) |
| `eft_jump_sounds.script:71/:89` | the engine's load of each `jump\jump_*` / `landing\landing_*` path | 5 geometry raycasts, one ray object per ray, `oleh_sound_utils.get_outfit_class()` | engine both ways |
| `footstep_sounds.script:87` | the engine's load of each `footstep\n_*`, `cloth\*`, `gear_rattle\*`, `ladder\*` path | queue shuffle, `db.actor:get_total_weight()`, and on ladders 4 more raycasts | engine |

## What the mod does

At `actor_on_first_update` — frame 7 of the level load, with the loading screen
still up (`on_loading_screen_key_prompt` is at frame ~62 in every capture, so
there are ~55 frames of screen left):

1. `ui_inventory.GUI = ui_inventory.UIInventory()` if `GUI` is nil.
2. `ui_pda_encyclopedia_tab.get_ui()`.
3. Queue every sound path the three sound scripts can construct, and build them
   a few per frame on `actor_on_update` with a 0.5 ms/frame construction budget,
   unregistering when the queue drains.

Steps 1 and 2 are single indivisible constructions (8-18 ms and 6-9 ms), so they
are not sliced: behind the loading screen is the only place they are allowed to
run. Step 3 is ~160 independent items, so it is sliced; on this install it
drains long before the key prompt, but the budget means that if it did not, it
still never costs a visible frame. Every construction is `pcall`ed, so a path
that does not exist on someone's install costs one failed call and is counted.

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

## What could not be verified offline

- **That the engine's sound cost is path-keyed and survives with the object
  merely held.** Lua gives no way to ask the sound manager. The evidence is the
  profile (fresh object every call, yet the cost decays with the path), and the
  in-game A/B decides it. If it turns out the cost is per-`play` rather than
  per-construction, step 3 does nothing and steps 1 and 2 are unaffected.
- **The size of each win.** These are engine-side costs and there is no honest
  microbenchmark for them; no lupa timing is quoted anywhere in this mod or its
  tests. What is countable is what *moves*: one `UIInventory()`, one
  `pda_encyclopedia_tab()` and ~160 `sound_object()` constructions, out of play
  and into the loading screen.
- Whether the loading screen is still being drawn at frame 7 on every install.
  It was in all four captures of `20260920-125528-I-058-f5edb8`, where
  `actor_on_first_update` itself costs 1272 ms at frame 7 and the key prompt is
  at frame 62.

## Tests

`lab/tests/test_i063_prewarm.py`, stub engine under `lupa.luajit20`
(`lab/tests/i063_prewarm_harness.py`): differential arms with and without the
mod over the real `eft_jump_sounds.script` and `footstep_sounds.script`, the
GUI-nil assumption, the time-slice budget, the pcall containment of a bad path,
and the level-change rebuild.
