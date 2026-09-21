# Next session: generation 6 (the runs that need the user, then hitches)

Written 2026-09-20 at the end of the gen-5 run. Read this, `lab/coord/README.md`,
`lab/framework/README.md` ("Measuring a rewrite in script-ms", "The tail, not the mean") and `beam-ideas.md` section 12 first.

## UPDATE, same evening: generation 6 already ran (hitches). Start here.

`beam-ideas.md` sections 12.1 and 13 have the numbers. The attended runs in the table further down (I-057 moving, I-058 hitch)
are DONE; I-057 is kept (-37 us/frame moving), the hitch bar is adopted (>= 5 ms, read as what lands in one frame; cold costs stack).
`integrate/gen6` = main + `agent/gen6-I063` (alao-prewarm v1.3) + `agent/gen6-I062` (walk-out profiler v4): 711p/7s/4xf, lab 357p.

Open, in order:

1. **I-064 hamlet squad spawn, 430-450 ms, 8 of 8 captures.** `try_respawn` is 99.97 % of it. The v4 profiler (`alao-profiler-walkout-listeners-inv`,
   one axis per nesting level, `acr` = `alife_create`) decides engine-bound (defer / spread) vs Lua (delete the duplicated
   `setup_squad_and_group` / `setup_civil_war_squad` passes). Request ready: `lab/coord/overlays/i062-smart-terrain-request.json`, attended,
   walk straight to the hamlet and do NOT avoid other smarts (tests 'once per smart per session'). Then the fix as a patcher or monkey patch, then re-measure.
2. **I-066 `get_visible_value`** 34 us/frame standing: cache the 8 MCM reads. Unattended profiler run can score it on the `eng` axis.
3. **I-065 squad first_update bursts** in the first ~25 s after every load.
4. **I-063 leftovers:** inventory first open by FRAME ms over >= 4 captures per arm (expect little); `lam2.script:271` first animated item use 9-26 ms.
5. I-061 arm drift A/A, I-060 orphaned cache refs, I-053 locked moving save (still needs the user) - unchanged from below.

Attended-run habits that worked: tell the user the capture count (repeats x 2 ARMS, even when both arms are the same overlay); the user
cannot be pinged mid-run, they message when done; read `[alao_prewarm]` / `wdr` / `bnx` log lines BEFORE crediting a mod or an instrument
with a result; run `profile_report.py` from the branch that has the parser (`--frames --axes --trace --hitch`).
Publishing: the user said not now, they will clean up first. Do not raise it each round.

## Locked in (do not re-measure)

All script us/frame, I-048 profiler in both arms, 4x120 s, round 1 of each arm dropped, `gammabaseline`, standing still.

| arm | baseline -> variant | delta | queue item |
|---|---|---:|---|
| full ALAO (`ref3-alao-b` + `ref3-vanilla-bottom`) | ~710 (701.7-742.2 across five runs) | | |
| + I-043 `make_callback` array dispatch | 712.3 -> 607.3 | -105 | `20260919-192703-I-043-3f2729` |
| + I-049 `drx_da_main` shared dispatcher | 742.2 -> 519.8 | **-222** | `20260919-210108-I-049-35365e` |
| + I-050a ledge-grabbing cold-camera guard | 710.7 -> 604.0 | **-107** | `20260919-211043-I-050-025a5a` |
| + I-050b player-injuries crossing cut | 701.7 -> 686.0 | -16, arms overlap, under the bar | `20260919-211144-I-050-34ad1e` |
| **all four together** (`gen4-all-b`) | 714.8 -> 309.5 | **-405 (-57%)**; fps avg +3.7%, 1% low +13.5% | `20260919-232541-I-054-d26d45` |
| **stock** GAMMA + dispatch mod (no ALAO) | 713.5 -> 624.4 | **-89** | `20260919-211347-I-051-21408e` |
| `gen4-all-b` + I-057 (ledge on demand + bundle), `agent-I057-b` | 309.7 -> 291.8 | -18 warm, -12 all rounds (overlap); under the bar standing | `20260920-114512-I-057-e8b3e2` |

Listener ranking of what is left standing still (`20260920-112405-I-054-69d4e1`, listener mode): injuries 46, drx walker 24,
`fluid_aim` 16, `light_gem_mcm` 12, `liz_inertia_expanded` 12, `battery_warning` 10, then a flat tail. Ledge grabbing is
6 us standing, **68 us moving**.

## The rules now

- **Per-frame bar: >= 25 us of script time saved per frame** by site arithmetic or the listener ranking. Quote us/frame first.
- **Hitch bar (proposed by I-058, the user has not adopted it yet): >= 5 ms off the worst single call of a routine
  player action** (inventory / PDA open, jump, land), per-call max from the hitch profiler over >= 5 repeats.
- **Instrument: the I-048 profiler** (`profiler_overlay` request key), 4 repeats x 120 s, drop round 1. For hitches the
  `alao-profiler-hitch` / `alao-profiler-hitch-listeners-inv` overlays and `profile_report.py --queue <id> --listeners --hitch`.
- **When warm and all-rounds deltas disagree, quote both** (I-061, arm drift).
- **Attended captures: do nothing until the 30 s warm-up is over**, and finish ~30 s before the end. The profiler's first
  30 s window is dropped and the last partial window never reaches the log. Hitch stats are run-scoped and survive the
  drop, but only if the first inventory / PDA open of the session happens inside the capture.
- **Cost that sits engine-side gets no microbench.** Count crossings (deterministic, lock-independent) and go to the
  profiler. Price cheap getters at 0.25 us.
- **Benches: check the locks before AND after**; `jit.off()` (no args) is the global switch.
- **Keep the box quiet during captures.** `coord queue hold <id>|--all` / `release` now parks items officially.
- Multi-flag gate: `tools/corpus_matrix.py`. Second gate corpus is `extracted/vanilla_db` (826 files).
- Live copy of a script = top enabled mod, else loose `Anomaly/gamedata/scripts`, else db. GAMMA runs Modded Exes:
  `_g_patches.script` overrides `_g.script` globals (`ini_file_ex`, `SYS_GetParam` are uncached there).
- `fps_runner.py` did not change in gen-5; no restart needed.

## The gen-6 beam

| Idea | Deciding question | Instrument |
|---|---|---|
| **I-057 moving run** | Does the `demonized_ledge_grabbing` row collapse from ~68 us to under 1 while moving? `lab/coord/i057-moving-request.json` is ready, **needs the user playing** (same style as the I-053 exploratory run). | profiler, listener mode |
| **I-058 attended hitch run** | First open vs every open for `ui_inventory.script:93`; per-call max for jump / land / leave-dialog. `lab/coord/overlays/i058-hitch-request.json` is ready, **needs the user at the keyboard** with the scripted routine in its notes. Then implement the two cuts (prewarm `UIInventory()`, memoise `sound_object`s) as patchers and re-measure. | hitch profiler |
| **I-053** a locked moving / busy-hub save | Still **needs the user to make the save.** Unlocks a locked number for I-057 track A and sizing for I-062. | profiler A/A first |
| **I-061** arm drift | A/A on `gen4-all-b`, 6 rounds, then swapped arm order: session age or arm order? Unattended. | profiler |
| **I-062** engine-called globals | `visual_memory_manager.get_visible_value` (8 uncached MCM reads per NPC visibility evaluation) is invisible to the profiler. Wrap a short list of engine-called globals; size it in a crowd. After I-053. | profiler extension |
| **I-060** orphaned cache references | Strict-xfail repro for `_apply_edits` dropping a `repeated_*` declaration but keeping its replacements, then group them both ways. | unit + corpus matrix |
| **I-051 publishing** | Unchanged: mod folder, PR draft, `lab/docs/upstream.md`. Add the I-056 vanilla `r_value` / `remove_line` note. The I-057 ledge patch and the I-058 cuts are more patches to other people's mods. **The user decides whether and where.** | |

## Organizer notes (carried over, still true)

- pytest with `-p no:cacheprovider --basetemp=<scratch>`; agents share the organizer's scratchpad, so use per-agent subdirectories.
- Bash tool 10-minute cap kills lock waits; launch long `coord run` jobs in the background.
- Merge order that was conflict-free in gen-5: I-055, I-059, I-058, I-057, I-056.
- Files are CRLF, `lab/data/ideas.json` is indent 2 with `ensure_ascii`; rewrite it in that format or the diff is the whole file.
- Long heredocs with quotes get truncated by the Bash tool; write scripts with the Write tool.
- A still-running `fps_runner.py` from an earlier session will pick up queue items by itself.

## Housekeeping still open

- `extracted/_work` and `lab/coord/overlays/agent-I0[0-4]*`, `ref-alao-*`, `vanilla-db-bottom` (stale) are deletable.
- `lab/data/runs` and `lab/data/corpus/*` run dirs are the raw evidence; archive rather than delete. Note `lab/data/corpus/<run_id>/`
  dirs show up untracked in `git status` (only `diffs/` and `alao-report.json` are ignored).
- Gen-4 and gen-5 agent worktrees under `.claude/worktrees/` and branches `agent/gen4-*`, `agent/gen5-*`, `worktree-agent-*` can go once the merge is pushed.
