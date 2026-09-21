# alao-squad-stagger (I-065)

One new script, `gamedata/scripts/zzz_alao_squad_stagger.script`. Replaces no file, patches no
function, saves nothing. Kill switch: `local ENABLED = true` at the top of the script.

## The hitch

After loading `gammabaseline` there are, in some loads, 25 frames of 12-40 ms spaced ~455 ms
apart between t = 10 s and t = 22 s after the load. Evidence, all from the existing captures
(`lab/tools/i065_burst_report.py --queue <id>` prints this table):

| queue item | loads | with the burst | burst frames >= 12 ms | >= 20 ms | >= 30 ms | worst | script share |
|---|---:|---:|---:|---:|---:|---:|---:|
| `20260920-201819-I-062-a83793` | 4 | 3 | 25 / 25 / 25 | 15-16 | 4-10 | 38-40 ms | 87-88 % |
| `20260920-194429-I-063-d9e519` | 4 | 1 | 25 | 17 | 7 | 38 ms | 87 % |
| `20260920-185607-I-062-a1c78b` | 4 | 1 (by the `squad_on_first_update` window counts; no `frm` squad rows in that profiler build) | | | | | |
| `20260920-171714-I-063-ef2deb` | 4 | 4 (same) | | | | | |

So it is **not** every load: 9 of 16. The work is the same in all 16 - 523 `squad_on_first_update`
callbacks, 410-540 ms of `sim_squad_scripted:update` in total - and what differs is where the
engine's ALife scheduler puts it:

* **mode A (7 of 16):** all 523 first updates land in frame 8, the `actor_on_first_update` frame,
  behind the loading screen (worst single update 15-17 ms, cold; nobody sees it).
* **mode B (9 of 16):** the scheduler starts ~10 s after the load and visits ~20 squads per tick,
  one tick every ~455 ms, 25-26 ticks. Each tick is one frame. Per-squad cost in those frames is
  1.6-4.7 ms; the frame's callback axis is 0.6 ms, so it is not a listener.

Why the first update is expensive and the second is 50-60 us: `STATE_Write` saves
`current_target_id` but not `assigned_target_id`, and `init_squad_on_load` resets `current_action`
to 0. A squad that was travelling when the game was saved comes back without a target, and
`generic_update` falls through to `SIMBOARD:get_squad_target`, which evaluates every smart terrain
and squad in the simulation registry (Lua plus luabind crossings, ~350 kB of garbage per squad).
It is **Lua, not `alife_create`**: the `item_on_all` item creation in the first-update block only
applies to the few squad sections that set it.

## The lever

The search cannot be cut without changing what squads do, and it cannot be cached across a save
without touching the save format. It can be moved: the engine itself runs it behind the loading
screen in 7 loads out of 16. The mod makes that the rule. At `actor_on_first_update` it walks
`SIMBOARD.squads` in id order and calls `squad:update()` on every squad whose `first_update` is
still `false` - the same call `bind_monster.script`, `xr_effects.script` and `squads_filler.script`
already make from script. When the scheduler reaches those squads later they are ordinary 50 us
updates.

Fallback: the load sweep is capped at `LOAD_BUDGET_MS` (2000). Anything left is drained in play on
`actor_on_update`, nearest squad first, stopping each frame once `FRAME_BUDGET_MS` (3) is spent and
never more than `FRAME_MAX_SQUADS` (8) per frame. One cold update is indivisible (up to 4.7 ms), so
the bound is "budget plus at most one expensive squad". `SWEEP_AT_LOAD = false` gives the pure
stagger. The per-frame callback exists only while there is a backlog.

## What changes for the player

* Mode-A loads: nothing. If the engine swept first the mod logs `0 pending` and returns.
* Mode-B loads: the loading screen is ~0.4-0.5 s longer and the 25 stutters are gone. Squads pick
  targets 10-20 s earlier than they would have, with the same code, in the same order.
* Every squad gets one extra `update()` per load. `update()` is pcall'd here (the engine's call is
  not), so an erroring update is logged once instead of crashing from this call site.
* Saves: untouched. Remove the mod and the next load is a coin flip again.

## Log lines

```
[alao_stagger 1.1] installed: enabled=true sweep_at_load=true load_budget_ms=2000 frame_budget_ms=3 frame_max_squads=8 clock=os.clock
[alao_stagger] load sweep: 523 known, 523 pending, 523 updated in 431 ms (max one squad 16.0 ms), 0 errors, 0 left for the in-play drain
[alao_stagger] 523 squads, 1 frames, max per frame 431.0 ms (in play 0.0 ms), max one squad 16.0 ms, load sweep 523 in 431 ms, drain frames 0, skipped 0, errors 0, drained at load
```

or, when the engine got there first:

```
[alao_stagger] load sweep: 523 known, 0 pending - the engine got there first (or no squads); nothing to do
[alao_stagger] 0 squads, 0 frames, max per frame 0.0 ms (in play 0.0 ms), ... nothing pending
```

Read them before crediting the mod: a variant capture without a burst and with `0 pending` was a
mode-A load and proves nothing.

## Not verified offline

* That `actor_on_first_update` runs before the engine's own sweep in mode-A loads (either order is
  handled; the log line says which happened).
* Why the engine picks mode A or B. It does not matter for the fix, but it sets the sample size:
  with p(B) ~ 0.56 (9 of 16), six baseline loads see at least one burst with probability 0.99.
* The size: expected -25 to -35 ms on each of the ~25 burst frames (they become ordinary frames
  with ~1 ms of squad updates in the tick), in the loads that had them.
