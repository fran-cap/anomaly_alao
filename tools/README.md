# tools/

Helper scripts. The `corpus_*` trio is the regression harness: extract a scratch
corpus out of the (read-only) game install, run ALAO over it, record everything
in the lab's contract schema, then diff two runs.

Everything here runs on `py -3.12` (that's the interpreter with `luaparser`,
`jinja2` and `lupa`). `lupa` bundles LuaJIT 2.0, used to compile-check ALAO's
rewrites - install it with `py -3.12 -m pip install lupa`.

**Rule number one: the game install is read-only.** Never point `--fix` at
`D:\GOG_Games\Gamma\S.T.A.L.K.E.R. GAMMA`. `corpus_extract.py` copies scripts
out, `corpus_run.py` copies the corpus again before touching anything, and both
refuse to write inside the install.

---

## corpus_extract.py

Builds a scratch corpus into `extracted/<name>/` (gitignored), keeping the MO2
layout `<Mod>/gamedata/scripts/...` so ALAO's normal discovery works unchanged.

```bash
py -3.12 tools/corpus_extract.py --corpus gamma      # enabled mods only
py -3.12 tools/corpus_extract.py --corpus vanilla    # Anomaly/gamedata/scripts
```

| flag | meaning |
|---|---|
| `--corpus gamma\|vanilla` | required. `gamma` reads `GAMMA/profiles/<profile>/modlist.txt` and copies only ENABLED mods (`+` enabled, `-` disabled, `*` separator). `vanilla` copies `Anomaly/gamedata/scripts` as one pseudo-mod `VANILLA_SCRIPTS`. |
| `--install PATH` | game install root (default `D:\GOG_Games\Gamma\S.T.A.L.K.E.R. GAMMA`) |
| `--profile NAME` | MO2 profile, gamma only (default `G.A.M.M.A`) |
| `--out PATH` / `--name N` | destination (default `extracted/<corpus>`) |
| `--include-disabled` | also copy mods disabled in the profile |
| `--force` | wipe an existing destination |

Writes `corpus_manifest.json` in the destination: mod/file/byte totals, an
encoding histogram from `models.detect_file_encoding`, and per file its mod,
relative path, source path, size, sha256 and encoding.

> Heads up: the modlist parser is a small reimplementation of the lab's
> `framework/aalo/mo2.py` so this tool has no dependency on the lab checkout.
> Same prefix rules, including the `_separator` name suffix.

---

## corpus_run.py

Runs ALAO over a corpus and writes a contract-shaped run into
`C:\code\GIT\anomaly_alao\lab\data\corpus\<run_id>\`.

```bash
# analyze only
py -3.12 tools/corpus_run.py --corpus extracted/gamma --corpus-name gamma-0.9.4

# analyze, then fix a working copy
py -3.12 tools/corpus_run.py --corpus extracted/vanilla --corpus-name vanilla-1.5.3 \
    --fix-flags "--fix --fix-debug --fix-nil" --jobs 8
```

What a run does:

1. copies the corpus to `extracted/_work/<run_id>/work` (the corpus is never modified)
2. runs `stalker_lua_lint.py <work> --report <run>/alao-report.json` as a subprocess, timing it -> `analyze_s`
3. folds the report JSON into `findings_by_pattern` / `findings_by_severity`
4. **failure attribution**: reads `parse_failures` / `timeouts` / `crashes` straight
   out of `alao-report.json` (ALAO publishes them since I-029). Only falls back to
   re-running `analyze_file_worker` over every file when the report has no failure
   keys, i.e. against an older ALAO. `--probe` forces that duplicate pass anyway and
   cross-checks the two, recording the result in `extra.probe_vs_report`.
5. if `--fix-flags` is set, re-runs ALAO with those flags on the same working copy,
   with its own `--report alao-fix-report.json`
6. `files_modified` = files that got a sibling `.alao-bak`; `edits_applied` and
   `edits_dropped_overlap` come from that report's `edits_totals`
7. compile-checks every rewritten file with LuaJIT 2.0 via lupa -> `compile_failures_after_fix`.
   ALAO verifies before writing since I-004, so rewrites it *refused* to write show up
   in the fix report's `compile_failures` and are folded into the same list (and into
   `extra.compile_failures_refused_by_alao`). This external pass stays as the
   independent check on what did get written.
8. copies the fixed tree, deletes the `.alao-bak` files, fixes again, and compares bytes -> `idempotence_violations`
9. writes `manifest.json`, `results.json`, `diffs/` (unified diffs of the first 50 modified files), `alao-report.json`, and `analyze.log` / `fix.log` / `fix-pass2.log`

| flag | meaning |
|---|---|
| `--corpus PATH` | required. An extracted corpus dir. |
| `--corpus-name ID` | contract corpus id, e.g. `gamma-0.9.4`, `vanilla-1.5.3`, `fixtures` |
| `--label SLUG` | run_id slug (default derived from the corpus name) |
| `--fix-flags "..."` | fix flags to apply after analyze. Empty = analyze only. **A single flag needs the `=` form** - `--fix-flags=--fix`, because argparse eats a bare `--fix` as an option of its own. Multi-flag strings like `--fix-flags "--fix --fix-debug"` are fine. |
| `--analyze-args "..."` | extra args for the analyze pass |
| `--timeout S` | ALAO's per-file timeout (default 10) |
| `--jobs N` / `-j N` | workers for the fix pass and the probe (default min(8, cpus)) |
| `--single-thread` | pass `--single-thread` to the fix pass instead of `-j` |
| `--proc-timeout S` | wall-clock limit per ALAO subprocess (default 7200) |
| `--use-repo-exclude` | honour `alao_exclude.txt`. **Off by default** - it ships empty since I-036, but it used to exclude `VANILLA_SCRIPTS` and a local edit of it must never quietly shrink a regression corpus. |
| `--no-probe` | skip failure attribution (counts only, from stdout) |
| `--no-idempotence` | skip the second fix pass |
| `--no-capture-gate` | skip G9, the I-046 shadowing scan over the originals |
| `--keep-work` | keep the working copies (needed to inspect a violation afterwards) |
| `--out-root PATH` | default `C:\code\GIT\anomaly_alao\lab\data\corpus` |

### Why step 4 used to re-run the whole analyzer (closed by I-029)

All four gaps below are fixed; the probe pass survives only as `--probe`, for
cross-checking and for running against an older ALAO.

- ~~The JSON report contains **only findings**.~~ It now carries `parse_failures`,
  `timeouts`, `crashes`, `compile_failures`, `findings_by_pattern`,
  `findings_by_severity`, per-file `edits` and `edits_totals`, plus
  `alao_version` and `flags`.
- ~~ALAO's stdout prints only totals; the per-file lines need `-v`~~ - failure
  lines print unconditionally now, with the **full path**, so the dozen mods that
  all ship `ui_inventory.script` can be told apart (I-037).
- ~~`Files with parse errors` lumps timeouts in with syntax errors~~ - a timeout
  has its own counter and its own summary line (I-035).
- ~~Nothing reports how many edits `_apply_edits` dropped for overlap~~ -
  `edits_dropped_overlap` is a real number now, per file and in total.

One gap is *newly visible* rather than closed: ALAO used to return `[]` for a
file it could not parse, indistinguishable from a clean file, so the probe saw
nothing either. With `ASTAnalyzer.last_error` wired up, the enabled GAMMA corpus
turns out to have **3 files that do not parse** - they were being counted as
successfully analyzed all along.

The raw stdout counts are kept in `results.extra.stdout_counts` /
`extra.fix_stdout_counts` as a cross-check, and `extra.failure_source` records
whether the failures came from the report or the probe.

---

## corpus_matrix.py

Runs `corpus_run.py` once per fix-flag combination, sequentially, and prints one
gate table over the lot.

```bash
py -3.12 lab\coord\coord.py run corpus --ttl 3600 -- \
  py -3.12 tools/corpus_matrix.py --corpus C:\code\GIT\anomaly_alao\extracted\gamma \
      --corpus-name gamma-0.9.4 --label i052 --summary-json gamma-matrix.json
```

Why (I-052): **idempotence is a property of a flag combination, not of ALAO.**
Every gate up to gen-3 ran plain `--fix`, and both defects I-052 fixed were
invisible there - one needed `--fix-debug` (`sr_monster.script`, G5=1 in
`20260919-184338-vanilla-i044-fixdebug`), the other `--fix-nil` (24 GAMMA files
in `20260919-192746-gamma-i046`). One run per combination is the only way the
gate sees that class of bug.

| flag | meaning |
|---|---|
| `--combos a,b,c` | which combinations to run. Default `fix,fix-debug,fix-nil,fix-debug-nil`; `all` adds `fix-yellow`, `fix-experimental`, `fix-dead-code`, `everything`. |
| `--label SLUG` | each run is labelled `<slug>-<combo>`, so the run ids stay readable |
| `--summary-json PATH` | the gate table as JSON as well as on stdout |
| everything else | `--corpus`, `--corpus-name`, `--jobs`, `--keep-work`, `--notes`, `--out-root` pass straight through; trailing extra args go to `corpus_run.py` |

The table has one row per combination: run id, files modified, edits, **G4**
(compile failures), **G5** (idempotence violations), **G9** (captures), parse
failures, timeouts, crashes, findings. Exit code is non-zero if any of G4/G5/G9,
crashes, or a child return code is non-zero, so it works as a CI gate. Runs are
never parallel - G6 timings would be meaningless - so hold the `corpus` lock
around the whole matrix and expect gamma x 4 combos to take a while.

---

## corpus_compare.py

Diffs two runs and prints markdown.

```bash
py -3.12 tools/corpus_compare.py --latest
py -3.12 tools/corpus_compare.py --latest --corpus gamma-0.9.4
py -3.12 tools/corpus_compare.py <base_run_id> <new_run_id> --out ../reports/delta.md
py -3.12 tools/corpus_compare.py --list
```

Sections: run metadata, headline numbers (timings, totals, failures) with deltas,
findings by severity, per-pattern changes sorted by absolute delta, and
new/gone file lists for parse failures, timeouts, crashes, compile failures and
idempotence violations. `--latest` picks the two newest run dirs; pass
`--corpus` with it so you don't end up diffing vanilla against gamma (it warns
if the corpora differ, but the numbers are still meaningless).

---

## microbench.py + bench/

The corpus harness proves ALAO's rewrites *land* and *compile*. `microbench.py`
is the thing that proves they are *faster*, on the VM the game actually runs
(LuaJIT 2.0, via `lupa.luajit20`). It exists because the first hand-rolled
attempt at this skipped GC control and declared ALAO's highest-volume fix a 23%
regression - a wrong conclusion that survived until someone re-measured by hand.
So the protocol from `lab/docs/beam-ideas.md` section 2 is baked in and there is
no flag to turn any part of it off.

```bash
py -3.12 tools/microbench.py                                # all 24 pairs, ~18 s
py -3.12 tools/microbench.py --list
py -3.12 tools/microbench.py --pattern append_loop_counter --json out.json
py -3.12 tools/microbench.py --shipped                      # only what ALAO fixes today
py -3.12 tools/microbench.py --quick                        # tiny N smoke, NOT a measurement
py -3.12 tools/microbench.py --self-check                   # prove jit.off(f,true) works
```

Paths are resolved relative to the script, so you can run it from another
worktree by absolute path and it still finds its own `bench/`.

**Gate G2** (`>= 1.15x` in both modes, no mode below `0.98x`) is the `G2` column.
`speedup = t(original) / t(rewrite)`.

### What is enforced

| Element | Setting |
|---|---|
| VM | `lupa.luajit20`. `jit.version`, `version_num`, `arch`, `os` and the whole `jit.status()` flag list go into the JSON; a loud warning fires if the optimization flag set is not `fold cse dce fwd dse narrow loop abc sink fuse`, the set Anomaly's LuaJIT 2.0.4 reports. |
| Runtime | A fresh `LuaRuntime` per (case, arm, mode). Nothing is shared. |
| JIT off | `jit.off(f, true)` on the loaded chunk. A global `jit.off(true, true)` does **not** affect already-loaded chunks and silently measures JIT-on numbers, so a self-check runs first: an obviously jittable loop must be `>= 3x` slower interpreted, or the whole run aborts. It currently measures 24x. |
| Chunk | `local N, D, K = ...` prelude, `D` a 64-element float table (stops constant folding), `_G.__sink = <expr>` at the end (defeats DCE). |
| Warm-up | 2 calls at N=1000. |
| GC | `collectgarbage('collect')` immediately before every timed run. |
| Timing | `time.perf_counter()` on the Python side. `os.clock()` in Lua has ~10 ms resolution on Windows. |
| Reps | Best of 9, median also recorded. |
| N | 2e6 JIT on, 3e5 JIT off, per-snippet override with `@n`. |

One deliberate deviation from section 2: **`@setup` runs outside the timed
region**, by having the chunk return the measured work as a closure. Section 2
timed the whole chunk, which is fine when setup is two locals and lethal when it
is "build an N-element table" - setup then dominates and squashes every ratio
toward 1.00x. `jit.off(chunk, true)` is recursive, so the closure is covered;
`--self-check` uses the same machinery and would catch it if it were not.

### Writing a bench pair (< 1 minute)

Copy any `bench/*.lua`, change five things. The format is comment directives;
everything after a `-- @section` line until the next directive is Lua.

```lua
-- @pattern append_loop_counter          -- required; Finding.pattern_name, and the file name
-- @title t[#t+1]=v -> counter      -- required; one line for the table
-- @status proposed                 -- shipped | proposed (default proposed)
-- @doc 12.44 4.80                  -- optional: the beam-ideas s.2 figures, jit_on jit_off ('-' for none)
-- @n 200000 60000                  -- optional: total inner-iteration budget, jit_on jit_off
-- @iters 5 20 100 2000             -- optional: sweep inner loop length K
-- @doc_at 2000                     -- optional: which K the @doc figure refers to (default the largest)
-- @corpus_k 3-10?:15 68:3          -- optional: where this pattern runs, as <K range>[?]:<site count> buckets ('?' = range inferred, not measured)
-- @corpus_src 20260911-...-audit   -- required with @corpus_k: the run the counts came from
-- @notes anything; repeat the directive for more lines, they accumulate
-- @setup
local acc = 0                       -- runs per timed rep, UNTIMED. N, D, K are in scope.
-- @original
for r = 1, N do ... end             -- the code as mod authors write it
-- @rewrite
for r = 1, N do ... end             -- what ALAO produces (or would produce)
-- @sink
acc                                 -- an expression; assigned to _G.__sink so nothing is dead
```

The file name should match `@pattern`; `@pattern` should match
`Finding.pattern_name` where one exists, because that is what
`tests/test_microbench.py` checks coverage against.

### Loop-length sweeps

Several rewrites flip sign with loop length. `string_concat_in_loop` is **0.44x
at 3 iterations** and **16x at 2000** - a single huge-N number would have shipped
a regression into every short loop in the corpus. Add `@iters` to any pair whose
win plausibly depends on how long the loop runs; the harness then prints one row
per K, scales the outer repetition count so total work stays roughly constant,
and summarises the pattern as e.g. `passes for K >= 100` instead of a bare
pass/fail.

The `@doc` comparison only applies to the `@doc_at` row, since the doc has one
number per transform rather than a curve.

### `@corpus_k`: measure where the pattern actually runs

A speedup is a **function** of loop length. A scalar in a table is a claim that
the function is constant, and three of the four big rows in the beam's section-2
table turned out not to be. So the default assumption is inverted here: a row
earns a scalar by being shown flat over the range that matters, rather than
getting one by default and being caught later.

`@corpus_k` declares where the pattern runs, as **buckets of `<K range>:<site
count>`** — not as one range. Real corpora are bimodal: `string_concat_in_loop`
has 15 short UI builders at K=3-10 and an isolated spike of 3 literal
`for i=1,68` loops, nothing between. `3-68` would be true and useless, because it
loses the fact that the mass is at the bottom, and that is the thing that decides
a prune. Three things follow:

* Every bucket must contain an `@iters` point, or the snippet is **rejected at
  parse time**. Declaring where a pattern runs and then never measuring there is
  the whole defect; the harness will not let you do it quietly.
* `@corpus_src` is **required**, naming the run id or audit the counts came from.
  Otherwise the site count is a hand-entered number with exactly the trust
  problem of the scalar it replaces, and the rule above applies to it too.
* A trailing `?` on a bucket's range marks it **estimated** — the site count is
  measured, but *where those sites sit* is inference. See below; this is not
  decoration.
* The G2 verdict leads by **counting sites**:

  ```
  string_concat_in_loop: 15 of 18 corpus sites fail G2 (on an estimated loop
    length), 3 of 18 pass [20260911-111300-i039-audit]
    (15 at K=3-10 (estimated): fail; 3 at K=68: pass)
  ```

That sentence is what pruned the `string_concat_in_loop` promotion (I-039): the
beam's 8.69x was measured in the low hundreds, while 15 of the 18 rewritable
sites sit below every measured breakeven. Nothing about the 8.69x was *wrong* -
it was about a part of the curve the code mostly does not visit. That is a
different failure from the missing `collectgarbage`, which made a number
incorrect: this one makes a correct number irrelevant, and it is the harder of
the two to notice, because nothing about the measurement looks off.

**Why `?` exists.** The two buckets above look identical in the syntax, are cited
to the same run, and have different evidential status. `68:3` is measured: three
`for i=1,68` loops with literal bounds read off the source. `3-10?:15` is not —
what the run established is that those fifteen trip counts are *statically
unknowable* (the bounds are `#p-1`, `size_table(warnings)`, `pairs()` over
inventory tables), and `3-10` is a judgement about what those tables hold in
Anomaly. Good inference; not a measurement; the run id does not back it. Without
the `?`, the audit trail would point at that run as though it had said otherwise,
and if someone later instruments the game and finds one of those `pairs()` loops
running 300 times on a modded inventory, the verdict flips for that site. This is
the same defect as everything else on this page, one layer in: a number that is
correct, is sourced, and is not the kind of thing its source establishes. A
generated line should not print inference and measurement in the same typeface.

Note what the verdict does **not** say. An earlier version of this feature
declared the corpus range as `3-10` and printed "passes only at K in [.., 68, ..],
which this pattern does not reach in the corpus" - which was false, since ALAO
does rewrite those three K=68 sites and they clear G2. A verdict that overstates
to FAIL is *weaker* than one that concedes the wins and counts the losses,
because the overstatement is the part a reader can check and falsify. The
generated line is only worth having if it can be trusted without chasing commits.

---

## script_extractor.py / split_test.py

Older helpers, predating the corpus harness. `script_extractor.py` copies all
`.script` files out of a mods tree preserving structure; `split_test.py` splits
an extracted tree into zip chunks of N mods for batch testing. `corpus_extract.py`
supersedes the first one for regression work (it filters by MO2 enablement and
writes a hashed manifest).
