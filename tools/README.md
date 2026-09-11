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
| `--use-repo-exclude` | honour `alao_exclude.txt`. **Off by default** - that file excludes `VANILLA_SCRIPTS`, which would silently empty the vanilla corpus. |
| `--no-probe` | skip failure attribution (counts only, from stdout) |
| `--no-idempotence` | skip the second fix pass |
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

## script_extractor.py / split_test.py

Older helpers, predating the corpus harness. `script_extractor.py` copies all
`.script` files out of a mods tree preserving structure; `split_test.py` splits
an extracted tree into zip chunks of N mods for batch testing. `corpus_extract.py`
supersedes the first one for regression work (it filters by MO2 enablement and
writes a hashed manifest).
