# lab/coord — working in parallel on ALAO without stepping on each other

Several agents evaluate beam ideas at once, each in its own git worktree. This folder is the
shared ground between them: locks for things that exist once on the machine, a queue for
in-game FPS runs (which need an elevated shell and the game exe, one at a time), and a status
board so the organizer can see where everyone is.

Everything here is addressed by **absolute path into the main checkout**, never relative to a
worktree, because the runtime state (`locks/`, `queue/`, `status/`, `overlays/`) is gitignored
and would not exist in a worktree:

```
set ALAO_AGENT=agent-I001                      # your name; every coord call defaults --owner/--agent to it
py -3.12 C:\code\GIT\anomaly_alao\lab\coord\coord.py board
```

## What is contested, and which lock covers it

| Resource | Why it is contested | Lock |
|---|---|---|
| The game: `ModOrganizer.exe`, `AnomalyDX11AVX.exe`, PresentMon's ETW session, `user.ltx`, `GAMMA/profiles/aalo-*`, `GAMMA/mods/aalo-rewrite-*` | one game process at a time; the harness edits and restores config around each run | `game` (held only by `fps_runner.py`) |
| Timed corpus runs (`tools/corpus_run.py`) | G6 timing is meaningless if two 8-worker runs overlap; run ids are second-resolution | `corpus` |
| `lab/data/ideas.json`, `lab/docs/beam-ideas.md` | one writer; they are tracked in git and would merge-conflict across worktrees | `ideas` (organizer only) |
| `extracted/gamma`, `extracted/vanilla` (read-only shared corpus source) | regenerating it under someone's run | `extract` (only if you must re-extract; you should not need to) |

Not contested, no lock needed: your worktree, `extracted/_work/` under your worktree,
`lab/data/corpus/<run_id>/` (each run gets its own dir), `lab/coord/overlays/<your-name>-*`,
`lab/coord/status/<your-name>.json`.

```
coord run corpus --ttl 900 -- py -3.12 tools\corpus_run.py --corpus C:\code\GIT\anomaly_alao\extracted\gamma --corpus-name gamma-0.9.4 --fix-flags=--fix --keep-work
```
`coord run` acquires the lock (waiting up to 30 min by default), heartbeats while the command
runs, releases when it exits, and passes the exit code through. A lease that stops being
heartbeated goes stale after its ttl and the next acquirer breaks it, so a crashed holder never
wedges the team.

## Worktree gotchas

- `extracted/` is gitignored, so it is **absent** in your worktree. Point `--corpus` at
  `C:\code\GIT\anomaly_alao\extracted\gamma` (or `...\vanilla`). It is a read-only source;
  `corpus_run.py` copies it into `<your worktree>\extracted\_work\<run_id>\` before touching it.
- `corpus_run.py --out-root` already defaults to the main checkout's `lab\data\corpus`, so runs
  from every worktree land in one place and `tools\corpus_compare.py` can diff across agents.
  The baseline to compare against is `20260910-230310-gamma-fix` (post I-008, 0 idempotence
  violations). `git_state()` records your worktree's commit in the manifest.
- **Do not edit `lab/data/ideas.json` or `lab/docs/beam-ideas.md`** in your worktree. Report
  via `coord post` and your final message; the organizer updates the beam under the `ideas` lock.
- `lupa`, `luaparser`, `pytest` are installed for `py -3.12` machine-wide. Use `py -3.12` only.
- Commit in your worktree on your branch as you go. Never push, never touch `main`.
- The game install `D:\GOG_Games\Gamma\S.T.A.L.K.E.R. GAMMA` is read-only for you. Never point
  `--fix` at it. The only thing that writes there is `fps_runner.py`, only under
  `GAMMA/mods/aalo-rewrite-*` and `GAMMA/profiles/aalo-*`, and it removes both afterwards.

## Status board

Post at every phase change so the organizer does not have to ask:

```
coord post --idea I-001 --phase bench   --msg "counter append 12.1x JIT / 4.6x interp, protocol s.2"
coord post --idea I-001 --phase impl    --msg "analyzer+transformer done, 14 new tests green"
coord post --idea I-001 --phase corpus  --msg "run 20260911-1201xx-gamma-fix: G4 0/520, G5 0, G6 +3%, G7 clean"
coord post --idea I-001 --phase fps-req --msg "queued 20260911-...-I-001-xxxxxx"
coord post --idea I-001 --phase blocked --msg "need X from the organizer"
```

Phases: `start`, `bench`, `impl`, `tests`, `corpus`, `overlay`, `fps-req`, `blocked`, `done`, `pruned`.

## Requesting an in-game FPS run

Corpus gates (G4-G8) prove a change is *safe*; only the game proves it *helps*. When your idea
passes the corpus gates and the microbenchmark says the win is real, build two overlays and
queue a request. The runner drains the queue from an elevated shell, one item at a time, under
the `game` lock; you will see the result on the board and in the queue item.

1. Produce fixed trees. `corpus_run.py --keep-work` leaves one at
   `<worktree>\extracted\_work\<run_id>\work`. For the *baseline* arm you usually want ALAO
   **without** your idea (so the delta isolates it): run the same command on `main`'s code, or
   with your new pattern disabled. `null` baseline = stock game, which measures all of ALAO.
2. Build overlays into the shared folder (the runner must find them after your worktree is gone):
   ```
   py -3.12 C:\code\GIT\anomaly_alao\lab\coord\build_overlay.py --work <fixed tree> --out C:\code\GIT\anomaly_alao\lab\coord\overlays\agent-I001-b
   ```
   It walks the live modlist in priority order and takes a rewritten file **only if that copy
   is the one the game loads**. Read the printed counts: on the current corpus 290 of 513
   rewritten files are live winners and 223 are shadowed by a higher-priority mod.
3. Write the request and submit it:
   ```json
   {
     "label": "counter-append",
     "variant_overlay":  "C:/code/GIT/anomaly_alao/lab/coord/overlays/agent-I001-b",
     "baseline_overlay": "C:/code/GIT/anomaly_alao/lab/coord/overlays/agent-I001-a",
     "repeats": 3, "duration_s": 300, "warmup_s": 30, "save": "gammabaseline",
     "notes": "what differs between the arms, in one line"
   }
   ```
   ```
   coord queue submit --idea I-001 --file request.json [--priority 3]
   ```
   Priority 1 runs first, 9 last; default 5. One experiment is 3 repeats x 2 arms x
   (~90 s load + 30 s warm-up + 300 s measure), roughly 40 minutes, so do not queue speculatively.
4. `coord queue show <id>` when it is done: `result.arms.{baseline,variant}` carry mean fps_avg,
   fps_1pct_low, frametime_p99_ms and the run ids under `lab/data/runs/`; `result.delta` has the
   differences and percentages. The first real baseline (stock GAMMA, standing still) was
   212 fps avg / 147 fps 1% low / 6.1 ms p99 with per-30 s windows within 207-215, so treat a
   delta inside +-2% avg as noise unless the 1% low moves with it.

## Draining the queue (organizer / whoever has the elevated shell)

```
py -3.12 C:\code\GIT\anomaly_alao\lab\coord\fps_runner.py --dry-run --once   # pipeline check, no game
py -3.12 C:\code\GIT\anomaly_alao\lab\coord\fps_runner.py                    # elevated: drain until empty
py -3.12 C:\code\GIT\anomaly_alao\lab\coord\fps_runner.py --watch 120        # elevated: keep polling
```
Close RTSS / Afterburner first (both hook the game and fight PresentMon). The runner refuses to
start un-elevated unless `--dry-run`.

## Files

| file | role |
|---|---|
| `coord.py` | locks (`lock`, `run`), queue (`queue`), status board (`post`, `board`). Importable too: `coord.held(name, owner)` context manager. |
| `build_overlay.py` | fixed MO2-layout tree -> one overlay mod containing only the rewritten files the live profile actually loads |
| `fps_runner.py` | queue drainer: installs overlays as `aalo-rewrite-*` mods, clones the profile to `aalo-src-*`, writes an experiment TOML, runs `aalo run --experiment`, summarizes, cleans up |
| `locks/ queue/ status/ overlays/` | runtime state, gitignored |
