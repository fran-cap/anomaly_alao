# I-041 — vanilla per-frame census

Investigation, not a transform. Question behind it: the only measured in-game gain so far
(+2.6% avg, suggestive) came from the 111 rewritten vanilla scripts at the bottom of the GAMMA
load order, so what is actually in the vanilla per-frame bodies, what does ALAO already do to
them, and what should it do next?

Corpus: `extracted/vanilla_db` (Anomaly 1.5.3 db scripts, 413 unique `.script` files; the tree
holds two copies of each, everything below is deduped). Analyzer at commit `bae4b0c`.
Shadowing computed against the live G.A.M.M.A. profile (577 enabled mods, read-only).

## 1. Headline numbers

| | count |
|---|---:|
| vanilla scripts | 413 |
| live in GAMMA (no enabled mod ships the same name) | 317 |
| shadowed by a mod | 96 |
| per-frame bodies found by the I-010/I-013 classifier | 157 |
| ... interpreted / mixed / compiled | 99 / 14 / 44 |
| ... live in GAMMA | 103 (57 interp, 11 mixed, 35 compiled) |
| live per-frame bodies with **zero** actionable ALAO findings | **74 of 103** |
| GREEN findings in the whole vanilla corpus | 809 |
| ... that land inside any per-frame body | **17** |
| ... inside a per-frame body that is live *and* shipped in the overlay | **10** |

That last row is the finding of this investigation. ALAO rewrote 176 vanilla files and applied
2261 edits, of which **ten** touched code that runs per frame in the profile that was measured.

## 2. Where the time goes instead: trace aborts

Abort-site counts inside the 103 **live** per-frame bodies (I-013 classifier):

| abort reason | sites |
|---|---:|
| `time_global()` (engine C function) | 53 |
| `:id()` | 31 |
| `BC_CAT` (string concatenation) | 30 |
| `:name()` | 27 |
| `:position()` | 25 |
| `pairs` | 25 |
| `game.translate_string` | 20 |
| `:alive()` | 11 |
| `:set()` | 10 |
| `:object()` | 9 |
| `game.get_game_time` | 8 |
| `device` | 6 |
| `vector` | 6 |
| `:distance_to()` | 6 |

Static call sites of interest inside live per-frame bodies: `time_global`/`device` 60,
engine getters (`:id` `:name` `:position` `:section` `:alive` `:object`) 117, `pairs`/`ipairs` 25,
`vector()` 6, `distance_to*` 8. Fifteen live bodies call `time_global()` at least twice;
twelve repeat the *same* engine getter on the same receiver at least twice.

## 3. The frame chain in the standing-still gammabaseline save

Confirmed by reading the code, not by profiling:

```
engine -> actor_binder:update            (bind_stalker.script, LIVE, interpreted, 1/frame)
            task_manager.get_task_manager():update()
            level_weathers.get_weather_manager():update()      LIVE, interpreted, 2x time_global
            xr_sound.update(actor id)                          LIVE, compiled
            itms_manager.get_item_processor():update()         SHADOWED by a mod
            sr_psy_antenna.psy_antenna:update()                SHADOWED
            bind_stalker_ext.actor_on_update()                 LIVE, interpreted
              -> SendScriptCallback("actor_on_update") -> axr_main.make_callback  (pairs loop)
                   -> every registered actor_on_update: bind_stalker_ext, logic_enforcer,
                      release_npc_inventory, txr_mines, ui_pda_npc_tab, warfare, ...

engine -> motivator_binder:update        (xr_motivator.script, LIVE, interpreted, 1/frame/NPC)
            xr_sound.update(id)
            xr_logic.try_switch_to_another_section    LIVE, interpreted, 7 abort sites
            xr_combat.set_combat_type                 LIVE, interpreted
            SendScriptCallback("npc_on_update") -> make_callback
                 -> xr_weapon_jam, xr_bribe, gameplay_silent_kills   (all LIVE, interpreted)
            scheme update: state_mgr:update / move_mgr:update / xr_animpoint / sr_* ...

engine -> generic_object_binder:update   (bind_monster.script, LIVE, interpreted, 1/frame/monster)
            SendScriptCallback("monster_on_update") -> make_callback
```

Everything named `UI*:Update` (about half of the live bodies: `utils_ui`, `ui_inventory`,
`ui_workshop`, `ui_pda_*`, `ui_debug_*`, `item_*` UI classes) runs only while that window is
open, so none of it executes in a standing-still capture. Excluding those leaves roughly
**35 live per-frame bodies that plausibly run in the gammabaseline save**, and only about a
dozen of them run per-object rather than once per frame.

## 4. The classifier's blind spot: per-frame callees

The I-010/I-013 classifier only recognises a body as per-frame when its *name* is an engine
callback or an `update`/`Update` method. Every function those bodies call is invisible to it,
and that is where the hot vanilla code actually lives. One-hop expansion from the live
per-frame bodies (mode from the same classifier):

| callee | live? | jit_mode | abort sites | top reasons |
|---|---|---|---:|---|
| `axr_main.make_callback` | live | mixed | 1 | `pairs` — runs once per SendScriptCallback, i.e. per NPC per frame |
| `xr_logic.try_switch_to_another_section` | live | interpreted | 7 | `level.object_by_id`, `:id()`, `:name()` |
| `xr_logic.pick_section_from_condlist` | live | interpreted | 21 | `:has_info()`, `:name()`, `:disable_info_portion()` |
| `xr_logic.determine_section_to_activate` | live | interpreted | 5 | `:id()`, `:name()` |
| `xr_logic.issue_event` | live | interpreted | 1 | `pairs` |
| `xr_logic.switch_to_section` | live | interpreted | 1 | `:id()` |
| `xr_combat.set_combat_type` | live | interpreted | 2 | `:best_enemy()`, `:id()` |
| `state_mgr.set_state` | live | interpreted | 1 | `:id()` |
| `bind_stalker_ext.actor_on_update` | live | interpreted | 3 | `:alive()`, `:has_info()`, `time_global` |
| `trade_manager.update` | live | interpreted | 5 | `game.get_game_time`, `:id()`, `:name()` |
| `utils_obj.stalker_at_waypoint` | live | interpreted | 2 | `:distance_to_sqr()`, `:position()` |
| `xr_gulag.get_npc_smart` | live | interpreted | 4 | `:object()`, `:id()`, `alife` |
| `ph_door.try_to_open_door` / `try_to_close_door` | live | interpreted | 3 / 4 | `pairs`, `:distance_to_sqr()` |
| `xr_sound.update`, `xr_sound.set_sound_play`, `xr_logic.mob_release`, `xr_logic.mob_capture` | live | compiled | 0 | — |

`axr_main.make_callback` is the single hottest Lua function in the game: a `pairs` walk with a
`type()` test and a table index per registered listener, executed for every
`SendScriptCallback`, which includes `npc_on_update` for every online NPC every frame. It is
live, it aborts its trace on `pairs`, and **ALAO produces no GREEN finding for it at all**
(its only findings are `global_write` x7 and `debug_statement` x5).

## 5. What `--fix` actually did to the top bodies

Re-ran plain `--fix` on a fresh copy of the vanilla scripts at `bae4b0c` (176 files modified,
2261 edits, matching run `20260911-151002-vanilla-i019`). Inside the top-of-chain bodies:

| body | what `--fix` changed inside it |
|---|---|
| `bind_stalker.script actor_binder:update` | **nothing** (file not modified at all) |
| `xr_motivator.script motivator_binder:update` | one edit: `local actor = db.actor` hoisted, replacing 4 `db.actor` lookups |
| `bind_monster.script generic_object_binder:update` | one edit: `pos:distance_to(...) > 10` becomes `distance_to_sqr(...) > 100` |
| `level_weathers.script WeatherManager:update` | nothing (the 8 GREEN edits are all in `select_weather`, a cold path) |
| `move_mgr.script move_mgr:update` | nothing (edits are in `waypoint_callback` and `reset`) |
| `xr_eat_medkit.script eat_medkit:update` | nothing (5 GREEN edits elsewhere in the file) |
| `xr_logic.script` | 15 GREEN edits, none inside `try_switch_to_another_section` / `pick_section_from_condlist`; the biggest is an `:id()` hoist in `determine_section_to_activate` and a `string.sub` alias in `parse_infop` |
| `warfare.script actor_on_update` | 41 GREEN in the file, one `table_insert_append` inside the body; warfare is off in a default GAMMA profile |

Corpus gates for that run were clean: 0 compile failures, 0 idempotence violations, 0 timeouts,
1 pre-existing parse failure (`lua_help.script`, not valid Lua 5.1).

### Is +2.6% plausible from these diffs?

**No.** Ten GREEN rewrites inside live per-frame bodies, the largest of which converts four
`db.actor` table lookups into one, cannot move the average frame time by 2.6%. The measured
delta is either noise (the locked stock-to-full-ALAO spread is 209.7 to 215.2 fps, the same
order as the run-to-run window in the baseline report) or it comes from something outside the
per-frame bodies: 2261 edits also changed module-load code, callback handlers fired by events,
and `string_find_plain` / `table_insert_append` sites in helpers. There is no idempotence
violation in `xr_logic` at this commit (I-019 closed all 8), so that hypothesis is dead.
Treat +2.6% as unexplained and do not build on it.

## 6. Ranked follow-up rewrites

Ranked by frequency in the standing-still save multiplied by work removed per invocation. None
of these are measured; each is a hypothesis with a named owner pattern.

1. **Array-backed callback dispatch in `axr_main.make_callback`** (new idea; relates to I-005).
   `pairs` over a mixed function/userdata-keyed table, per listener, per SendScriptCallback,
   per NPC, per frame. A parallel array of listeners plus a numeric `for` removes the hash walk
   and lets the dispatcher compile. Not mechanically derivable by the existing `pairs`-to-`ipairs`
   rule (keys are functions and userdata), so it needs its own pattern or a hand patch.
   Highest frequency of anything in this census.
2. **I-040 `time_global()` caching**: 53 abort sites in live per-frame bodies, 15 bodies call it
   twice or more. Per-body caching is the safe version; a per-frame global stamped once in
   `actor_binder:update` would remove nearly all 53 but changes semantics. Biggest single
   trace-abort reason in the census.
3. **I-021 engine-call hoisting**: 117 static engine-getter sites in live per-frame bodies,
   12 bodies repeat the same getter on the same receiver. ALAO's `repeated_*` family already
   does this for a hard-coded list of receiver names; the census says the list is the limit,
   not the opportunity. `:id()` 31, `:name()` 27, `:position()` 25 are the three to generalise.
4. **Extend the per-frame classifier to callees** (new idea). One hop from the per-frame bodies
   reaches `xr_logic.pick_section_from_condlist` (21 abort sites), `try_switch_to_another_section`
   (7), `determine_section_to_activate` (5), `trade_manager.update` (5). All live, all
   interpreted, all invisible to every mode-gated decision ALAO makes today. This is the
   prerequisite for items 2 and 3 actually landing where it matters.
5. **I-005 `pairs`-to-`ipairs`**: 25 sites in live per-frame bodies, concentrated in
   `warfare.script` (4, off by default), `xr_animpoint` (3), `logic_enforcer` (2),
   `sound_manager` (2), `sr_teleport` (2). Real but small, and the beam already notes it is
   0.29x interpreted on most per-frame sites, so gate it on the classifier.
6. **String concatenation / `BC_CAT`**: 30 sites in live per-frame bodies, but essentially all
   of them are arguments to `printf` / `printd` / `print_dbg` debug calls that plain `--fix`
   leaves in place. The cheap win here is not a concat rewrite, it is `--fix-debug`: 34
   `debug_statement` findings sit inside live per-frame bodies, each one a call plus a
   concatenation plus sometimes a `game.translate_string` on the hot path. Worth an FPS arm of
   its own.
7. **I-012 `distance_to` to `distance_to_sqr`**: 6 to 8 sites in live per-frame bodies
   (`bind_monster`, `sr_monster`, `gameplay_silent_kills`, `txr_mines`, `move_mgr`). Already
   fires and already fixes; nothing more to gain in vanilla.
8. **I-009 vector reuse**: 6 `vector()` sites in live per-frame bodies
   (`bind_dynamo_hand` 2, `sr_monster` 2, `heli_combat` 1, `ph_oscillate` 1). Thin in vanilla.

## 7. Full per-frame body table

`live?` is against the G.A.M.M.A. modlist; `in overlay?` is whether the file was one of the 111
taken into `lab/coord/overlays/vanilla-db-bottom`.

| file | body | line | size | jit_mode | aborts | live? | in overlay? | findings inside |
|---|---|---:|---:|---|---:|---|---|---|
| actor_status.script | `UIIndicators:Update` | 368 | 67 | interpreted | 2 | shadowed | no | unnecessary_else x1 |
| actor_status_sleep.script | `actor_on_update` | 159 | 38 | interpreted | 2 | shadowed | no | - |
| actor_status_thirst.script | `actor_on_update` | 165 | 38 | interpreted | 2 | shadowed | no | - |
| arszi_psy.script | `actor_on_update` | 64 | 22 | interpreted | 1 | shadowed | no | - |
| axr_companions.script | `UIWheelCompanion:Update` | 1731 | 14 | interpreted | 2 | shadowed | no | - |
| axr_companions.script | `UICompanionList:Update` | 1907 | 59 | interpreted | 10 | shadowed | no | - |
| bind_anomaly_field.script | `anomaly_field_binder:update` | 609 | 10 | compiled | 0 | shadowed | no | - |
| bind_anomaly_zone.script | `anomaly_zone_binder:update` | 497 | 3 | compiled | 0 | live | yes | - |
| bind_camp.script | `camp_binder:update` | 46 | 6 | interpreted | 1 | live | no | - |
| bind_campfire.script | `campfire_binder:update` | 312 | 3 | compiled | 0 | shadowed | no | - |
| bind_car.script | `car_binder:update` | 23 | 14 | interpreted | 1 | live | yes | - |
| bind_container.script | `container_binder:update` | 91 | 10 | interpreted | 3 | live | no | - |
| bind_crow.script | `crow_binder:update` | 22 | 47 | interpreted | 14 | shadowed | no | debug_statement x1 |
| bind_door_labx8.script | `door_binder_labx8:update` | 101 | 32 | compiled | 0 | live | no | - |
| bind_dynamic_light.script | `generic_light_binder:update` | 91 | 52 | interpreted | 13 | live | yes | uncached_globals_summary x1 |
| bind_dynamo_hand.script | `dynamo_hand_binder:update` | 85 | 44 | interpreted | 10 | live | yes | repeated_device x1, potential_nil_access x1 |
| bind_faction.script | `faction_binder:update` | 21 | 4 | compiled | 0 | live | no | - |
| bind_heli.script | `heli_binder:update` | 80 | 30 | interpreted | 3 | live | yes | - |
| bind_item.script | `item_binder:update` | 152 | 43 | interpreted | 7 | live | no | - |
| bind_level_changer.script | `lchanger_binder:update` | 19 | 4 | compiled | 0 | live | no | - |
| bind_monster.script | `generic_object_binder:update` | 49 | 99 | interpreted | 21 | live | yes | potential_nil_access x2, unnecessary_else x1, distance_to_comparison x1 |
| bind_physic_object.script | `generic_physics_binder:update` | 45 | 18 | interpreted | 1 | live | yes | - |
| bind_red_forest_bridge.script | `bridge_binder:update` | 80 | 26 | mixed | 1 | live | no | debug_statement x1 |
| bind_restrictor.script | `restrictor_binder:update` | 87 | 11 | interpreted | 1 | live | yes | - |
| bind_signal_light.script | `signal_light_binder:update` | 26 | 48 | interpreted | 3 | live | no | - |
| bind_smart_cover.script | `smart_cover_binder:update` | 39 | 3 | compiled | 0 | live | no | - |
| bind_smart_terrain.script | `smart_terrain_binder:update` | 57 | 6 | compiled | 0 | live | no | - |
| bind_stalker.script | `actor_binder:update` | 207 | 106 | interpreted | 16 | live | no | global_write x3, debug_statement x2 |
| bind_stalker_ext.script | `actor_on_update` | 80 | 26 | interpreted | 3 | live | yes | repeated_db_actor x1 |
| bind_trader.script | `trader_object_binder:update` | 33 | 17 | interpreted | 2 | live | yes | - |
| dynamic_news_manager.script | `actor_on_update` | 95 | 19 | interpreted | 4 | shadowed | no | - |
| gameplay_disguise.script | `hud_update` | 1074 | 50 | interpreted | 3 | shadowed | no | unnecessary_else x1 |
| gameplay_radioactive_water.script | `actor_on_update` | 14 | 11 | compiled | 0 | live | yes | - |
| gameplay_silent_kills.script | `npc_on_update` | 125 | 36 | interpreted | 11 | live | yes | potential_nil_access x2 |
| gwr_worldweapon_binder.script | `gwr_wpn_m98_binder:update` | 11 | 19 | interpreted | 7 | live | no | global_write x1 |
| heli_combat.script | `heli_combat:update` | 490 | 83 | interpreted | 5 | live | yes | - |
| heli_move.script | `heli_move:update` | 149 | 43 | mixed | 2 | live | yes | - |
| item_artefact.script | `UIBelt:Update` | 323 | 12 | interpreted | 1 | shadowed | no | unnecessary_else x1 |
| item_artefact.script | `artefact_binder:update` | 350 | 113 | interpreted | 8 | shadowed | no | potential_nil_access x2, repeated_db_actor x1 |
| item_backpack.script | `UICreateStash:Update` | 205 | 3 | compiled | 0 | shadowed | no | - |
| item_cooking.script | `UICook:Update` | 219 | 55 | interpreted | 2 | shadowed | no | unused_local_variable x1 |
| item_device.script | `device_binder:update` | 680 | 85 | interpreted | 6 | shadowed | no | potential_nil_access x1 |
| item_radio.script | `UI3D_RF:Update` | 305 | 56 | interpreted | 7 | shadowed | no | - |
| item_recipe.script | `UIRecipe:Update` | 281 | 20 | compiled | 0 | shadowed | no | - |
| item_repair.script | `UIRepair:Update` | 288 | 31 | interpreted | 3 | live | yes | - |
| item_weapon.script | `UIWheelAmmo:Update` | 656 | 15 | compiled | 0 | shadowed | no | - |
| itms_manager.script | `ItemProcessor:update` | 1167 | 86 | interpreted | 9 | shadowed | no | debug_statement x6 |
| level_weathers.script | `WeatherManager:update` | 209 | 32 | interpreted | 6 | live | yes | - |
| logic_enforcer.script | `actor_on_update` | 56 | 48 | interpreted | 8 | live | no | potential_nil_access x2 |
| mob_camp.script | `mob_camp:update` | 84 | 23 | interpreted | 3 | live | yes | - |
| mob_home.script | `mob_home:update` | 53 | 3 | compiled | 0 | live | no | - |
| mob_jump.script | `mob_jump:update` | 47 | 27 | compiled | 0 | live | no | - |
| mob_remark.script | `mob_remark:update` | 101 | 45 | compiled | 0 | live | no | debug_statement x1 |
| mob_trade.script | `mob_trade:update` | 73 | 2 | compiled | 0 | live | yes | - |
| mob_trader.script | `mob_trader:update` | 98 | 5 | compiled | 0 | live | no | - |
| mob_walker.script | `mob_walker:update` | 72 | 32 | compiled | 0 | live | no | unused_local_variable x1 |
| move_mgr.script | `move_mgr:update` | 329 | 40 | interpreted | 3 | live | yes | - |
| ph_appforce.script | `ph_force:update` | 21 | 20 | interpreted | 4 | live | no | - |
| ph_button.script | `ph_button:update` | 19 | 5 | compiled | 0 | live | no | - |
| ph_car.script | `action_car:update` | 942 | 14 | compiled | 0 | live | yes | - |
| ph_code.script | `codepad:update` | 22 | 2 | compiled | 0 | live | no | - |
| ph_death.script | `ph_on_death:update` | 17 | 2 | compiled | 0 | live | no | - |
| ph_door.script | `action_door:update` | 63 | 10 | mixed | 1 | live | no | debug_statement x1 |
| ph_hit.script | `action_hit:update` | 37 | 17 | interpreted | 1 | live | no | - |
| ph_idle.script | `action_idle:update` | 17 | 13 | compiled | 0 | live | no | - |
| ph_minigun.script | `action_mgun:update` | 246 | 51 | mixed | 3 | live | no | debug_statement x1 |
| ph_on_hit.script | `ph_on_hit:update` | 16 | 2 | compiled | 0 | live | no | - |
| ph_oscillate.script | `action_oscillator:update` | 30 | 23 | interpreted | 3 | live | no | - |
| ph_sound.script | `snd_source:update` | 47 | 74 | interpreted | 6 | shadowed | no | - |
| phantom_manager.script | `Phantom:update` | 123 | 3 | compiled | 0 | live | yes | - |
| psi_storm_manager.script | `CPsiStormManager:update` | 249 | 127 | interpreted | 12 | shadowed | no | unused_local_variable x1 |
| release_npc_inventory.script | `actor_on_update` | 17 | 47 | interpreted | 6 | live | no | potential_nil_access x3, unused_local_variable x1 |
| se_monster.script | `se_monster:update` | 111 | 4 | compiled | 0 | live | no | - |
| se_smart_cover.script | `se_smart_cover:update` | 102 | 3 | compiled | 0 | live | no | - |
| se_stalker.script | `se_stalker:update` | 143 | 4 | compiled | 0 | live | no | - |
| se_zones.script | `se_zone_anom:update` | 11 | 3 | compiled | 0 | live | no | - |
| se_zones.script | `se_zone_torrid:update` | 36 | 3 | compiled | 0 | live | no | - |
| se_zones.script | `se_zone_visual:update` | 62 | 3 | compiled | 0 | live | no | - |
| sim_squad_scripted.script | `sim_squad_scripted:update` | 144 | 106 | interpreted | 5 | shadowed | no | redundant_not_eq x3, potential_nil_access x2, unused_local_variable x2 |
| sim_squad_warfare.script | `squad_on_update` | 52 | 99 | interpreted | 8 | live | no | debug_statement x6 |
| smart_terrain.script | `se_smart_terrain:update` | 1238 | 48 | interpreted | 6 | shadowed | no | potential_nil_access x1 |
| smart_terrain_warfare.script | `smart_terrain_on_update` | 141 | 89 | interpreted | 5 | live | yes | debug_statement x3 |
| sound_manager.script | `sound_manager:update` | 69 | 115 | interpreted | 5 | live | yes | - |
| sr_camp.script | `CCampManager:update` | 126 | 67 | interpreted | 4 | shadowed | no | unnecessary_else x1 |
| sr_crow_spawner.script | `crowkiller:update` | 22 | 16 | interpreted | 2 | live | no | - |
| sr_cutscene.script | `cam_effector_set:update` | 135 | 20 | interpreted | 1 | live | no | - |
| sr_cutscene.script | `action_cutscene:update` | 244 | 19 | compiled | 0 | live | no | unused_local_variable x1 |
| sr_deimos.script | `CDeimos:update` | 18 | 96 | interpreted | 19 | live | yes | repeated_db_actor x1 |
| sr_idle.script | `action_idle:update` | 17 | 7 | compiled | 0 | live | no | - |
| sr_light.script | `action_light:update` | 28 | 8 | compiled | 0 | shadowed | no | - |
| sr_monster.script | `fake_monster:update` | 24 | 72 | interpreted | 13 | live | yes | debug_statement x4, potential_nil_access x2, distance_to_comparison x1 |
| sr_no_weapon.script | `action_no_weapon:update` | 29 | 26 | interpreted | 2 | live | no | - |
| sr_particle.script | `action_particle:update` | 58 | 36 | interpreted | 1 | live | no | debug_statement x1, unnecessary_else x1 |
| sr_postprocess.script | `action_postprocess:update` | 69 | 32 | interpreted | 1 | live | no | - |
| sr_psy_antenna.script | `PsyAntenna:update` | 172 | 43 | interpreted | 2 | shadowed | no | - |
| sr_psy_antenna.script | `action_psy_antenna:update` | 329 | 8 | compiled | 0 | shadowed | no | - |
| sr_silence.script | `CSilence_zone:update` | 20 | 3 | compiled | 0 | live | no | - |
| sr_teleport.script | `action_teleport:update` | 18 | 49 | mixed | 8 | live | yes | debug_statement x1 |
| sr_timer.script | `action_timer:update` | 12 | 36 | interpreted | 4 | live | no | - |
| state_mgr.script | `state_manager:update` | 439 | 63 | mixed | 1 | live | no | - |
| surge_manager.script | `CSurgeManager:update` | 529 | 547 | interpreted | 38 | shadowed | no | repeated_db_actor x1, debug_statement x1, unnecessary_else x1, vector_alloc_in_loop x1 |
| task_manager.script | `CRandomTask:update` | 104 | 16 | interpreted | 2 | live | yes | - |
| tasks_defense.script | `npc_on_update` | 154 | 6 | interpreted | 2 | shadowed | no | potential_nil_access x1 |
| tasks_guide.script | `actor_on_update` | 223 | 12 | interpreted | 1 | shadowed | no | - |
| tasks_measure.script | `UI3D_Anomaly:Update` | 549 | 31 | interpreted | 4 | shadowed | no | - |
| txr_mines.script | `actor_on_update` | 161 | 9 | compiled | 0 | live | no | - |
| txr_mines.script | `generic_on_update` | 180 | 20 | interpreted | 11 | live | no | potential_nil_access x5, debug_statement x2 |
| ui_companion_inv.script | `UICompanionInv:Update` | 156 | 33 | interpreted | 9 | live | no | string_concat_in_loop x2 |
| ui_ctrl_lighting.script | `UILightControl:Update` | 131 | 15 | interpreted | 1 | live | no | - |
| ui_debug_item.script | `UIItemEditor:Update` | 1016 | 76 | interpreted | 9 | live | yes | - |
| ui_debug_launcher.script | `UIDebug_ItemSpawn:Update` | 1487 | 15 | interpreted | 1 | shadowed | no | - |
| ui_debug_launcher.script | `UIDebug_ObjSpawn:Update` | 2070 | 9 | interpreted | 1 | shadowed | no | - |
| ui_debug_launcher.script | `UIDebug_Executer:Update` | 2344 | 9 | interpreted | 1 | shadowed | no | - |
| ui_debug_lighting.script | `LightEditor:Update` | 233 | 9 | interpreted | 2 | live | yes | - |
| ui_debug_main.script | `debug_ui:Update` | 480 | 3 | compiled | 0 | live | yes | - |
| ui_debug_main.script | `anim_ui:Update` | 2241 | 3 | compiled | 0 | live | yes | - |
| ui_debug_weather.script | `WeatherEditor:Update` | 592 | 51 | interpreted | 5 | shadowed | no | - |
| ui_debug_wpn_hud.script | `WpnHudEditor:Update` | 596 | 20 | interpreted | 3 | live | yes | - |
| ui_dosimeter.script | `ui_dosimeter:Update` | 30 | 70 | interpreted | 12 | shadowed | no | unnecessary_else x1 |
| ui_dyn_msg_box.script | `context_menu:Update` | 259 | 20 | interpreted | 1 | live | no | - |
| ui_dyn_msg_box.script | `context_props:Update` | 405 | 33 | mixed | 4 | live | no | - |
| ui_inventory.script | `UIInventory:Update` | 3501 | 74 | interpreted | 7 | shadowed | no | unnecessary_else x1 |
| ui_itm_details.script | `UIItemSheet:Update` | 280 | 28 | interpreted | 3 | live | yes | - |
| ui_main_menu.script | `main_menu:Update` | 95 | 11 | compiled | 0 | shadowed | no | - |
| ui_mm_faction_select.script | `UINewGame:Update` | 767 | 37 | interpreted | 7 | shadowed | no | - |
| ui_mutant_loot.script | `UIMutantLoot:Update` | 321 | 20 | interpreted | 1 | shadowed | no | - |
| ui_options.script | `UIOptions:Update` | 1248 | 30 | interpreted | 4 | shadowed | no | - |
| ui_pda_contacts_tab.script | `pda_contacts_tab:Update` | 69 | 27 | mixed | 2 | live | yes | - |
| ui_pda_npc_tab.script | `actor_on_update` | 72 | 12 | interpreted | 2 | live | yes | - |
| ui_pda_npc_tab.script | `pda_npc_tab:Update` | 553 | 3 | compiled | 0 | live | yes | - |
| ui_pda_radio_tab.script | `pda_radio_tab:Update` | 851 | 53 | mixed | 14 | live | yes | - |
| ui_pda_relations_tab.script | `pda_relations_tab:Update` | 131 | 29 | mixed | 2 | live | yes | - |
| ui_pda_warfare_tab.script | `pda_warfare_tab:Update` | 245 | 28 | interpreted | 7 | live | yes | - |
| ui_sleep_dialog.script | `UISleep:Update` | 135 | 14 | mixed | 2 | shadowed | no | - |
| ui_workshop.script | `UIWorkshopRepair:Update` | 470 | 33 | interpreted | 1 | shadowed | no | - |
| ui_workshop.script | `UIWorkshopUpgrade:Update` | 986 | 86 | interpreted | 8 | shadowed | no | string_concat_in_loop x1 |
| ui_workshop.script | `UIWorkshopCraft:Update` | 1606 | 27 | interpreted | 1 | shadowed | no | - |
| utils_ui.script | `UICellItem:Update` | 617 | 37 | interpreted | 2 | shadowed | no | - |
| utils_ui.script | `UICellContainer:Update` | 2152 | 40 | mixed | 1 | shadowed | no | - |
| utils_ui.script | `UIInfoItem:Update` | 2608 | 240 | interpreted | 18 | shadowed | no | unused_local_variable x1 |
| utils_ui.script | `UIInfoUpgr:Update` | 3029 | 58 | interpreted | 3 | shadowed | no | - |
| utils_ui.script | `UICellProperties:Update` | 3212 | 34 | mixed | 4 | shadowed | no | - |
| utils_ui.script | `UIHint:Update` | 3420 | 32 | interpreted | 3 | shadowed | no | - |
| warfare.script | `actor_on_update` | 272 | 154 | interpreted | 9 | live | yes | global_write x6, debug_statement x4, table_insert_append x1, uncached_globals_summary x1 |
| xr_abuse.script | `CAbuseManager:update` | 66 | 27 | interpreted | 3 | live | no | - |
| xr_animpoint.script | `animpoint:update` | 316 | 113 | interpreted | 12 | live | yes | debug_statement x6, uncached_globals_summary x1 |
| xr_bribe.script | `npc_on_update` | 119 | 43 | interpreted | 8 | live | yes | potential_nil_access x3 |
| xr_detector.script | `actor_detector:update` | 27 | 27 | interpreted | 4 | live | no | debug_statement x1 |
| xr_eat_medkit.script | `eat_medkit:update` | 103 | 74 | interpreted | 13 | live | yes | - |
| xr_meet.script | `Cmeet_manager:update` | 283 | 191 | interpreted | 12 | shadowed | no | uncached_globals_summary x1, repeated_db_actor x1 |
| xr_motivator.script | `motivator_binder:update` | 441 | 93 | interpreted | 4 | live | yes | potential_nil_access x2, repeated_db_actor x1 |
| xr_patrol.script | `PatrolManager:update` | 272 | 5 | compiled | 0 | live | yes | - |
| xr_remark.script | `action_remark_activity:update` | 81 | 62 | mixed | 1 | live | yes | - |
| xr_walker.script | `action_walker_activity:update` | 97 | 17 | interpreted | 1 | live | no | - |
| xr_weapon_jam.script | `npc_on_update` | 18 | 40 | interpreted | 8 | live | yes | unused_local_variable x1 |
| xr_wounded.script | `Cwound_manager:update` | 234 | 34 | compiled | 0 | shadowed | no | global_write x2 |
| xrs_dyn_music.script | `stereo_sound:update` | 421 | 3 | compiled | 0 | live | yes | - |
