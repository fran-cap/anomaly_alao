# Next session: generation 2

Written 2026-09-11 at the end of the gen-1 run. Read this, `lab/coord/README.md` and
`beam-ideas.md` section 8 before starting anything.

## Locked in (do not re-measure)

| arm | fps avg | 1% low | p99 |
|---|---|---|---|
| stock GAMMA, `gammabaseline` save, standing still | 209.7 | 166.9 | 5.53 ms |
| **full ALAO** = merged mod rewrites on top + rewritten vanilla `scripts.db0` at bottom | 215.2 | 170.7 | 5.41 ms |

Queue items `20260911-122705-REF-5954d8` (mods only: 0.0%) and `20260911-141814-VANILLA-9dd6ab`
(full: +2.6% avg, suggestive). Launch-to-launch spread is 206-213 on identical stock, windows
inside a round are flat within 1 fps. **From now on every in-game request is a delta**:
`baseline_overlay` = the full-ALAO overlays, `variant_overlay` = full ALAO plus the idea. Anything
inside +-2% avg without the 1% low moving with it is noise; use 5-6 repeats when it matters.

Overlays to reuse (rebuild them if `--fix` output changes): `lab/coord/overlays/ref-alao-merged-b`
(top) and `lab/coord/overlays/vanilla-db-bottom` (bottom). Corpus sources: `extracted/gamma`
(1503 files) and `extracted/vanilla_db` (413 files, unpacked from `scripts.db0`).

## The loop per idea

1. **Lua first, offline.** `tools/microbench.py --pattern <name>` with an `@iters` sweep; quote the
   K at which G2 passes. Anything whose win depends on loop length is quoted with K or not at all.
2. **Corpus gates** under the `corpus` lock (`coord run corpus -- py -3.12 tools/corpus_run.py ...`),
   both corpora: G4 0 compile failures, G5 0 idempotence, G6 within 10%, G7 subset, G8 tests green.
   Also report the `jit_mode` split of the sites and how many are live after MO2 shadowing
   (`build_overlay.py` prints it).
3. **In-game only if the overlay diff touches live per-frame code** the standing-still save runs.
   Gen-1 skipped two experiments on that rule and was right both times.

## What to chase (from the gen-1 evidence)

- **`time_global()`-throttled per-frame bodies** (186 abort sites, the top trace killer). Idea: hoist
  the engine call out of the hot path, or cache per frame; measure interpreted, it will not compile.
- **Engine-call hoisting in the 183 interpreted per-frame bodies**: repeated `db.actor`, `:position()`,
  `:id()`, `:section()` inside one body (I-021 widening `EXPENSIVE_INDEXES` is the cheap start).
- **`pairs` -> `ipairs` (I-005)** gated by the I-013 classifier: 5.9x compiled, 0.29x interpreted, and
  78% of per-frame `pairs` sites are interpreted. Only the compiled 22% may take it.
- **`math.sqrt` retarget (I-012)**: 3-4x interpreted, 25 sites, cheap, do it.
- **`global_write` grouping (I-031)**: 5697 RED findings drowning the report.
- **Vanilla-first**: the +2.6% came from 111 vanilla scripts; per-frame bodies in vanilla
  (`xr_motivator`, `bind_stalker`, `xr_logic`, `sr_*`) deserve their own census.

## Kickoff prompt for the next session

> Read lab/docs/next-session.md, lab/coord/README.md and beam-ideas.md section 8. Spin up a team of
> Opus agents (one per idea, own worktree, `ALAO_AGENT` set) on generation-2 ideas: [pick 4-6 from
> the "what to chase" list or the beam]. Each agent: microbench with an iteration sweep, corpus gates
> on both `extracted/gamma` and `extracted/vanilla_db` under the `corpus` lock, report the jit_mode
> split and live-after-shadowing count, and queue an in-game DELTA request (baseline = full-ALAO
> overlays, variant = full ALAO + idea) only if the diff touches live per-frame code. You are the
> organizer: relay cross-cutting findings between agents, keep `ideas.json`/`beam-ideas.md` under the
> `ideas` lock, merge on an integration branch with the full suite and a corpus run as the gate, and
> close with verdicts and the single deciding fact per idea. Do not re-measure the stock or full-ALAO
> baselines; they are locked in next-session.md.

## Housekeeping still open

- `corpus_compare.py` severity totals do not reconcile exactly with per-pattern deltas after I-029.
- The strict xfail for `xr_logic.script:741` (nested `string_find_plain` under `table_insert_append`)
  is the I-019 fix waiting to happen.
- Branch `lab/tests-and-corpus-tooling` is ~50 commits ahead of origin and nothing is pushed.
