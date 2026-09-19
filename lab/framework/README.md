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

### Honesty rules for a script-ms number

1. Quote the **run-to-run** `cv_pct`, not the within-run one. Windows inside a
   run are correlated; runs are the unit of noise.
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
