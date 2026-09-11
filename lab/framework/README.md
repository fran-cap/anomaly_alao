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
| `metrics.py` | PresentMon or psutil sampling, and the fps/frametime math |
| `runner.py` | one run, or a whole A/B experiment |
| `ideas.py` | the idea pool and its beam search |

## Tools

```
py -3.12 tools/tail_xray_log.py --errors     # follow the newest engine log
py -3.12 tools/presentmon_check.py           # is real frame capture available
py -3.12 tools/seed_demo_runs.py --count 3   # synthetic runs for the dashboard
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
