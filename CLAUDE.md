# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this is

ALAO (Anomaly Lua Auto Optimizer) is a Python CLI that parses S.T.A.L.K.E.R. Anomaly mod scripts
(Lua 5.1 / LuaJIT 2.0.4, files named `*.script` or `*.lua`) into an AST, reports performance and
safety issues, and optionally rewrites the source in place with safe optimizations. It targets
Mod Organizer 2 layouts (`<mods>/<ModName>/gamedata/scripts/*.script`).

No package layout, no build step. The optimizer itself is flat top-level modules run directly.
There IS a test suite now (`tests/`, pytest) plus corpus regression tooling (`tools/corpus_*.py`)
and a lab (`lab/`) with a dashboard, an in-game FPS harness, the improvement-idea beam, and reports.
See "Tests, tools and lab" below.

## Setup and running

```bash
pip install -r requirements.txt        # luaparser>=3.0.0, jinja2>=3.0.0 (Python 3.8+)

# Analyze only (default): prints a summary, writes nothing
python stalker_lua_lint.py <path_to_mods>

# Common combos
python stalker_lua_lint.py <mods> --report report.html            # .txt / .html / .json by extension
python stalker_lua_lint.py <mods> --fix --fix-debug --fix-nil     # rewrite scripts (creates .alao-bak files)
python stalker_lua_lint.py <mods> --revert                        # restore every .alao-bak
python stalker_lua_lint.py <some_dir> --direct                    # loose scripts, no gamedata/scripts layout
python stalker_lua_lint.py <mods> --single-thread -v              # debugging: no multiprocessing, full tracebacks
```

The full option list is in the module docstring of `stalker_lua_lint.py` and in `README.md`.
`luaparser` is NOT installed on this machine's default Python (3.13) as of the initial clone;
install requirements before running anything.

To verify a change, run `py -3.12 -m pytest -q` (see below) and, for anything touching the
analyzer or transformer, a corpus run with `tools/corpus_run.py`. Manual spot checks still work:
run the CLI against a scratch copy of some scripts (use `--direct` on a folder of `.script`
files, or `--single-thread` for tracebacks) and diff the result against the `.alao-bak` originals. Never point `--fix` at a real mods folder you
have not backed up. `--clean-backups` is destructive.

## Architecture

Pipeline: **discover -> analyze (per file) -> findings -> transform (per file) / report**.

| File | Role |
|------|------|
| `stalker_lua_lint.py` | Entry point. Arg parsing, mod discovery, exclude handling, backup/revert/list/clean ops, multiprocessing pools for analyze and fix phases, per-file timeout, summary printing. ~1000 lines, all in `main()`. |
| `discovery.py` | `discover_mods()` (MO2 layout, also accepts a bare `gamedata` dir or a single mod) and `discover_direct()` (`--direct`). Returns `{mod_name: [Path, ...]}`. |
| `models.py` | `Finding` dataclass (`pattern_name`, `severity`, `line_num`, `message`, `details`, `source_line`) and `detect_file_encoding()` (UTF-8 BOM -> UTF-8 -> CP1251 with Cyrillic heuristic -> latin-1). |
| `ast_analyzer.py` | `ASTAnalyzer`: a hand-rolled visitor over `luaparser` AST nodes. Walks once collecting calls, indexes, assigns, scopes, aliases, nil sources, etc., then `_analyze_patterns()` runs every `_analyze_*` / `_detect_*` pass and emits `Finding`s. Also holds all the tunable constant tables at the top of the file. ~3200 lines. |
| `ast_transformer.py` | `ASTTransformer`: re-runs the analyzer on a file, filters findings by the enabled fix flags, converts each to `SourceEdit`s (char-offset ranges + replacement + priority + optional group), then `_apply_edits()` resolves overlaps and writes the file. ~2400 lines. |
| `reporter.py` | `Reporter` collects findings per mod/file, prints summaries, and saves `.txt` / `.json` / `.html` (Jinja2, `templates/`). `PERFORMANCE_IMPACT` maps pattern names to critical/high/medium/low for the HTML report. |
| `whole_program_analyzer.py` | Cross-file symbol definition/usage tracker for dead-code detection. **Not imported anywhere yet**; standalone / experimental. |
| `tools/capture_gate.py` | G9 (I-046): runs the transformer over originals and asserts no inserted `local` binds over a live outer name. Imported by `tools/corpus_run.py` and `tests/test_capture_gate.py`; `lab/tools/i021_capture_scan.py` is its CLI. |
| `tools/script_extractor.py` | Copies all `.script` files out of a mods tree preserving structure (for building test corpora). |
| `tools/split_test.py` | Splits an extracted mods tree into zip chunks of N mods for batch testing. |
| `templates/base.html`, `templates/report.html` | Jinja2 HTML report. |
| `alao_exclude.txt` | One mod name per line to exclude from reports and fixes. Auto-loaded when it sits next to `stalker_lua_lint.py`; also applies to `--revert`. Ships empty since I-036 (it used to carry `VANILLA_SCRIPTS`), and every excluded mod is named on stdout. |

### The pattern-name contract

`Finding.pattern_name` is the string key that ties the three big modules together. Adding or
renaming a pattern means touching all of:

1. `ast_analyzer.py`: a new `_analyze_*` method, registered in `_analyze_patterns()`, emitting
   `Finding(pattern_name=..., severity=...)` with whatever `details` the fix needs (node spans,
   names, `is_safe_to_fix`, etc.).
2. `ast_transformer.py`: a branch in `_generate_edits()` dispatching to a new `_edit_*` method
   that appends `SourceEdit`s. Some families are matched by prefix (`dead_code_*`, `repeated_*`).
3. `reporter.py`: an entry in `PERFORMANCE_IMPACT` (else it falls to the default) and, if the HTML
   highlight needs it, `highlight_code_match()`.
4. `README.md`: the GREEN / YELLOW / RED / DEBUG tables.

### Severity semantics

- `GREEN`: fixed by `--fix`. Must be semantically safe for arbitrary Lua 5.1.
- `YELLOW`: fixed only with `--fix-yellow` (or `--experimental` for `string_concat_in_loop`).
- `RED`: report only, never auto-fixed.
- `DEBUG`: debug/log calls, commented out by `--fix-debug`.
- `potential_nil_access` and `dead_code_*` are gated separately by `--fix-nil` /
  `--remove-dead-code` and additionally by `details['is_safe_to_fix']` / `details['is_safe_to_remove']`.

### How edits are applied

`SourceEdit` uses absolute character offsets into `self.source`. `_apply_edits()` sorts by
priority then position and drops any edit that overlaps an already-accepted higher-priority one.
`group_id` links an "enabler" insertion (e.g. `local mfloor = math.floor`) to its replacement
edits; if every replacement in a group is rejected, the insertion is dropped too. Debug-statement
comment-outs run at higher priority so commented-out code never participates in caching.

Node spans come from `luaparser` token positions via `_get_node_span()` / `_get_call_func_span()`;
when those are missing the code falls back to line-based spans and regex on the raw line.
Encoding is preserved on write via the analyzer's detected `_file_encoding`.

### Safety machinery in the CLI

- Any `--fix*` flag with no `scripts-backup-*.zip` in the mods root triggers an automatic full
  zip backup first (skip with `--no-first-time-auto-backup`).
- Each modified file gets a sibling `.alao-bak` (never overwritten if one already exists).
- Files that already have a `.alao-bak` are **skipped** on subsequent fix runs to prevent
  double-fixing. To re-process, `--revert` first.
- Analysis runs under a per-file `ThreadPoolExecutor` timeout (`--timeout`, default 10 s);
  fixes run in a `multiprocessing` pool (`--workers`). Worker functions must stay picklable
  (module-level functions taking a single tuple).

## Conventions and gotchas

- Lua target is 5.1 / LuaJIT 2.0.4. No `goto` semantics beyond keyword reservation, no integer
  subtype, `#t`/`table.getn` equivalence, `unpack` is a bare global.
- Anomaly engine specifics are encoded as constants at the top of `ast_analyzer.py`
  (`HOT_CALLBACKS`, `PER_FRAME_CALLBACKS`, `CACHEABLE_*`, `NIL_RETURNING_FUNCTIONS`,
  `NIL_CHECK_PATTERNS`, `SAFE_CALLBACK_PARAMS`, `EXPENSIVE_INDEXES`, `DEBUG_FUNCTIONS`). Prefer
  extending these tables over adding special-case logic.
- Hot callbacks use `cache_threshold - 1`; call counting is branch-aware across
  `if/elseif/else` so mutually exclusive branches are not summed.
- Cache variable naming goes through `_resolve_cache_name()` and `_collect_function_locals()`
  to avoid shadowing; caches inside closures resolve as upvalues.
- Many mod scripts are CP1251 with Russian comments. Do not read/write files with a hardcoded
  encoding; use `detect_file_encoding()`.
- Comments in the source are casual and first-person (author: Abraham / Priler). Keep the same
  tone; no need to formalize.
- `.gitignore` excludes `test/`, `extracted/`, `val/`, `origin_backup/`, `*.zip`, and
  `/report.*`. Use those names for local scratch corpora and outputs.
- The two `*_report_example.html` files at the root are sample outputs, not templates.

## Tests, tools and lab

Use `py -3.12` on this machine (it has `luaparser` 4.2.0, `jinja2`, `pytest`, `lupa`). `lupa` bundles
LuaJIT 2.0 and is the compile-checker / executor for rewritten Lua; import it as `lupa.luajit20`.

```bash
py -3.12 -m pytest -q                 # unit + CLI tests: 671 passed, 7 skipped, 4 xfailed (2026-09-19, gen-4 merge; lab/tests 210 passed)
py -3.12 -m pytest -q --corpus        # also analyzes the 66 vanilla scripts in the game install, read-only
py -3.12 -m pytest -q -rx             # print the xfail reasons: each one names a real, unfixed ALAO bug
py -3.12 -m pytest lab/tests -q       # lab-only tests (dashboard + FPS harness)
```

- `tests/`: `conftest.py` builds temp MO2 trees from inline Lua, runs the analyzer/transformer on
  snippets, compiles output with LuaJIT (`luajit_compiles`) and diff-executes original vs rewritten
  code under stubbed Anomaly globals (`run_both`). `tests/test_patterns/` has one module per
  pattern family; `test_transformer.py` covers edit overlap, groups, encodings, `.alao-bak`,
  idempotence; `test_cli.py` runs the real CLI in subprocesses. `tests/README.md` has the details.
- **Strict xfails are the bug list.** A test marked `xfail(strict=True)` documents a confirmed
  defect; when you fix the defect the test starts passing and pytest fails until you drop the
  marker. Never delete or loosen one to make the suite green. Known defects as of 2026-09-10:
  - `ast_transformer.py:155` writes with `Path.write_text()` and default newline translation; LF
    files come back CRLF on Windows.
  - `ast_analyzer.py:1612` suppresses the `db.actor` index when it is an Invoke receiver, so
    `db.actor:method()` never counts toward `repeated_db_actor`.
  - `ast_analyzer.py:2514` `_walk_for_dead_after_terminator` never descends into `do` blocks, so
    `dead_code_after_return` / `dead_code_after_break` cannot fire on parseable Lua 5.1.
  - Nil-guard detection is line-based: a one-line `if o then ... end` is not seen as a guard.
  - ~~`--fix --fix-debug` and `--fix --fix-nil` are not fixpoints (1 vanilla / 24 GAMMA files).~~
    Fixed 2026-09-19 (I-052), two strict xfails dropped. `_edit_repeated_calls` used to chop
    each scanned line at the first `--` before looking at strings, so a `---` inside a string
    literal faked an unbalanced `(` and killed the hoist; `mask_lua_code()` (new `keep_strings`
    flag) now masks comments and strings in one offset-preserving pass. And `--fix-nil` only
    offers its one-line `if var then ... end` when no other unguarded access of the same nil
    source exists - it used to guard use #1 of an inserted cache and leave #2..#4 to crash -
    and it treats `item and <expr using item>` as the guard Lua's short-circuit makes it
    (-475 GAMMA / -223 vanilla-db false-positive `potential_nil_access`, the only G7 movement).
    `tools/corpus_matrix.py` runs the corpus gate once per flag combination, which is the only
    way that class of bug is visible (every gate before gen-4 ran plain `--fix`).
  - ~~`stalker_lua_lint.py:738` reports timeouts as parse errors; `transform_file_worker`
    applies no timeout; the JSON report has no failure data.~~ Fixed 2026-09-11 (I-035 / I-037 /
    I-029), three strict xfails dropped. Timeouts have their own counter, failure lines print
    full paths without `-v`, the fix phase honours `--timeout` and skips files that failed
    analysis, and the JSON report carries `parse_failures` / `timeouts` / `crashes` /
    `compile_failures` / `findings_by_*` / per-file `edits`. `--verify-compile` (on whenever
    `lupa` imports) LuaJIT-compiles each rewrite and refuses the write on failure (I-004).
- `tools/microbench.py` + `bench/*.lua`: paired-snippet LuaJIT 2.0 microbenchmarks, one pair per
  `Finding.pattern_name`, with the beam's section-2 protocol baked in and no opt-out (fresh
  `LuaRuntime` per arm/mode, `jit.off(f, true)` on the chunk plus a self-check that the interpreter
  really is ~20x slower, `collectgarbage` before every timed run, best-of-9, `_G.__sink`, a 64-float
  `D` table). `--iters` sweeps inner loop length, because several rewrites flip sign with it
  (`table.concat` is 0.43x at 3 iterations and 15x at 2000). Prints a markdown table with a G2
  pass/fail column plus a jitter warning, writes JSON with `--json`. `pytest --bench` (or `-m slow`)
  runs the coverage guard that fails when a GREEN pattern has no bench pair. Format docs in
  `tools/README.md`; last full run in `lab/reports/microbench-table.md` / `-baseline.json`.
- `tools/corpus_extract.py --corpus gamma|vanilla` copies scripts from the GAMMA install
  (`D:\GOG_Games\Gamma\S.T.A.L.K.E.R. GAMMA`, READ-ONLY, never run `--fix` there) into `extracted/`
  (gitignored). `tools/corpus_run.py` runs analyze + fix on a fresh copy, LuaJIT-compiles every
  rewritten file, does a second fix pass to catch idempotence violations, and writes
  `lab/data/corpus/<run_id>/{manifest,results}.json`. `tools/corpus_matrix.py` runs that once per
  fix-flag combination (`--fix`, `--fix --fix-debug`, `--fix --fix-nil`, `--fix --fix-debug
  --fix-nil` by default, `--combos all` for the yellow/experimental/dead-code sweep) and prints
  one G4/G5/G9 table; idempotence is a property of a flag *combination*, which is how I-052's two
  defects hid from every gen-3 gate. `tools/corpus_compare.py --latest` diffs two
  runs. Baseline on GAMMA (1503 enabled scripts, 577 mods): 0 parse failures, 0 compile failures,
  analyze ~13 s, fix ~24 s, 513 files rewritten, 0 idempotence violations (was 8 before
  `_apply_edits` learned to fold contained edits into their container, 2026-09-10).
- `lab/` (contract and schemas in `lab/CONTRACT.md`):
  - `lab/dashboard/server.py --port 8765`: stdlib HTTP dashboard over `lab/data` (Corpus tab is the
    primary view; Ideas beam; in-game Runs). Open http://127.0.0.1:8765.
  - `lab/data/ideas.json` + `lab/docs/beam-ideas.md`: the ranked beam of ALAO improvement ideas
    (56 as of 2026-09-19 after gen-4: I-049 drx_da_main dispatcher -222 us/frame, I-050a ledge grabbing -107, make_callback mod -89 on stock, all measured in script-us; gen-5 = I-054 combined arm, I-053 moving scene, publishing decisions, see lab/docs/next-session.md). Score, keep or prune ideas there; new experiments should
    trace back to an idea id.
  - `lab/framework` (`aalo` package) + `lab/tools`: in-game A/B FPS harness that launches GAMMA via
    MO2 and captures frametimes (PresentMon if installed, psutil fallback). Needs an elevated
    terminal; see `lab/framework/README.md`. Use it to prove a rewrite helps in-game, not for
    settings hunting. PresentMon 2.5.1 CLI is installed (Intel MSI, `PresentMonConsoleApplication`).
    `aalo run --save gammabaseline` auto-loads that save via `-start server(...)` and skips the
    keypress screen, so runs are unattended. First real baseline (2026-09-10, run
    `20260910-231400-gamma-baseline`, stock GAMMA, uncapped, standing still): 212 fps avg,
    147 fps 1% low, 6.1 ms p99, per-30 s windows within 207-215. RTSS still runs with OSD on.
  - `lab/reports/`: install and corpus sanity reports.
- Benchmark honesty: microbenchmarks must state N, warm-up, best-of-K, JIT on/off, and must call
  `collectgarbage()` before every timed run (the protocol table in `lab/docs/beam-ideas.md` section 2).
  Without the collect, an early run wrongly showed `t[#t+1]=v` at 0.77x of `table.insert`; done
  properly it is 1.00x compiled and 1.2x-1.5x interpreted. Also note `jit.off(true,true)` does not
  affect already-loaded chunks. ALAO currently has no known regressions; the counter-based append
  (`n=n+1; t[n]=v`, 12x compiled) and promoting `string_concat_in_loop` out of `--experimental`
  (~8x) are the biggest measured wins on the queue.
- Regenerable data (`lab/data/runs`, `snapshots`, corpus `diffs/` and `alao-report.json`) is
  gitignored. Nothing from the lab work has been committed yet.
