# ALAO corpus sanity report

- Subject: ALAO at `C:\code\GIT\anomaly_alao`, commit `22bdcbbc` (working tree dirty: untracked `CLAUDE.md` and `docs/` only, no source edits)
- Corpora: vanilla Anomaly 1.5.3 (66 files) and GAMMA 0.9.4 enabled mods (1503 files)
- Date: 2026-09-10. Toolchain `py -3.12`, luaparser 4.2.0, lupa 2.8 (LuaJIT 2.0 for compile checks)
- Game install was read only throughout. Every run works on a copy under `C:\code\GIT\anomaly_alao\extracted\`.
- Harness: `tools/corpus_extract.py`, `tools/corpus_run.py`, `tools/corpus_compare.py` (see `tools/README.md`)

---

## Verdict

**ALAO is stable on this corpus and its output compiles.** Across 1503 GAMMA scripts and 66 vanilla scripts there were zero parse failures, zero analyzer timeouts, zero crashes, and zero LuaJIT compile failures among the 533 files it rewrote. The analyzer is also fast: 1503 files in 13.5 seconds, the fix pass in another 23.6 seconds.

One real correctness bug surfaced, and the harness was built to catch exactly this class:

- **`--fix` is not a fixpoint.** A cacheable-global rewrite landing *inside* a `table.insert(...)` call silently cancels the `table_insert_append` rewrite of that call. The optimization is lost, not deferred: because ALAO skips any file that already has a `.alao-bak`, a user who runs `--fix` once never gets it. 8 GAMMA files and 1 vanilla file are affected. Reproducer in section 4.

Everything else is bookkeeping and reporting gaps, listed in section 5.

---

## 1. Runs

| run_id | corpus | files | flags | analyze | fix |
|---|---|---:|---|---:|---:|
| `20260910-222214-vanilla-fix` | vanilla-1.5.3 | 66 | `--fix --fix-debug --fix-nil` | 1.55 s | 2.83 s |
| `20260910-222240-gamma-analyze` | gamma-0.9.4 | 1503 | analyze only | 12.77 s | n/a |
| `20260910-222415-gamma-fix` | gamma-0.9.4 | 1503 | `--fix` | 13.52 s | 23.56 s |

All three wrote `manifest.json`, `results.json`, `alao-report.json`, `diffs/` and per pass logs into `C:\code\GIT\anomaly_alao\lab\data\corpus\<run_id>\`.

### Corpus composition

| | vanilla-1.5.3 | gamma-0.9.4 |
|---|---:|---:|
| mods with scripts | 1 | 361 |
| script files | 66 | 1503 |
| bytes | 652,378 | 14,203,921 |
| utf-8 | 64 | 1476 |
| cp1251 | 2 | 18 |
| latin-1 fallback | 0 | 9 |

Note on the corpus size: `CONTRACT.md` states 2079 files and 605 enabled of 797 mods. The live `G.A.M.M.A` profile has **577 enabled**, 191 disabled and 29 separators, and the enabled mods carry **1503** script files. 2108 files exist across all 804 mod folders on disk, so the contract's 2079 is close to the all-mods figure, not the enabled-only one. The harness extracts enabled mods only, which is what the game actually loads. `corpus_extract.py --include-disabled` gets the larger set if a broader parser corpus is wanted.

---

## 2. Failure categories

| category | vanilla | gamma |
|---|---:|---:|
| parse failures | 0 | 0 |
| analyzer timeouts (10 s per file) | 0 | 0 |
| crashes | 0 | 0 |
| compile failures after fix (LuaJIT 2.0) | 0 | 0 |
| idempotence violations | 1 | 8 |

Parse failures, timeouts, crashes and compile failures are all empty, so there are no "first 10" entries to list for those four categories. That is the result, not a gap in the measurement: the harness attributes failures per file by re-running ALAO's own `analyze_file_worker` over every file, and it cross-checks against ALAO's own stdout counters, which also reported none.

Compile checking covered every rewritten file: 20 of 66 vanilla files and 513 of 1503 GAMMA files. Each was loaded with `loadstring` under lupa's bundled LuaJIT 2.0. A file is only counted as a failure if its `.alao-bak` original compiled and the rewrite did not, so pre-broken mod scripts do not get blamed on ALAO.

---

## 3. Findings

### GAMMA, top 15 patterns (14,455 findings total)

| pattern | count | severity |
|---|---:|---|
| `global_write` | 5697 | RED |
| `debug_statement` | 2586 | DEBUG |
| `unused_local_variable` | 1581 | RED |
| `potential_nil_access` | 1499 | YELLOW |
| `table_insert_append` | 701 | GREEN |
| `unnecessary_else` | 621 | RED |
| `string_find_plain` | 441 | GREEN |
| `uncached_globals_summary` | 280 | GREEN |
| `string_concat_in_loop` | 205 | YELLOW |
| `string_literal_concat` | 189 | YELLOW |
| `per_frame_callback` | 88 | RED |
| `repeated_db_actor` | 78 | GREEN |
| `vector_alloc_in_loop` | 78 | RED |
| `unused_local_function` | 71 | RED |
| `redundant_not_eq` | 66 | GREEN |

By severity: GREEN 2023, YELLOW 3987, RED 5797, DEBUG 2648.

`global_write` alone is 39% of everything reported and is RED, so it is pure noise for anyone running ALAO to make the game faster. Anomaly mod style writes globals on purpose. Worth considering a default-off switch for it.

### Vanilla, top 15 patterns (399 findings total)

| pattern | count |
|---|---:|
| `debug_statement` | 113 |
| `table_insert_append` | 63 |
| `global_write` | 51 |
| `potential_nil_access` | 41 |
| `unnecessary_else` | 37 |
| `unused_local_variable` | 35 |
| `uncached_globals_summary` | 18 |
| `string_concat_in_loop` | 15 |
| `string_len` | 10 |
| `string_find_plain` | 3 |
| `unused_local_function` | 3 |
| `redundant_not_eq` | 3 |
| `distance_to_comparison` | 2 |
| `pow_op_simple` | 2 |
| `repeated_self_object_id()` | 1 |

By severity: GREEN 103, YELLOW 131, RED 51, DEBUG 114.

### What survives the fix

Re-analyzing the fixed GAMMA tree: GREEN drops from 2023 to **79**, so `--fix` lands about 96% of what it claims. The 79 stragglers are 8 `table_insert_append` (the bug in section 4) and 71 `repeated_*` cache opportunities, most of which are newly visible only because pass 1 rewrote the surrounding code. YELLOW rose from 3987 to 3989: two new `string_concat_in_loop` findings were created by the fix pass itself, which is worth a look but is not a correctness problem since YELLOW is never auto-fixed without `--fix-yellow`.

---

## 4. The one real bug: nested cache edits cancel `table_insert_append`

**Severity: medium. Lost optimization, not broken output.** Nothing miscompiles; ALAO just silently fails to apply a GREEN fix it reported, and its file-skipping rule makes the loss permanent for a normal user.

### Mechanism

`ast_transformer.py:2272` `_apply_edits()` resolves overlapping replacements by priority and drops the loser. When a function has enough `math.random` / `unpack` / `string.sub` calls to trigger the cacheable-globals rewrite, ALAO emits a replacement for that name at every call site. If one of those call sites sits *inside* the argument list of a `table.insert(...)` that also has a `table_insert_append` edit, the inner name replacement wins and the outer append rewrite is dropped. The append edit is discarded, never retried.

Running `--fix` a second time applies it, because by then the inner rewrite is already in the file and no longer competes. That is precisely what the idempotence check detects.

### Minimal reproducer

```lua
function build(src)
    local anims = {}
    for i,v in pairs(src) do
        local a = math.random(0,1)
        local b = math.random(0,2)
        local c = math.random(0,3)
        table.insert(anims, {e = i, d = math.random(0,1), c = a+b+c})
    end
    return anims
end
```

```
py -3.12 stalker_lua_lint.py <dir> --direct --fix --no-first-time-auto-backup --single-thread
```

After pass 1 the four `math.random` calls become `mrandom` and a `local mrandom = math.random` appears, but the `table.insert` line is untouched. Delete the `.alao-bak`, run again, and only then does it become `anims[#anims+1] = {...}`.

### All 9 affected files, with the offending line

GAMMA (`20260910-222415-gamma-fix`, paths relative to the corpus root `C:\code\GIT\anomaly_alao\extracted\gamma\`):

| # | file | line | source |
|---|---|---:|---|
| 1 | `108- Remove dropping weapons from damage - Great_Day/gamedata/scripts/actor_effects.script` | 1533 | `table.insert(anims,{e = i,d = v[2] or mrandom(0,1),c = cnt})` |
| 2 | `166- New sorting tabs - Bartoche/gamedata/scripts/ui_inventory.script` | 770 | `table.insert(context_params, {id, bag, unpack_(func_action)})` |
| 3 | `167- New sorting tabs htsb patch - Bartoche/gamedata/scripts/ui_inventory.script` | 772 | `table.insert(context_params, {id, bag, unpack_(func_action)})` |
| 4 | `181- Ballistics Overhaul (GBOOBS) - Grokitach/gamedata/scripts/grok_bo_enhanced_recoil.script` | 107 | `table.insert(anims,{e = i,d = v[2] or mrandom(0,1),c = cnt})` |
| 5 | `G.A.M.M.A. 3D PDA and Headlamp Animations/gamedata/scripts/actor_effects.script` | 1533 | `table.insert(anims,{e = i,d = v[2] or mrandom(0,1),c = cnt})` |
| 6 | `G.A.M.M.A. Accurate Defense Values/gamedata/scripts/ui_inventory.script` | 844 | `table.insert(context_params, {id, bag, unpack_(func_action)})` |
| 7 | `G.A.M.M.A. Enhanced Recoil/gamedata/scripts/grok_bo_enhanced_recoil.script` | 128 | `table.insert(anims,{e = i,d = v[2] or mrandom(0,1),c = cnt})` |
| 8 | `G.A.M.M.A. UI/gamedata/scripts/ui_inventory.script` | 807 | `table.insert(context_params, {id, bag, unpack_(func_action)})` |

The line numbers and the `mrandom` / `unpack_` names are from the *post-fix* file, which is where the dropped edit is visible. In the pristine source those read `math.random` and `unpack`.

Vanilla (`20260910-222214-vanilla-fix`), same bug, five sites in one file, `VANILLA_SCRIPTS/gamedata/scripts/luapanda.lua`:

| line | source (post-fix) |
|---:|---|
| 2185 | `table.insert(logTable, tostr(k));` |
| 2187 | `table.insert(logTable, tostr(v));` |
| 3490 | `table.insert(t, ssub(s, oldj+1, i-1))` |
| 3512 | `table.insert(t, escapeSequences[ssub(s, i, j)])` |
| 3515 | `table.insert(t,ssub(j, j+1))` |

The `ssub` calls are ALAO's own `string.sub` cache locals, which is the same mechanism. The give-away in `luapanda.lua` around line 2185 is that the two adjacent lines with no nested call, `logTable[#logTable+1] = ":"`, were rewritten fine while the two with `tostr(...)` inside were not.

### Suggested direction

In `_apply_edits`, an overlap between a *containing* replacement and a *contained* one is not a genuine conflict. The contained edit can be applied to the replacement text of the container instead of cancelling it. Failing that, re-running edit generation until no edits are dropped would at least make `--fix` a fixpoint. The overlap rule as written assumes edits are peers.

---

## 5. Reporting and tooling gaps in ALAO

None of these are wrong output, but each one forced the harness to work around ALAO rather than read from it.

1. **The JSON report contains findings only.** No parse failures, no timeouts, no crashes, no per-file edit counts, no dropped-edit counts. Anything a regression harness needs about *failures* has to come from somewhere else. `reporter.py:368` `_save_json` writes `generated`, `summary` and `findings` and nothing more.
2. **`Files with parse errors` lumps timeouts in with syntax errors.** `stalker_lua_lint.py:738` routes anything matching `TimeoutError` into the parse-error bucket. A file that took longer than `--timeout` is a completely different problem from a file that will not parse, and the summary cannot tell you which you have.
3. **Per-file failure detail requires `-v`, which is unusable at corpus scale.** `-v` also triggers `reporter.print_detailed()`, which dumps all 14,455 findings. And even then the per-file lines print `script_path.name`, not the path, so the many mods sharing `ui_inventory.script` cannot be told apart.
4. **Dropped edits are invisible.** `_apply_edits` discards overlapping edits silently. `edits_dropped_overlap` is written as `null` in every `results.json` with a note, because there is nothing to read it from. This is the counter that would have made the section 4 bug obvious on day one.
5. **`alao_exclude.txt` ships with `VANILLA_SCRIPTS` in it** and auto-loads whenever it sits next to `stalker_lua_lint.py`. Anyone who names their vanilla corpus folder the obvious thing gets a silent zero-file run. The harness defaults to overriding it with an empty list; `--use-repo-exclude` restores the repo behavior.
6. **`--fix` skips files that already have a `.alao-bak`.** Combined with gap 4 and the section 4 bug, a dropped edit is permanent unless the user knows to `--revert` and re-run.

Tooling ideas worth filing against `data/ideas.json`, all `category: tooling` or `transformer`:

- Add `parse_failures` / `timeouts` / `crashes` arrays to the JSON report, with full paths and error text. Removes the need for the harness's duplicate analyze pass and halves corpus run time.
- Count and report edits dropped for overlap, per file and in total.
- Separate the timeout counter from the parse-error counter in the summary.
- Make `--fix` iterate to a fixpoint, or fix the containment case in `_apply_edits` directly.

---

## 6. Reproducing

```
py -3.12 tools/corpus_extract.py --corpus vanilla --force
py -3.12 tools/corpus_extract.py --corpus gamma --force

py -3.12 tools/corpus_run.py --corpus extracted/vanilla --corpus-name vanilla-1.5.3 \
    --label vanilla-fix --fix-flags "--fix --fix-debug --fix-nil" --jobs 8
py -3.12 tools/corpus_run.py --corpus extracted/gamma --corpus-name gamma-0.9.4 \
    --label gamma-analyze --jobs 8
py -3.12 tools/corpus_run.py --corpus extracted/gamma --corpus-name gamma-0.9.4 \
    --label gamma-fix --fix-flags=--fix --jobs 8 --keep-work

py -3.12 tools/corpus_compare.py --latest --corpus gamma-0.9.4
```

A full GAMMA analyze plus fix plus idempotence cycle is about 90 seconds wall clock on this machine with 8 workers, so this is cheap enough to run on every change to the analyzer or the transformer.
