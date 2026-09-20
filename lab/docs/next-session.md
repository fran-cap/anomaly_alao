# Next session: generation 4 (profile-guided)

Written 2026-09-19 at the end of the gen-3 run. Read this, `lab/coord/README.md`,
`lab/framework/README.md` ("Measuring a rewrite in script-ms") and `beam-ideas.md` section 10 first.

## Locked in (do not re-measure)

| arm (gammabaseline save, standing still) | script us/frame | fps avg |
|---|---|---|
| full ALAO = `ref3-alao-b` on top + `ref3-vanilla-bottom` at the bottom | 712.6 (cv 1.2%, 4x120 s) | 213 |
| full ALAO + I-043 `make_callback` array dispatch (`agent-I043-b`) | 602.5 (cv 2.5%) | 210 (noise) |

Older fps baselines (stock 209.7, full ALAO 215.2) stand, with the caveat that the gen-1/2 bottom
overlay carried a db-derived `bind_monster.script` over GAMMA's loose patched one.

## The rules now

- **Bar: an idea goes in-game when site arithmetic (or the listener ranking) says >= 25 us of script
  time saved per frame.** Absolute, set by the user. Quote us/frame first, percent second.
- **Instrument: the I-048 profiler, not fps.** Request key `profiler_overlay`
  (`lab/coord/overlays/alao-profiler`, or `alao-profiler-listeners` for per-listener attribution, which
  adds ~124 us/frame of its own). Protocol: **4 repeats x 120 s, round 1 of each arm dropped**
  (`script_ms_per_frame_warm`). Launches buy certainty, minutes do not (49-run analysis, section 10).
  FPS is a secondary readout and cannot see under ~100 us.
- Live copy of a script = top enabled mod, else loose `Anomaly/gamedata/scripts` (66 files), else db.
  Run `build_overlay.py` from the main checkout.
- Gates G4-G9 as before; G9 is now built into `tools/corpus_run.py` (`captures` in results.json).
- If the runner code changed, the user must restart `fps_runner.py`; a running runner keeps old code.
- Create agent worktrees from the lab branch head, and check `git log -1` in each before starting.

## The gen-4 beam

| Idea | Deciding question | Instrument |
|---|---|---|
| **I-049** shared throttled dispatcher for `drx_da_main.script:2734` | 353 per-anomaly `actor_on_update` closures cost ~179 us/frame doing a throttle check each. Does one module-level walker over a due-time array (or bucketed queue) take that under 20 us with identical behaviour? | differential tests offline, then profiler (listener mode before/after) |
| **I-050** top single listeners | `demonized_ledge_grabbing.script:443` 142 us and `zzz_player_injuries.script:1503` 74 us, every frame, standing still. What does plain `--fix` buy inside them, and what does a throttle / early-out buy? | microbench the bodies, then profiler |
| **I-051** deliver I-043 | Package the dispatch patch as a standalone mod against the stock loose `axr_main.script` and draft the upstream PR (lines are the Kutez priority patch, xray-monolith PR #339). Reproduce the delta on the stock, non-ALAO baseline. **Publishing anywhere is the user's call.** | profiler |
| **I-052** flag-combination fixpoints | Fix `--fix-nil` re-guarding inserted caches (24 violations) and the `--fix --fix-debug` two-pass case; add a `--fix --fix-debug --fix-nil` run to the integration gate. | corpus |
| **I-053** (new, unregistered) a moving-scene save | Everything so far is standing still in a quiet spot, which starves npc/monster/squad callbacks. A second locked scene (scripted walk or a busy hub) would say whether the ranking generalises. | profiler A/A first |

Also worth asking once I-049 is measured: is "callback closure registered from a binder constructor /
per-object init" detectable statically? That would be ALAO's first profile-motivated RED pattern.

## Organizer notes (carried over, still true)

- pytest with `-p no:cacheprovider --basetemp=<scratch>`; agents use per-agent scratch subdirectories.
- Bash tool 10-minute cap kills lock waits; launch long `coord run` jobs detached.
- Corpus jobs wait on the `game` lock, so agents starve while the runner drains a queue; schedule the
  in-game block, or accept that corpus gates land at integration.
- Merge order that was conflict-free in gen-3: I-048, I-044, I-042, I-043, I-046.
- Files are CRLF; conflict-resolution regexes need `\r?\n`.

## Housekeeping still open

- `extracted/_work` and `lab/coord/overlays/agent-*`, `ref-alao-*`, `vanilla-db-bottom` (stale) are deletable.
- `lab/data/runs` is the raw evidence; gitignored on purpose, archive rather than delete.
- Agent worktrees under `.claude/worktrees/` and branches `agent/gen3-*` can go once the merge is pushed.
