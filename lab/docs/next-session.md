# Next session: generation 5 (stack the wins, second scene)

Written 2026-09-19 at the end of the gen-4 run. Read this, `lab/coord/README.md`,
`lab/framework/README.md` ("Measuring a rewrite in script-ms") and `beam-ideas.md` section 11 first.

## Locked in (do not re-measure)

All script us/frame, I-048 profiler in both arms, 4x120 s, round 1 of each arm dropped, `gammabaseline`, standing still.

| arm | baseline -> variant | delta | queue item |
|---|---|---:|---|
| full ALAO (`ref3-alao-b` + `ref3-vanilla-bottom`) | ~710 (701.7-742.2 across five runs) | | |
| + I-043 `make_callback` array dispatch | 712.3 -> 607.3 | -105 (was quoted as -110, that was all four rounds) | `20260919-192703-I-043-3f2729` |
| + I-049 `drx_da_main` shared dispatcher | 742.2 -> 519.8 | **-222** | `20260919-210108-I-049-35365e` |
| + I-050a ledge-grabbing cold-camera guard | 710.7 -> 604.0 | **-107** | `20260919-211043-I-050-025a5a` |
| + I-050b player-injuries crossing cut | 701.7 -> 686.0 | -16, arms overlap, under the bar | `20260919-211144-I-050-34ad1e` |
| **all four together** (`gen4-all-b`) | 714.8 -> 309.5 | **-405 (-57%)**; fps avg +3.7%, 1% low +13.5% | `20260919-232541-I-054-d26d45` |
| **stock** GAMMA + dispatch mod (no ALAO) | 713.5 -> 624.4 | **-89** | `20260919-211347-I-051-21408e` |

## The rules now

- **Bar: >= 25 us of script time saved per frame** by site arithmetic or the listener ranking. Quote us/frame first.
- **Instrument: the I-048 profiler** (`profiler_overlay` request key), 4 repeats x 120 s, drop round 1. FPS is a
  secondary readout and saw none of the gen-4 wins individually.
- **Pricing Lua->C crossings: use the pessimistic read.** I-050b removed 59 of 104 crossings and got 16 us, not 34;
  trivial getters are ~0.25 us, UI calls carry the cost.
- **Benches: check the locks before AND after**, and `jit.off()` (no args) is the global switch;
  `jit.off(true,true)` does nothing for code loaded afterwards.
- **Keep the box quiet during captures.** Agents working cost ~2 points of baseline cv. Corpus jobs and fps runs
  exclude each other; to give a corpus job a window, park pending items in `lab/coord/queue/held/` and move them back.
- Multi-flag gate: `tools/corpus_matrix.py` (default four combos; `--combos all` for eight). Second gate corpus is
  `extracted/vanilla_db` (826 files), not `extracted/vanilla` (66).
- Live copy of a script = top enabled mod, else loose `Anomaly/gamedata/scripts` (66 files), else db.
- If the runner code changed, the user must restart `fps_runner.py`. (It did not change in gen-4.)
- All work is on `main`; agent worktrees branch from `main`, check `git log -1` first.

## The gen-5 beam

| Idea | Deciding question | Instrument |
|---|---|---|
| **I-054** listener-mode pair | The combined arm is measured (-405 us, table above). Left: one listener-mode pair (`ref3-alao-b` vs `gen4-all-b`) for the new ranking of the remaining ~310 us and to explain I-050a's variant drift. The dispatch mod needs `invalidate()` after `wrap_all_listeners` in listener mode. | profiler, listener mode |
| **I-053** a moving / busy-hub save | Everything is standing still, which flatters I-050a's guard (it never fires while moving) and starves npc/monster callbacks. An exploratory moving run exists (`20260920-110221-I-053-5895a5`, user playing: 851 -> 413 us/frame, beam 11.1) and says the wins generalise; a LOCKED repeatable scene still **needs the user to make the save.** | profiler A/A first |
| **I-051 publishing** | Mod folder `lab/mods/alao-make-callback-dispatch/` and PR draft `lab/docs/i051-upstream-pr-draft.md` are ready. Same question for the I-049 and I-050a patches (`--pristine` patcher output; both are Demonized's mods). **The user decides whether and where.** | |
| **I-058** hitch attribution | The per-frame mean hides what the player feels: `ActorMenu_on_before_init_mode` is 8-10 ms per inventory open, `actor_on_jump` 1.5-2.8 ms, `actor_on_land` 1.2-1.6 ms (moving run, beam 11.1). Which listeners, and first-open vs every-open? | profiler + per-call max |
| **I-055** multi-line-argument hoist gap | `_edit_repeated_calls` bails at paren depth > 0, so reported `repeated_device` / `repeated_db_actor` never land; `device()` alone is 177 redundant calls / 121 functions / 59 files. Run `lab/tools/i050a_paren_gap_scan.py` to size it. | corpus matrix |
| **I-056** `ini_file_ex:r_value` never caches `false` | How many per-frame reads of false-valued MCM options are there on the live stack, and what do they cost? | site arithmetic, then profiler |
| **I-057** small-listener bundle | `actor_effects` fog latch, `battery_warning`, `fluid_aim`, `light_gem`: ~25 us estimated together, likely less. Only as one arm, only after I-053. | profiler |

Also open: a RED pattern for a "do once" latch declared `local` inside a per-frame function
(`zzz_player_injuries.script:1575`); a `hold` verb for `coord.py queue`.

## Organizer notes (carried over, still true)

- pytest with `-p no:cacheprovider --basetemp=<scratch>`; agents use per-agent scratch subdirectories.
- Bash tool 10-minute cap kills lock waits; launch long `coord run` jobs detached.
- Merge order that was conflict-free in gen-4: I-052, I-051, I-049, I-050a, I-050b.
- Files are CRLF; conflict-resolution regexes need `\r?\n`. A Python patcher saved CRLF turns `.replace("\n", NL)` into `\r\r\n`.
- A still-running `fps_runner.py` from an earlier session will pick up queue items by itself.

## Housekeeping still open

- `extracted/_work` and `lab/coord/overlays/agent-I0[0-4]*`, `ref-alao-*`, `vanilla-db-bottom` (stale) are deletable.
- `lab/data/runs` and `lab/data/corpus/*` run dirs are the raw evidence; archive rather than delete.
- Gen-4 agent worktrees under `.claude/worktrees/` and branches `agent/gen4-*`, `worktree-agent-*` can go once the merge is pushed.
