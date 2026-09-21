# AALO framework

Python package `aalo`: an A/B harness for S.T.A.L.K.E.R. GAMMA performance work.
It snapshots the config, applies a change, launches the game through Mod
Organizer 2, samples frames, parses the engine log, and writes the run artefacts
that `CONTRACT.md` specifies.

The harness is domain-neutral. An "idea" is any knob that can be expressed as a
`user.ltx` console command or a modlist toggle, so A-Life tuning, renderer
settings and mod removal all go through the same code path.

## Install

Python 3.12, stdlib only. Two optional extras:

```
py -3.12 -m pip install -r framework/requirements.txt   # psutil + pytest
winget install Intel.PresentMon                          # real frame data
```

Without PresentMon the harness still runs and records CPU and RSS at 1 Hz, but
every fps metric comes out `null`. Check with `py -3.12 tools/presentmon_check.py`.

## Run it

All commands run from `lab/framework` (that directory must be on the path
for `-m aalo` to resolve):

```
cd C:\code\GIT\anomaly_alao\lab\framework

py -3.12 -m aalo paths                    # resolved paths, and what is missing
py -3.12 -m aalo mo2 list --enabled       # mods in the live profile
py -3.12 -m aalo mo2 command              # the launch command that would be used
py -3.12 -m aalo snapshot take --label before-tuning
py -3.12 -m aalo run --dry-run --idea I-001
py -3.12 -m aalo runs                     # results so far
py -3.12 -m aalo ideas list
py -3.12 -m aalo log parse                # newest engine log
```

`--dry-run` does everything except launch: it snapshots, applies the config
change, writes a 30 second synthetic `samples.csv`, computes metrics, restores
the config. It needs no elevation and no game install access beyond reading.

## Before a real capture

- **Close RTSS / MSI Afterburner**, or disable its overlay. Both are running on
  this machine and hook the game, which distorts frame timing and can fight
  PresentMon for the same present hooks.
- **Install PresentMon** or accept null fps metrics: `py -3.12 tools/presentmon_check.py`.
- **Warm up.** `alife.ltx` has `auto_switch=true`, so `switch_distance` starts at
  1250 m and collapses to 450 m about ten seconds after the level loads. Frames
  captured in that window measure the transient, not the setting under test. The
  runner therefore waits for the level-load marker in the engine log and then a
  further `warmup_s` (default 30) before the first sample. Override per run with
  `--warmup`, per experiment with `warmup_s`, or globally in `aalo.toml`. If the
  marker never appears within the launch grace, the warm-up runs from launch
  instead. A dry run honours it too: it synthesises the warm-up and then trims
  it, so both paths report a measurement window of exactly the requested
  duration. The value used, whether the marker was seen, how long that took and
  how many samples were trimmed are recorded in `metrics.json` under `extra`.

## Unattended runs: auto-loading a save

By default the game boots to the main menu and someone has to load a save by
hand; the runner waits for the engine's "save loaded" line for the whole
`timeout_s`, so that works but ties you to the keyboard for every launch.

Pass `--save <name>` (or `save = "<name>"` in an experiment TOML, or
`autoload_save` in `aalo.toml [run]`) and the runner launches through
`ModOrganizer.exe run -a "-start server(<name>/single/alife/load) client(localhost)" -e "<shortcut>"`
instead of the shortcut, so the engine loads that save straight away. It also
sets `keypress_on_start off` for the run (snapshotted and restored like any
other `user.ltx` change; turn off with `skip_keypress = false`). The save must
exist in `appdata/savedgames` and its name cannot contain `( ) / "`.
`--save ""` forces a manual load even when `aalo.toml` sets a default.

```
py -3.12 -m aalo run --slug baseline --duration 300 --save gammabaseline
py -3.12 -m aalo run --experiment alife-stutter-mod --save gammabaseline
```

Stand the save somewhere representative and leave the character still; the
measurement is only as repeatable as the scene.

## Where user.ltx lives

The G.A.M.M.A. profile sets `LocalSettings=true`, so Mod Organizer 2 shadows
`Anomaly\appdata` with the profile directory. Once the game has run at least
once, `GAMMA\profiles\G.A.M.M.A\user.ltx` is authoritative and the copy in
appdata is stale; an `aalo-` profile copy likewise carries its own. Until that
first launch the profile file does not exist and appdata is used.

`config.effective_user_ltx(profile)` resolves this, and snapshot, diff and the
runner all go through it. Never read or write `cfg.user_ltx` directly.
`py -3.12 -m aalo paths` prints which file is currently authoritative.

## Elevation caveat

The GOG build of G.A.M.M.A. runs Mod Organizer 2 with `RUNASADMIN`, because the
MO2 virtual filesystem has to inject into the engine process. Windows will not
let a non-elevated process start an elevated one without a UAC prompt, so:

- **A real run must be started from an elevated terminal.** Otherwise the launch
  either raises a prompt the harness cannot answer, or fails outright with an
  error the runner reports as `could not launch MO2`.
- PresentMon also needs elevation to open its ETW session.
- `--dry-run` needs neither.

If you automate runs, use a Scheduled Task with "run with highest privileges"
rather than trying to elevate from inside Python.

## What a run writes

`data/runs/<run_id>/` with exactly the contract's four artefacts:

| file | contents |
| --- | --- |
| `manifest.json` | run id, idea id, status, exe, profile, `config_diff`, notes |
| `metrics.json` | `fps_avg`, `fps_1pct_low`, `frametime_p99_ms`, `load_time_s`, `ram_peak_mb`, `crashed`, `duration_s`, `extra` (sampler, warm-up, log summary) |
| `samples.csv` | `t_s,frametime_ms,fps`, one row per frame or per poll |
| `xray.log` | the engine log for that run, copied out of `appdata/logs` |

`fps_avg` is the harmonic mean: frametimes are averaged and inverted, so a run
that alternates 100 fps and 20 fps reports 33, not 60. `fps_1pct_low` is the
mean of the slowest 1% of frames.

## Safety rules the code enforces

- **The live MO2 profile is never edited.** Any run that toggles mods copies the
  profile to `profiles/aalo-<run_id>` first and edits the copy. `apply_mod_toggles`
  and `set_mod_enabled` raise `ValueError` if handed a profile that is not
  `aalo-` prefixed, and `delete_profile` refuses the same.
- **`user.ltx` is snapshotted before every change and restored afterwards**, to
  `data/snapshots/<timestamp>-<label>/` with a SHA-256 per file. Pass
  `keep_changes=True` to leave an edit in place deliberately.
- Nothing else inside the game install is written.

## Adding an experiment

Drop a TOML file in `framework/experiments/`. Two arms, alternated A/B/A/B for
`repeats` rounds so thermal drift hits both equally:

```toml
name = "my-experiment"
idea_id = "I-007"
repeats = 3
duration_s = 300        # seconds of gameplay to measure per run
warmup_s = 30           # settle time after the level-load marker

[baseline]
notes = "stock"

[baseline.user_ltx]     # keys are user.ltx console commands
r2_sun_quality = "st_opt_medium"

[variant]
notes = "cheaper shadows"

[variant.user_ltx]
r2_sun_quality = "st_opt_low"

[variant.mods]          # keys are exact modlist.txt names
"Jaku's Improved Shaders" = false
```

Then:

```
py -3.12 -m aalo run --experiment my-experiment --dry-run
py -3.12 -m aalo run --experiment my-experiment            # elevated
```

`alife-stutter-mod.toml` is the experiment worth running first: G.A.M.M.A. ships
a disabled mod named "Turn this on if you stutter" at higher priority than
"G.A.M.M.A. Alife optimization", so the vendor has already written both arms.

The runner writes `manifest.notes` as `baseline ...` for A runs and
`variant ...` for B runs, and scrubs the word "variant" out of a baseline's
notes. The dashboard classifies runs by that convention, so do not hand-edit
those notes.

Verify a key or mod exists before relying on it. `aalo paths` prints the
authoritative `user.ltx` to pass here:

```
py -3.12 -m aalo paths
py -3.12 -m aalo ltx "<effective_user_ltx>" --key sun
py -3.12 -m aalo mo2 list --filter shaders
```

## Measuring a rewrite in script-ms (the I-048 profiler)

Read this before queueing an FPS delta for a script rewrite. fps measures the
whole frame; a rewrite only moves the script slice of it. On the standing-still
`gammabaseline` save the per-round fps spread on **identical** arms is 203-222,
so fps cannot resolve anything under about 2% of a frame - and every per-frame
rewrite ALAO does today is worth roughly 0.01% of one. That is why I-021 and
I-040 both came back null. Measure the slice instead.

`lab/profiler/` is an overlay mod that adds exactly one script,
`zzz_alao_profiler.script`. At `on_game_start` it wraps `axr_main.make_callback`
- `_g.SendScriptCallback` funnels every scripted callback in the game through
that one function - and accumulates **inclusive, top-level** time per callback
name. Every 30 s it prints one block of `ALAOPROF|` lines to the engine log.
Because `runner.py` already copies the engine log into every run directory as
`xray.log`, there is nothing extra to collect.

It patches the table rather than editing a file, so it does not care which copy
of `axr_main.script` wins. That matters, because the answer is not obvious:
priority here is **highest-priority enabled mod, then GAMMA's ~66 loose in-place
patches in `Anomaly/gamedata/scripts`, then the `.db` archives**, and for
`axr_main.script` the loose GAMMA copy wins - it dispatches through
`spairs(intercepts[name], sort_func_values_ascend)`, the `hspairs` min-heap from
`_g_patches.script`, not the db copy's bare `pairs`. Nothing in the install
caches `axr_main.make_callback` into a local, so one assignment catches every
caller. (The binder option is the part that does care: `bind_monster.script` is
loose-patched, `bind_stalker.script` comes from the db.)

Three things it establishes rather than assumes, all in the `hdr` line:

- `units_per_ms`: `profile_timer`'s units are calibrated against a 250 ms
  `os.clock` busy loop at startup. If calibration fails the parser refuses to
  convert to milliseconds instead of quoting a made-up number.
- `overhead_ns`: what one instrumented call costs, measured the same way.
  Multiply by calls/frame to price the instrument.
- `make_callback=true`: the wrap actually took. `binders=off` by default;
  `WRAP_BINDERS` in the script also wraps the three binder `:update` methods,
  but their bodies contain the callbacks, so their inclusive time swallows the
  per-callback ranking. Leave it off unless that is what you want.

### Per-listener attribution (`WRAP_LISTENERS`, off by default)

A callback name is a mailing list, and the first in-game run showed 95% of all
scripted per-frame time sitting on one name, `actor_on_update`. Knowing *which
subscriber* costs what needs the individual listeners, and those live in
`intercepts`, a file-local in `axr_main.script`. It is reachable anyway: it is an
upvalue of `make_callback`, so `debug.getupvalue` hands it over. Setting
`WRAP_LISTENERS = true` in the overlay script makes the profiler rewrite that
table in place at `on_game_start` (by which point every listener is registered,
so load order does not matter), wrapping each subscriber in its own timer, and
wrap `callback_set` / `callback_unset` so later registrations are covered and an
unset by the original function still finds its wrapper. Rows come out labelled
`<callback>#<file>:<line>` from `debug.getinfo`, and `profile_report.py
--listeners` ranks them. Same inclusive-top-level rule as the name level, on a
second independent timer. Validated offline against the real `axr_main.script`
but **not yet in-game** - run it once with `--listeners` before quoting it.

### The tail, not the mean (`lab/profiler-hitch`, idea I-058)

Script ms/frame is an average, and an average is exactly the wrong statistic for
the thing the player complains about. In the moving run
`20260920-110221-I-053-5895a5`, `ActorMenu_on_before_init_mode` is 0.001 ms/frame
- invisible - and **8-11 ms per inventory open**, which at 215 fps is two whole
frames dropped every time you press I.

`lab/profiler-hitch` is the same overlay with a second half bolted on. Per
callback **and** per listener it records the worst single call, a fixed log2
histogram the parser reads a coarse p99 off, and the time / frame / cost of the
**first** call that crossed the floor - which is what separates "the first
inventory open of the session cost 37 ms" from "every open costs 5 ms".

All of it hides behind one numeric compare against a 0.1 ms floor. A call under
the floor pays that compare and nothing else: no table lookup, no allocation.
That matters because `drx_da_main` alone is 353 listener calls per frame. Over
240000 sub-floor calls offline it comes out at 0.96x the I-048 build (best of 5,
fresh runtime per arm, collect before each timed round), i.e. unchanged.

Two consequences worth knowing before quoting a number:

- The hitch numbers describe calls **at or above the floor** only. Everything
  below is counted by `calls` on the ordinary `cb` / `lst` line and the parser
  recovers bucket 0 as `calls - above`. So "first call" means first call above
  the floor - for an inventory open, every one of which is milliseconds, that is
  the first open.
- `hit` lines are **run-scoped cumulative**, not per window, because the
  interesting hitch lives in the first window and every report drops that.

```
py -3.12 lab/tools/i058_build_overlays.py          # builds both overlays
py -3.12 lab/tools/profile_report.py --queue <id> --listeners --hitch
```

`alao-profiler-hitch` is callback level; `alao-profiler-hitch-listeners-inv` is
per subscriber and `invalidate()`s the I-051 dispatch mod afterwards. They sit
beside `alao-profiler*`, which nothing here modifies - every locked gen-3/gen-4
number was taken with those.

Hitches need someone to press the key: an unattended `gammabaseline` run never
opens an inventory, so a hitch capture is an **attended** run.

### The other doors into Lua (`lab/profiler-walkout`, idea I-062)

`make_callback` is one door and the engine has several. Walking out of the
`gammabaseline` start area produces 1-4 frames of 25-44 ms, CPU bound
(`MsGPUBusy` ~5), and the hitch profiler shows **no listener above ~12 ms in
them** - so the cost is either engine-side or Lua the instrument cannot see. A
~31 ms frame also appears 8-9 s into every capture, standing still, with no
callback to explain it.

`lab/tools/i062_engine_entry_census.py` enumerates the doors over the live
winner tree (1350 scripts): 38 `object_binder` classes, 36 `cse_`/`se_` server
classes, 169 scheme action classes, 508 `CreateTimeEvent` sites, 17
`AddUniqueCall`, 9 `level.add_call`, and the `.ltx` `functor` bindings.

`lab/profiler-walkout` is the hitch build plus three extra timing axes and a
frame recorder:

| axis | what it wraps |
|---|---|
| `bnd` | object binders and `se_*` server objects, per class **and** method (`xr_motivator.motivator_binder.net_spawn`), lifecycle methods always, `:update` too |
| `eng` | functions the engine calls by name: `visual_memory_manager.get_visible_value`, `ProcessEventQueue`, `xr_logic.issue_event`, ... |
| `evt` | `CreateTimeEvent` / `AddUniqueCall` / `level.add_call` bodies, wrapped at registration so each is labelled with its own `file:line` |

Each axis has **its own timer and its own depth guard**. That is the fix for
the old `WRAP_BINDERS` mode, which shared `depth` and the main timer with
`make_callback` and therefore turned every callback fired inside a binder body
into an untimed `nested` - which is why it was never usable, not a matter of
cost. A single shared `active` counter stops the axes from double counting:
only a region entered with nothing else running enters the per-frame union.

The line the build exists for is `frm`, one per frame over 12 ms (capped at 400
a run):

```
ALAOPROF|1|frm|n=1|frame=8123|t=..|ms=43|dt_dev=..|u_top=..|n_top=..|u_cb=..|u_bnd=..|u_eng=..|u_evt=..|spawn=4|destroy=1|gc0=..|gc1=..|after_log=0|top=a~u,b~u,c~u
```

`ms` minus `u_top` converted to milliseconds is **engine plus everything the
wrap lists do not reach** - the number that decides "script or engine" directly.
Both clocks are printed (`ms` is the `time_global()` delta, `dt_dev` is
`device().time_delta` raw) because which is truthful at frame granularity is a
question the first run answers rather than one to assume.
`collectgarbage("count")` is sampled at both boundaries, so a frame the
collector ran in is identifiable rather than merely suspicious. The frame
*after* a `frm` line paid for the log write and carries `after_log=1`; the
report drops those.

What it still cannot see: anything registered before `on_game_start` (hence
`ProcessEventQueue` on the globals list), a wrapped function someone already
cached into a local, `update()` on classes outside the target list, and every
engine-side cost by construction. A wrapper propagates at most three return
values and does not `pcall`.

#### v2, after the first in-game run (`20260920-185607-I-062-a1c78b`)

Two faults, both of which made a capture say less than it appeared to.

1. **Zero binder classes were wrapped, silently.** `class "x" (object_binder)`
   in Anomaly is a **luabind class object - userdata**, not a table, so
   `type(cls) ~= "table"` bailed on all 32 targets and `rawget` could not have
   read them anyway. Every `frm` line read `u_bnd=0.000, spawn=0`, which looks
   exactly like a quiet scene. v2 indexes the class normally under `pcall`,
   accepts userdata, looks in the module namespace **and** as a bare global,
   prints one `bnx` line per target naming `type(mod)`, `type(cls)`, where the
   class was found and what the first method attempt said, and sets
   `selfcheck=bnd-zero` in `wdr` plus an `err` line. `profile_report --frames`
   prints a `!!` banner for it. **An axis that wrapped nothing measured
   NOTHING; it did not measure nothing happening.**
2. **Deferred functors were labelled by the plumbing.** The worst walk-era
   frame in all three captures was 29 ms, ~90% script, blamed on
   `call_cond:_g.script:456` - the bridge closure `AddUniqueCall` builds
   around somebody else's functor, with the real owner swallowed as `nested`
   on the same axis. v2 labels by the functor's own `short_src:linedefined`
   plus its registering caller (`@file:line`) and, for a time event, its
   sanitised `obj_id.ev_id`; and `level.add_call` does not wrap what
   `AddUniqueCall` hands it, so the innermost owner gets the time.

Settled by that run and not worth re-asking: **`device().time_delta` equals the
`time_global()` delta exactly**, frame for frame, so either clock is fine.
Other things it did establish: `visual_memory_manager.get_visible_value` is
20 µs per call at 1.7 calls/frame (the I-056 lead, now measured);
`smart_terrain.setup_gulag_and_logic_on_spawn` is 16-19 ms per call during the
level load; the walk-out frames themselves are 17-42 ms with **1-3% visible
script and no GC drop**; and there is a one-off 786 ms frame, 707 ms of it in
`actor_on_update#bind_campfire.script:176`
(`game.start_tutorial("tutorial_campfire_*")`), the first time you approach a
campfire in a session.

#### v3, after the second in-game run (`20260920-194429-I-063-d9e519`)

v2 works: `bnd=284+update, eng=12, evt=3` in all four captures, 32 of 32 wrap
targets hit, every class resolved as `cls=userdata, via=module`.

**The walk-out stutter is engine.** The 35-40 ms frames while walking out carry
1-3% visible script with binders now visible (`u_bnd` 0.4-1.0 ms), and one
111 ms frame had 5 `net_spawn` in it and 9.6 ms of script. That question is
answered; the prediction held.

**The 26 ms frame at t~54 s was NOT a profiler job**, and the label that said so
was wrong twice over. `uniq:zzz_alao_profiler.script:873@zzz_alao_profiler.script:1115`
has **3258 calls in a 3258-frame window - the same count as `ProcessEventQueue`**:
the game re-registers `ProcessEventQueue` through `AddUniqueCall`, we had
already wrapped it on the `eng` axis, and the `evt` hook wrapped our wrapper and
named it after its own definition site. The `@caller` half was computed at the
wrong `debug.getinfo` level and named this file every time. The work is one
frame of the game's own event queue - which is also why the same ~30 ms frame at
the same second appears in every listener-mode capture since I-058, none of
which wrapped `AddUniqueCall` at all. It is one frame per capture, present in
every arm, so it cancels in any delta; it does contaminate a p99 or a 1% low.
The second wrapper also held the `evt` axis for the whole frame, so every real
time-event body inside it was counted as `nested` - which is why every `evt:`
row in that run reads `calls=0`. v3 refuses to wrap its own wrappers.

**The big one, still open.** `bind_smart_terrain.smart_terrain_binder.update` is
**440-457 ms in ONE call in all four captures**, ~100% script, +6.5-8.0 MB of GC
in that frame, with only 6 ms of callbacks in the whole frame - so not a
listener and not a `SendScriptCallback`. The binder is three lines
(`bind_smart_terrain.script`, db copy) and forwards to
`se_smart_terrain:update()` in `smart_terrain.script` (winner: *G.A.M.M.A. ZCP
1.4 Balanced Spawns*). Ranked suspects inside it: `update_jobs()` on the smart's
first online update, which runs `fill_npc_info` + `select_npc_job` for every NPC
against every job; `try_respawn()`; `check_smart_faction()`. `load_jobs()` is
called from `on_register()`, not from `update()`, so it should not appear.
v3 adds a `sub` axis for those methods, with their leaves on `eng` so one level
further down is timed instead of counted as nested, and an `nst` line that dumps
the five most expensive regions inside any top-level call over 50 ms:

```
ALAOPROF|1|nst|n=1|frame=26447|t=138788|axis=bnd|scope=<the slow call>|units=..|in=a~u,b~u,c~u
```

`profile_report --frames`/`--axes` prints it with an `acct` column: how much of
the slow call the five slots actually explain. `lab/coord/overlays/i062-smart-terrain-request.json`
is the attended run that answers it.

Also from that run: the campfire tutorial is
`bind_stalker.actor_binder.update` at 724.7 / 736.4 ms inside 755 / 771 ms
frames on the baseline captures and **absent from both `alao-prewarm` v1.2
captures**; `npc_on_death_callback` 30.1 ms once; and capture 2 alone had a
dozen 27-38 ms frames at t=34-41 s dominated by `sim_squad_scripted.update` with
+3.5-4 MB of GC per frame, inside the warm-up window.

One Lua-5.1 trap worth recording: `install()` grew past the **60-upvalue limit**
when the `sub` axis was added, which is a *load-time* error - the overlay would
not have loaded in the game at all, and only the offline `loadstring` check
caught it. The axis setup now lives in its own `install_axes()`.

#### v4, after the hamlet run (`20260920-201819-I-062-a83793`)

The `nst` line worked and **falsified the top suspect**. In all four captures
`bind_smart_terrain.smart_terrain_binder.update` is 439.6 / 432.0 / 445.9 /
431.7 ms and `in=` reads `smart_terrain.se_smart_terrain.try_respawn` at
**99.97%** of it, then only `se_stalker_on_spawn` rows at 0.1-0.26 ms
(`xrs_rnd_npc_loadout.script:136`). `update_jobs` never appears.
`smr_pop.smart_can_respawn` - the gate - is **3 ms over 750 calls**, and
`try_respawn` as a whole is **7.8 ms over 920 calls** apart from that one. So
the cost is entirely *after* the gate, in one squad being created on the frame
the actor arrives.

The path, read out of the live winners (`smart_terrain.script`,
`sim_board.script`, `smr_pop.script`, all from *G.A.M.M.A. ZCP 1.4 Balanced
Spawns*):

```
se_smart_terrain:try_respawn()                       smart_terrain.script:1599
  smr_pop.smr_handle_spawn(section, self)            smr_pop.script:1193
    SIMBOARD:create_squad(smart, section)            sim_board.script:131
      alife_create(squad_id, ...)                    engine
      squad:create_npc(smart)                        sim_squad_scripted.script:629
        alife_create per member                      engine
      smr_pop.remove_disabled_mutants_from_squad
      smr_pop.adjust_squad_size                      may create more members
      smr_pop.replace_mutant_variants_in_squad
      SIMBOARD:assign_squad_to_smart
      per member: SIMBOARD:setup_squad_and_group + SendScriptCallback
      smr_civil_war.setup_civil_war_squad
  per member: SIMBOARD:setup_squad_and_group         smart_terrain.script:1730
  smr_civil_war.setup_civil_war_squad                smart_terrain.script:1742
```

**Already visible from the static read:** the last two lines are *duplicates*.
`create_squad` already ran `setup_squad_and_group` for every member and
`setup_civil_war_squad` for the squad; `try_respawn` then does both again. That
is not the 440 ms on its own, but it is free to delete and it is the kind of
thing the next `nst` will size exactly.

v4 follows the path down with **one axis per level**, because two functions on
one axis mean the inner one is `nested` and invisible - which is how a 440 ms
call had nothing in it to look at for two runs:

| level | axis | what |
|---|---|---|
| 1 | `bnd` | `bind_smart_terrain.smart_terrain_binder.update` |
| 2 | `sub` | `se_smart_terrain.try_respawn` |
| 3 | `spn` | `simulation_board.create_squad` |
| 4 | `mem` | `create_npc`, `init_squad`, `adjust_squad_size`, `remove_disabled_mutants_from_squad`, `replace_mutant_variants_in_squad`, `setup_squad_and_group`, `assign_squad_to_smart`, `setup_civil_war_squad` |
| 5 | `acr` | `alife_create`, `alife_create_item` |

`smr_pop.smr_handle_spawn` is deliberately **not** wrapped: it is a dispatcher
that returns `create_squad`'s result, and wrapping both on one axis would hide
`create_squad`. The next `nst` therefore answers the only question that decides
what a fix can be: **is it N x `alife_create` (engine, so the only lever is
*when*) or Lua around it (fixable outright)?**

Also v4: per-axis `nst` floors (`cb`/`evt`/`lst` at 20 ms, everything else 50),
and a region that ran *inside* another one now gets an `nst` line with
`top=0` and no breakdown - named, but not decomposed, because the slots at that
moment belong to whatever contained it.

##### The t~54 s job finally has a name

`ProcessEventQueue` reads 26.9-28.7 ms there again, and the frame line's own
top-3 names the owner: **`evt:dynamic_news_manager.script:250#DynamicNewsManager.TickNews@dynamic_news_manager.script:220`,
26.7 ms** (live winner: *116- Dialogues Expanded - indyora*). It is a time-event
body, it is the game's, it fires once a session at that point, and it is in
every arm.

##### The `sim_squad_scripted.update` bursts

Captures 1, 2 and 3 all show 10-24 frames of 30-40 ms in the first ~25 s after
the load (t=30-41 s), each frame carrying many
`sim_squad_scripted.sim_squad_scripted.update` calls at 2-3.5 ms with +1.5-4 MB
of GC. Static read: `sim_squad_scripted:update` has a `first_update` branch that
runs once per squad and, among other things, `alife_create_item`s the squad's
`item_on_all` list for every member. So this is every squad on the level doing
its one-off first update, spread over the frames after a load. v4 puts
`specific_update` / `generic_update` / `refresh` / `check_online_status` /
`get_script_target` on the `sub` axis to split it.

##### Fix options for the 440 ms, with what each one breaks

These are written before the measurement that chooses between them, so that the
measurement can still say no.

**(a) If it is `alife_create`-bound** (engine time, N members x ~tens of ms):
nothing makes a member cheaper, so the only lever is *when*. Spread creation
over frames - one member per frame from a `CreateTimeEvent`. Non-identities,
all of them real: a squad exists **half-built for N frames**, and
`squad:squad_members()` is iterated by `setup_squad_and_group`, by
`smr_civil_war.setup_civil_war_squad` (relations are set per member, so a member
added later gets none), by task targets and by the simulation's target
selection, which can pick a one-member squad and then find it has five. A save
taken mid-build stores a squad whose `already_spawned` count has already been
incremented. And `create_squad` returns the squad to `try_respawn`, which
immediately iterates its members - that loop would have to move too. This is the
option with the worst blast radius and it should only be taken if the time is
genuinely engine-side.

**(b) Move the roll off the arrival frame.** The gate is `if
(self.is_on_actor_level and self.dist_to_actor ~= nil) then if (self.dist_to_actor
< self.respawn_radius) then return end end` - so a respawn fires only while the
actor is **outside** the radius. It coincides with walking to the hamlet because
`dist_to_actor` is `self.online and ...distance_to(actor) or math.huge`: while
the smart is offline the distance is `math.huge`, the radius gate cannot stop
it, and the very first `update()` after the smart comes online is the first one
that both has `already_spawned` filled in and passes `smart_can_respawn`. (The
30% first-spawn skip that would have damped this is commented out at
`smart_terrain.script:1646-1651`.) If that reading is right - and the next run
can confirm it by whether `try_respawn` is expensive exactly once per smart -
then **every smart with `respawn_params` that the player approaches for the
first time in a session pays one squad spawn**, and this is a whole-map pattern,
not one hamlet. Sizing it is a config count: how many smarts on a level carry
`respawn_params`. The fix is to make the first post-online roll happen off the
arrival frame (defer it by a few seconds via a time event), which changes
*nothing* about what spawns - only when - and is far cheaper in behaviour than
(a). Non-identity: a player who runs through a smart and leaves within the
deferral gets no spawn at all that visit.

**(c) Anything quadratic in SMR.** `smr_civil_war.setup_civil_war_squad` and
`smr_pop.*` take the squad and the smart's name and consult faction tables;
`remove_disabled_mutants_from_squad` and `replace_mutant_variants_in_squad` each
iterate `squad:squad_members()` separately, and `adjust_squad_size` can create
more members after the first two have already walked the list. Three passes over
the members plus a fourth in `create_squad` plus a fifth in `try_respawn` is
five iterations of the same list, and the duplicate
`setup_squad_and_group` / `setup_civil_war_squad` pair above is a sixth and
seventh. If the `mem` axis shows these dominating rather than `acr`, this is
ordinary Lua and fixable outright, with no behavioural change at all beyond
deleting the duplicate work.

`lab/coord/overlays/i062-smart-terrain-request.json` is the run that chooses.

```
py -3.12 lab/tools/i062_engine_entry_census.py --top 30
py -3.12 lab/tools/i062_build_overlays.py      # alao-profiler-walkout[-listeners-inv]
py -3.12 lab/tools/profile_report.py --queue <id> --frames --axes --hitch --listeners --trace
```

`alao-profiler-walkout-listeners-inv` also carries I-063's per-call inventory
trace, because the user gets one attended session and both questions have to fit
in it.

### Running an arm with it

Add one key to the queue request. The profiler is the **instrument, not the
treatment**, so it is installed once, at the top of the load order, and enabled
in *both* arms:

```json
{
  "label": "i043-make-callback",
  "baseline_overlay": ".../overlays/ref3-alao-b",
  "variant_overlay":  ".../overlays/my-arm-b",
  "baseline_overlay_bottom": ".../overlays/ref3-vanilla-bottom",
  "variant_overlay_bottom":  ".../overlays/ref3-vanilla-bottom",
  "profiler_overlay": "C:/code/GIT/anomaly_alao/lab/coord/overlays/alao-profiler",
  "repeats": 3, "duration_s": 300, "warmup_s": 30, "save": "gammabaseline"
}
```

The finished queue item then carries a `profiler` section next to the fps
numbers: mean script ms/frame per arm, the run-to-run spread, and the top-20
callback ranking. Each run directory also gets a `profiler.json`.

### Reading it back

```
py -3.12 lab/tools/profile_report.py --queue <queue id>
py -3.12 lab/tools/profile_report.py --arm-a <run dirs...> --arm-b <run dirs...>
py -3.12 lab/tools/profile_report.py <one run dir>         # single arm
```

The first window of every run is dropped by default (`--drop-first`): it
straddles the level load and the warm-up. A run also loses up to one window of
tail, because a window only reaches the log when it is dumped.

### What the A/A run measured (2026-09-19, `20260919-184343-I-048-757367`)

Both arms identical (`ref3-alao-b` + the profiler on top, `ref3-vanilla-bottom`
at the bottom), 3 repeats x 300 s each, standing still on `gammabaseline`:

| | all 6 rounds | dropping each arm's first round |
|---|---:|---:|
| total script ms/frame | 0.749 | 0.724 |
| run-to-run cv, script ms/frame | **5.48%** | **1.64%** |
| run-to-run cv, fps avg, same runs | 4.35% | 4.82% |
| A/A delta, script ms/frame | -0.14% | +1.04% |
| A/A delta, fps avg | -1.83% | +0.62% |

Three things follow.

1. **There is a script-side session warm-up the in-level warm-up does not
   cover.** The first round of each arm reads ~10% high in script ms/frame
   (0.810 / 0.792 against 0.715-0.740 for every later round) while its fps is
   unremarkable - so it is not a general session artefact, it is specific to
   script time (LuaJIT traces, caches). **Run 4 repeats and drop the first round
   of each arm** (`profile_report.py --drop-rounds 1`; `fps_runner` reports both
   numbers, `script_ms_per_frame` and `script_ms_per_frame_warm`). Then the cv
   target is met with room to spare.
2. **Script time is 15.7% of the frame**: 0.75 ms of a 4.78 ms frame at 209 fps.
   That is the whole argument for this instrument. A rewrite that cuts script
   time by 10% moves the frame by 1.6% - under what fps can resolve - but moves
   script-ms by 10%, which is 6x the warm instrument's noise.
3. **94.5% of it is one callback name**, `actor_on_update`, at ~709 us per
   frame, one call per frame. `npc_on_update` is 1.7%, `npc_on_choose_weapon`
   1.5%, `squad_on_update` 0.7%; nothing else clears 0.5%. Measured standing
   still in a quiet spot, so the npc/monster rows are starved of work and the
   ranking is site-specific - but the actor row is not, and it is where every
   optimisation should be aimed. Use `WRAP_LISTENERS` to find out which
   subscriber inside it costs what.

### Honesty rules for a script-ms number

1. Quote the **run-to-run** `cv_pct`, not the within-run one. Windows inside a
   run are correlated; runs are the unit of noise. And say whether the first
   round was dropped - it changes the cv by 3x.
2. A delta is only resolvable if it clears both arms' cv. `profile_report.py`
   prints them next to the delta for exactly that reason.
3. Subtract nothing for the instrument, but state `overhead_ns x calls/frame` -
   it is present in both arms and cancels in the delta, yet it inflates the
   absolute ms/frame.
4. Numbers are **inclusive** of everything a callback calls. A nested
   `make_callback` is counted (`nested`) but not timed, so per-name times sum to
   the total without double counting.
5. Never quote a number from a round the runner marked `capped`.

## Beam search over ideas

`data/ideas.json` holds the pool. Score a measured idea, keep the top *k* of a
generation, and spawn children from the survivors:

```
py -3.12 -m aalo ideas add "Lower sun shadow quality" --category render --gain med
py -3.12 -m aalo ideas score I-001 4.2
py -3.12 -m aalo ideas beam --keep 3 --spawn 2
```

Unscored ideas are never pruned by `beam`: they have not been measured, so they
are neither survivors nor casualties.

## Modules

| module | role |
| --- | --- |
| `config.py` | resolves every path from `framework/aalo.toml`; nothing else hardcodes paths |
| `ltx.py` | round-trip parser for sectioned `.ltx` and for flat `user.ltx` |
| `mo2.py` | ModOrganizer.ini, modlist.txt, profile copies, launch commands |
| `snapshot.py` | snapshot/restore `user.ltx` and a profile; diff into `config_diff` |
| `xraylog.py` | engine log: load time, level markers, warnings, FATAL ERROR, A-Life stats |
| `profiler.py` | the I-048 `ALAOPROF\|` dumps: ms/frame, per-callback ranking, run-to-run spread |
| `metrics.py` | PresentMon or psutil sampling, and the fps/frametime math |
| `runner.py` | one run, or a whole A/B experiment |
| `ideas.py` | the idea pool and its beam search |

## Tools

```
py -3.12 tools/tail_xray_log.py --errors     # follow the newest engine log
py -3.12 tools/presentmon_check.py           # is real frame capture available
py -3.12 tools/seed_demo_runs.py --count 3   # synthetic runs for the dashboard
py -3.12 tools/profile_report.py --queue <id>  # I-048 script-ms report for a run
```

Seeded runs carry `"demo": true` in their manifest, so they are never mistaken
for real measurements, and come as a baseline/variant pair per idea so the
dashboard summary has deltas to show. `--clean` removes them.

## Tests

```
cd C:\code\GIT\anomaly_alao\lab
py -3.12 -m pytest tests -q
```

The suite parses the real `fsgame.ltx` and the real `modlist.txt` read-only, and
creates then deletes one `aalo-unittest` profile copy to prove the copy path
leaves the source untouched. It never launches the game.
