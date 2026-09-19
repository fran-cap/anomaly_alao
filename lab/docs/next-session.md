# Next session: generation 3

Written 2026-09-11 at the end of the gen-2 run. Read this, `lab/coord/README.md` and
`beam-ideas.md` sections 8 and 9 before starting anything.

## Locked in (do not re-measure)

| arm | fps avg | 1% low | p99 |
|---|---|---|---|
| stock GAMMA, `gammabaseline` save, standing still | 209.7 | 166.9 | 5.53 ms |
| full ALAO (gen-1 code) = merged mod rewrites on top + rewritten vanilla `scripts.db0` at bottom | 215.2 | 170.7 | 5.41 ms |

Gen-2 added two deltas on top of full ALAO, both null and both predicted null from site arithmetic
before they ran: I-021 +0.35% (`20260911-153413-I-021-392bd3`), I-040 +0.09%
(`20260911-164718-I-040-ec27c2`). Per-round spread on identical arms is 203-222 fps. The
standing-still FPS capture cannot resolve anything below roughly 2% of a frame, and every
per-frame rewrite ALAO can do today is worth ~0.01%. **Do not queue an FPS delta for a pattern
rewrite again unless the site arithmetic says >0.5% of frame time.**

(Bar lowered from 1% to 0.5% by the user on 2026-09-19: we are after gradual gains. FPS still cannot
resolve 0.5%, so anything between 0.5% and ~2% has to be read in script-ms through the I-048 profiler.)

Reference overlays `lab/coord/overlays/ref-alao-merged-b` and `vanilla-db-bottom` were built from
gen-1 code and no longer match `--fix` output. Rebuild them from a fresh integration corpus run
before the first in-game request of gen-3 (`build_overlay.py --work <fix tree>`).

## What gen-2 established (the short version)

1. **Reach, not speed, is the limit.** 74 of 103 live vanilla per-frame bodies have zero
   actionable findings; the hot live code sits one hop out from the name-based per-frame
   classifier (`axr_main.make_callback`, `xr_logic.pick_section_from_condlist`). Two agents hit
   this wall independently (I-041, I-005).
2. **The safety gates are blind to a whole bug class.** Three pre-existing silent-miscompile bugs
   in shipped GREEN caching were found by agents reading their own diffs. All three compile and
   are idempotent. Any audit built on grep or difflib under-reports; the scope-aware scanner
   (`lab/tools/i021_capture_scan.py`) is the only method that found all of them.
3. **The instrument is wrong for the question.** FPS resolves the frame; ALAO changes the script
   slice of the frame. The engine exposes `profile_timer` to Lua (`lua_help.script:1200`), so
   script time can be measured directly in-game.
4. **Report noise is largely gone** (GAMMA findings 15060 -> 10688 with byte-identical output),
   which makes the remaining RED / YELLOW families worth reading again.

## The gen-3 beam: five agents

Pick these, one agent each, own worktree, `ALAO_AGENT` set. Each idea's entry in `ideas.json`
has the hypothesis, change and measure; the line below is the deciding question.

| Agent | Idea | Deciding question | Instrument |
|---|---|---|---|
| A | **I-048 script-side profiler harness** (new) | Can a `profile_timer` wrapper around `axr_main.make_callback` dispatch (and the actor/NPC update binders) report per-callback Lua ms per frame with <5% run-to-run spread on the standing-still save? If yes, every later rewrite is measured in script-ms, not fps. | in-game, `game` lock, needs its own overlay mod that only adds the profiler; baseline = full ALAO |
| B | **I-046 shadowing-assertion gate** | Turn `i021_capture_scan.py` into a `corpus_run.py` gate (G9) and a pytest guard that fails on the `18756a9` and `tasks_fetch` repros at `bae4b0c`. Then extend it from cache declarations to every insertion ALAO makes (`local mfloor = ...`, `local n = #t`, sqrt aliases). | corpus, no game |
| C | **I-042 call-graph-aware per-frame classifier** | Propagating per-frame status one hop along same-file and `module.func` edges: how many interpreted functions enter the set, and do the I-040 / I-021 / debug-statement site counts inside it rise? Include the two `ENGINE_NYI_METHODS` gaps (`section_name`, `profile_name`). Report-only, like I-013. Run the census on GAMMA mods too, not only vanilla (I-041 was vanilla-only). | corpus, no game |
| D | **I-043 `axr_main.make_callback` dispatch** | Microbench the `spairs` dispatcher shape (pairs + `table.sort` + closure per call) against a sorted parallel array at 5 / 20 / 60 listeners, interpreted. If >2x, hand-patch `axr_main.script` as a one-off overlay and measure with agent A's profiler (fps as a fallback only). Not a table-driven pattern; needs its own transform or stays a hand patch. | microbench first; in-game only via A |
| E | **I-044 `--fix-debug` in-game arm** | Count `debug_statement` findings inside live per-frame bodies on GAMMA mods (vanilla has 34; the mod side is unknown). If the count times per-call cost clears 0.5% of frame time, build overlays from a `--fix --fix-debug` run of the merged code and queue one delta with 5 repeats. Otherwise report the count and stop. | corpus, then maybe game |

Do not start **I-017** (corpus-scale differential execution) as an agent this generation: it is
scored 9.0 but is a multi-day harness, and I-046 catches the shape I-017 cannot. Scope it as a
plan document instead, if anyone has spare time.

## The loop per idea (unchanged, with gen-2 additions)

1. **Lua first, offline.** `tools/microbench.py --pattern <name>` with an `--iters` sweep; quote the
   K at which G2 passes; never quote a number taken while the `game` lock is held (ratios drop
   20-75% during a capture). Validate any new scanner or census tool against a **known positive**
   before quoting its zero.
2. **Corpus gates** under the `corpus` lock, both corpora (`extracted/gamma`, `extracted/vanilla_db`):
   G4 0 compile failures, G5 0 idempotence, G6 within 10%, G7 subset, G8 tests green, and from
   gen-3 **G9: 0 captures from `lab/tools/i021_capture_scan.py`** on the fix tree. Baselines are
   `20260911-175058-gamma-integ-gen2` and `20260911-175201-vanilla-integ-gen2`. Report the
   `jit_mode` split and the live-after-shadowing count.
3. **In-game only through the profiler (agent A) or when site arithmetic clears 0.5% of frame time.**

## Organizer notes

- Run pytest with `-p no:cacheprovider --basetemp=<scratch>`; the shared temp dir throws
  PermissionError when several agents run the suite at once.
- Agents write scratch files under a per-agent subdirectory of the scratchpad.
- The Bash tool's 10-minute cap kills backgrounded lock waits; launch long `coord run` jobs
  detached (PowerShell `Start-Process`) and Git Bash rewrites `/c` to `C:/`, so pass `cmd /c` via
  PowerShell, not bash.
- Merge order that was conflict-light in gen-2: docs and report-only branches first, then the
  analyzer branches; conflicts were additive in `reset()` and `_analyze_repeated_calls_in_scope()`.
- `corpus_compare.py` warns "different corpora" for vanilla runs because the baseline was labelled
  `vanilla-1.5.3`; it is a label mismatch only.

## Kickoff prompt for the next session

> Read lab/docs/next-session.md, lab/coord/README.md and beam-ideas.md sections 8-9. Spin up a
> team of Opus agents (one per idea, own worktree, `ALAO_AGENT` set) on the gen-3 beam: I-048
> (script-side profiler harness), I-046 (shadowing-assertion gate, G9), I-042 (call-graph
> per-frame classifier, GAMMA census included), I-043 (make_callback dispatch, microbench then
> hand patch measured by the profiler), I-044 (--fix-debug arm, count first). Each agent validates
> its tools against a known positive, runs corpus gates on both corpora under the corpus lock, and
> queues in-game work only through the profiler or when site arithmetic clears 0.5% of a frame. You
> are the organizer: rebuild the reference overlays from a fresh integration run first, relay
> cross-cutting findings, keep ideas.json / beam-ideas.md under the ideas lock, merge on an
> integration branch with the full suite, both corpus runs and the capture scan as the gate, and
> close with verdicts and the single deciding fact per idea. Do not re-measure the locked baselines.

## Housekeeping still open

- `extracted/_work` (236 MB) and `lab/coord/overlays/agent-*` are deletable; `agent-I040-b` holds
  broken output and must not be reused.
- `lab/data/runs` (686 MB) is the raw FPS evidence; gitignored on purpose, archive rather than delete.
- I-014 (stale shipped report examples) and I-036 (`alao_exclude.txt` ships with VANILLA_SCRIPTS)
  are half-hour fixes nobody has picked up.
