# ALAO improvement beam — generation 1

Scope: make ALAO (`C:\code\GIT\anomaly_alao`) better at optimizing Anomaly/GAMMA Lua. Four axes:
correctness of transforms, coverage of new patterns, safety, and ALAO's own speed. Game knobs are
out of scope (the previous beam is archived in `data/ideas-game-knobs.json`).

Everything below is measured. Two independent measurement sources feed it:

- **The corpus harness** (`tools/corpus_run.py`, built by the corpus agent) — real ALAO runs over
  the enabled GAMMA profile and vanilla Anomaly, recorded in `data/corpus/<run_id>/` and written up
  in `reports/corpus-sanity-report.md`. Every ALAO-behaviour number here comes from that report.
- **A LuaJIT 2.0 microbenchmark** I ran through `lupa.luajit20`, the exact target VM, plus a
  regex census of the extracted enabled corpus. Every speedup and raw-construct count here comes
  from that.

**Revision note.** This document was first written against all 2084 `*.script` files on disk across
all 804 mod folders. The live `G.A.M.M.A` profile has **577 enabled mods, 361 of which ship scripts,
totalling 1503 files** — that is what the game actually loads and what the harness measures. Every
count below has been recomputed on the 1503-file enabled corpus. Where an all-mods figure is still
useful it is labelled as such.

---


> **Benchmark note (2026-09-11, resolved):** an early draft of this doc reported `t[#t+1]=v` at 0.77x of `table.insert` under the JIT. An independent rerun got 0.96x-1.02x, and the root cause was found: the first harness ran no `collectgarbage()` between timed runs, so allocation-heavy variants were timed against different heap states. With the protocol in section 2 (collect before every run, best of 9) the rewrite is 1.00x compiled and 1.2x-1.5x interpreted, so ALAO has no known regressions. The counter-based append (I-001) remains the real win.

## 1. Methodology — what "kept" means

An idea moves from `queued` to `kept` only if every applicable gate passes. Gates G4, G5 and G6 are
now automated by `tools/corpus_run.py` and `tools/corpus_compare.py`.

| Gate | Threshold |
|---|---|
| **G1 Corpus relevance** | A new pattern must have >= 50 real hits in the 1503-file enabled GAMMA corpus, or >= 10 hits inside a per-frame callback body. Analyzer/transformer/safety/tooling ideas are exempt. |
| **G2 Measured speedup** | The rewrite must be >= 1.15x faster than the original on `lupa.luajit20`, measured in **both** JIT-on and JIT-off mode, with no mode slower than 0.98x. A rewrite that wins in one mode and loses in the other is not GREEN; it may ship as YELLOW with a documented condition. |
| **G3 Semantics** | Differential execution: original and rewritten chunk produce identical observable results under a stubbed Anomaly API, over >= 20 generated inputs including empty tables, holes, `nil`, and metatables. |
| **G4 Compiles** | Zero LuaJIT 2.0 compile failures among rewritten files on a full corpus `--fix`. Baseline is 0 of 513, so any regression is unambiguous. |
| **G5 Idempotence** | A second `--fix` pass changes zero files. Baseline is 8 violations on GAMMA and 1 on vanilla, all from one known bug (I-008), so this gate should get *better*, never worse. |
| **G6 No ALAO slowdown** | Analyze wall time must not regress more than 10% against 13.5 s for 1503 files at 8 workers; fix must not regress more than 10% against 23.6 s. |
| **G7 No new findings from the fix** | Re-analysis after `--fix` must not introduce findings that were not there before. Baseline: YELLOW went 3987 -> 3989, so two are already leaking. |
| **G8 Tests** | `py -3.12 -m pytest` green from the repo root, including a fixture that pins the new behaviour. |

`pruned` is a real outcome, and so is retraction. I-011, I-034 and I-032 below are all expected
prunes on measured grounds. Hit count without a measured win is not evidence — and neither is a
measured win from a benchmark that skipped the protocol in section 2, which is how the first version
of this document reached a wrong conclusion about I-002.

**Baseline to beat** (`20260910-222415-gamma-fix`, 1503 files, 8 workers):

| Metric | Value |
|---|---|
| analyze wall time | 13.5 s |
| fix wall time | 23.6 s |
| files rewritten / edits applied | 513 / 4743 |
| parse failures, timeouts, crashes | 0, 0, 0 |
| LuaJIT 2.0 compile failures after fix | 0 of 513 |
| idempotence violations | 8 (GAMMA) + 1 (vanilla) |
| GREEN findings before -> after fix | 2023 -> 79 (96% landed) |
| YELLOW findings before -> after fix | 3987 -> 3989 |

---

## 2. The measurement that reframes the whole beam

The corpus harness proves ALAO is *stable*: it does not crash, does not time out, and its output
compiles. It does not, and cannot, prove ALAO's rewrites are *faster*. So the transforms were
benchmarked on LuaJIT 2.0 via `lupa.luajit20`, the exact target VM.

### Protocol (record this; future numbers must use it to be comparable)

| Element | Setting |
|---|---|
| VM | `lupa.luajit20` — LuaJIT 2.0, x64 Windows. One fresh `LuaRuntime` per mode. |
| Modes | JIT on (default), and JIT off via `jit.off(f, true)` applied to the chunk itself. Setting `jit.off(true, true)` globally does **not** take effect for already-loaded chunks and silently measures JIT-on numbers. |
| Chunk | `loadstring("local N,D=...\n" + body + "\n_G.__sink=" + sink)`. `D` is a 64-element float table so the JIT cannot constant-fold the loop body; `__sink` defeats dead-code elimination of the result. Without both, several cases optimize to zero and report meaningless ratios. |
| Warm-up | Two calls at N=1000 before timing, so the trace is recorded. |
| Timing | `time.perf_counter()` on the Python side around one `f(N)` call. `os.clock()` inside Lua has ~10 ms resolution on Windows and is unusable here. |
| **GC** | **`collectgarbage('collect')` immediately before every timed run.** |
| Reps | Best of 9; median also recorded. |
| N | 2e6 with JIT on, 3e5 with JIT off (the interpreter is roughly 15x slower). |

### Correction: the first version of this table was wrong

The first draft reported `table.insert(t,v)` -> `t[#t+1]=v` at **0.77x** and called ALAO's
highest-volume fix a regression. **That was a measurement artifact and is retracted.** The original
harness did not collect garbage between timed runs, so variants that allocate a multi-million-element
table were compared against different heap states. The team lead independently measured the same
case and got a neutral result; re-running with `collectgarbage` before each run and best-of-9
reproduces the team lead's numbers at both N=2e6 and N=3e6. Two other rows changed with the fix
(`math.pow(x,2)` and `math.pow(x,0.5)`), and both corrections go the same way: **ALAO has no
regressions.** The table below is the corrected one.

Speedup = time(original) / time(rewrite). Below 1.00 would mean the rewrite is slower.

| Transform (ALAO does this today unless noted) | JIT on | JIT off |
|---|---|---|
| `table.insert(t,v)` -> `t[#t+1]=v` | 1.00x | 1.2-1.5x |
| `string.len(s)` -> `#s` | 1.00x | 1.63x |
| `math.floor` -> cached `local mfloor` | 0.90x | 1.23x |
| `math.pow(x,2)` -> `x*x` | 1.00x | 1.58x |
| `math.pow(x,0.5)` -> `x^0.5` (what ALAO does) | 1.00x | 1.05x |
| `math.pow(x,0.5)` -> `math.sqrt(x)` (proposed instead) | 1.00x | **4.25x** |
| `x^0.5` -> `math.sqrt(x)` (the residual miss) | 1.00x | **4.12x** |
| bare global read -> local copy | 1.00x | 1.23x |
| `pos:distance_to(t)<n` -> `distance_to_sqr<n*n` (sqrt proxy) | 1.03x | 1.46x |
| `string.sub(s,1,1)==` -> `string.byte` (not in ALAO) | 1.00x | 1.10x |
| `for i=1,#t` -> hoisted `local n=#t` (not in ALAO) | 1.00x | 1.00x |
| `s=s..x` in loop -> `table.concat` (ALAO, **`--experimental` only**) | **8.69x** | **7.89x** |
| `t[#t+1]=v` -> manual counter (not in ALAO) | **12.44x** | **4.80x** |
| `table.remove(t)` tail -> `t[n]=nil; n=n-1` (not in ALAO) | **15.57x** | 4.31x |
| `pairs` -> `ipairs` over an array (not in ALAO) | **5.90x** | 0.29x |
| `ipairs` -> numeric `for` (not in ALAO) | 1.05x | **2.89x** |
| `vector()` alloc in loop -> reuse one scratch (not in ALAO) | 1.01x | **10.47x** |
| `string.format` -> `..` concat (not in ALAO) | 0.56x | 0.57x |

The `table.insert` interpreted figure is given as a range: 1.52x at N=3e5 here, 1.17x at N=2e6 in
the team lead's run. The direction is stable, the magnitude moves with table size, and nothing
depends on which end of the range is right.

Three conclusions drive the ranking:

1. **Every transform ALAO ships is safe to keep.** Nothing measures below 1.00x in a way that
   survives the corrected protocol. The worst row is cached `math.floor` at 0.90x under the JIT,
   which is close to noise and is 1.23x interpreted. ALAO is not making anything slower.
2. **But almost everything it ships is worth ~nothing on a compiled trace.** `table.insert`,
   `string.len`, `math.pow`, global caching and `distance_to_sqr` are all 1.00-1.03x under the JIT
   and 1.05-1.63x interpreted. These are interpreter optimizations, and ALAO has no idea which of
   its 325 per-frame bodies actually run interpreted. The mode split is severe in both directions:
   `pairs` -> `ipairs` is 5.90x compiled and 0.29x interpreted, while scratch-vector reuse is 1.01x
   compiled and 10.47x interpreted. Choosing the right transform per callback is worth more than any
   single new pattern.
3. **The big wins are allocation and O(n^2) wins, and ALAO already owns one of them.**
   `string_concat_in_loop` measures **8.69x compiled and 7.89x interpreted**, the best all-round
   result in the table, it has 205 corpus findings — and it is locked behind `--experimental` so
   nobody gets it. That is the cheapest large win available (I-039). The other three are
   counter-based append (12.44x), tail `table.remove` (15.57x, but one corpus site) and
   scratch-vector reuse (10.47x interpreted).

The corpus result that GREEN drops 2023 -> 79 means ALAO *lands* 96% of what it promises. It says
nothing about whether what it promises is worth landing. That gap is the whole beam.

---

## 3. What ALAO currently detects and fixes

From `ast_analyzer.py` (3172 lines) and `ast_transformer.py` (2365 lines).

**Constant tables** at the top of the analyzer: `HOT_CALLBACKS` (19 names), `PER_FRAME_CALLBACKS`
(only 4 names), `CACHEABLE_BARE_GLOBALS`, `BARE_GLOBALS_UNSAFE_TO_CACHE`, `CACHEABLE_MODULE_FUNCS`
(math/string/table/bit), `DEBUG_FUNCTIONS`, `DIRECT_REPLACEMENT_FUNCS`, `EXPENSIVE_INDEXES`
(a single entry, `db.actor`), `NIL_RETURNING_FUNCTIONS` (~45 entries), `NIL_CHECK_PATTERNS`,
`SAFE_CALLBACK_PARAMS`.

**The 18 passes** registered in `_analyze_patterns()`: table_insert, deprecated_funcs, math_pow,
pow_operator, string_literal_concat, string_find_plain, redundant_not_eq, uncached_globals,
repeated_calls_in_scope, string_concat_in_loop, debug_statements, global_writes, nil_access,
dead_code (five sub-detectors plus unnecessary_else, constant_conditions, unused locals and funcs),
per_frame_callbacks, distance_to_comparisons, vector_allocations_in_loops.

**The 15 `_edit_*` methods** in the transformer, applied through `_apply_edits()`, a three-pass
filter: replacements admitted by priority then position with bisect-based overlap rejection,
insertions admitted only outside admitted replacement spans, then enabler insertions dropped when
their group has no surviving replacement. Edits are applied end-to-start.

**Where the logic is heuristic**, in rough order of how much it worries me:

- **`_apply_edits()` treats containment as conflict** (`ast_transformer.py:2272`). This is no longer
  a worry, it is a confirmed bug with a reproducer — see I-008. Two edits where one span *contains*
  the other are not peers, but the overlap rule handles them as peers and drops the container.
- `_extract_table_insert_value()` (`ast_transformer.py:229`) re-parses the call text and takes the
  first `,` after the first `(`, with no string or paren awareness *before* that comma. It then does
  a careful string/long-string/paren scan for the closing paren. The value span is reconstructed
  from text even though the AST already has the argument node. A first argument containing a comma
  inside a string or a call would split wrong. The enabled corpus has zero such cases, so this is a
  fixture-and-fuzz bug, not a field bug.
- `_get_node_span()` / `_get_call_func_span()` (lines 1853, 2033) fall back to `_get_line_span()`
  and then to raw-line regex when `luaparser` token positions are missing. `_parse_token_start/end`
  parse token positions out of `str(token)`. There are 31 regex call sites in the transformer.
- `_edit_uncached_globals()` has a fallback insertion path (lines 1323-1335) that guesses the indent
  via `_get_indent_at_line` then `_detect_indent_unit` when the function body start is unresolvable.
- Cache-name collision avoidance runs through `_resolve_cache_name()` + `_collect_function_locals()`,
  a name-set heuristic rather than real scope resolution, so shadowing a name introduced in a nested
  block is possible in principle.
- `_strip_line_comments_and_strings()` (analyzer, line 1388) and `_strip_strings_and_comments()`
  (transformer, line 580) are two separate hand-rolled lexers used for nil-guard detection and
  debug-statement comment-out.
- `analyze_file()` returns `[]` on both encoding failure (line 446) and parse failure (line 459), so
  an unparseable file is indistinguishable from a clean one. The enabled corpus has zero parse
  failures, so this is currently a latent hazard rather than an active one.

---

## 4. Corpus measurements (enabled GAMMA, 1503 files)

Encodings: 1476 UTF-8, 18 CP1251, 9 latin-1 — `detect_file_encoding()` handles 100% with zero
failures. Zero parse failures, zero timeouts, zero crashes under ALAO's own analyze pass.

| Raw construct (regex census) | Enabled (1503) | All mods (2084) |
|---|---:|---:|
| `table.insert(t, v)` | 682 | 1012 |
| — of those, inside loops | 232 | 329 |
| `table.remove` | 42 | 63 |
| — 1-arg tail form | **1** | 6 |
| `table.sort` | 133 | 168 |
| `vector()` constructor | 1081 | 1567 |
| — of those, `vector():set(...)` | 1007 | 1458 |
| — inside loops / per-frame bodies | 52 / 27 | 102 / 27 |
| `level.object_by_id` | 496 | 688 |
| — inside loops / per-frame | 209 / 11 | 278 / 12 |
| `alife():object` | 47 | 93 |
| `pairs(` | 2333 | 3564 |
| — inside loops / per-frame | 256 / 50 | 396 / 57 |
| `ipairs(` | 449 | 597 |
| `for i = ..., #t do` | 764 | 1274 |
| `..` concat inside loops | 1711 | 2866 |
| — inside per-frame bodies | 145 | 179 |
| `.. tostring(` | 503 | 743 |
| `string.format` | 234 | 305 |
| `game.translate_string` in loops / per-frame | 66 / 15 | 179 / 15 |
| `time_global()` / `os.clock` | 1043 / 0 | 1285 / 2 |
| `get_console():execute` | 141 | 199 |
| `ini_file:r_*` reads | 111 | 201 |
| `string.len` / `string.sub` | 38 / 87 | 59 / 139 |
| `unpack(` / `select('#',` | 168 / 7 | 249 / 21 |
| `math.pow` / `math.floor(x+0.5)` | 25 / 4 | 47 / 8 |
| `db.actor` inside per-frame bodies | 306 | 350 |
| Per-frame bodies (engine callbacks + `:update` methods) | 228 + 97 = **325** | 302 + 139 = 441 |
| Files with any `local f = math.foo` cache | **113 of 1503 (7.5%)** | 177 of 2084 (8.5%) |

---

## 5. ALAO's real hit distribution

From `20260910-222240-gamma-analyze`: **14,455 findings**, by severity GREEN 2023, YELLOW 3987,
RED 5797, DEBUG 2648.

| Pattern | GAMMA (1503) | Vanilla (66) | Severity |
|---|---:|---:|---|
| `global_write` | 5697 | 51 | RED |
| `debug_statement` | 2586 | 113 | DEBUG |
| `unused_local_variable` | 1581 | 35 | RED |
| `potential_nil_access` | 1499 | 41 | YELLOW |
| `table_insert_append` | **701** | 63 | GREEN |
| `unnecessary_else` | 621 | 37 | RED |
| `string_find_plain` | 441 | 3 | GREEN |
| `uncached_globals_summary` | 280 | 18 | GREEN |
| `string_concat_in_loop` | 205 | 15 | YELLOW |
| `string_literal_concat` | 189 | — | YELLOW |
| `per_frame_callback` | 88 | — | RED |
| `repeated_db_actor` | 78 | — | GREEN |
| `vector_alloc_in_loop` | 78 | — | RED |
| `unused_local_function` | 71 | 3 | RED |
| `redundant_not_eq` | 66 | 3 | GREEN |

**Correction to the first draft of this document.** I had read the two `*_report_example.html` files
shipped in the repo and concluded that eleven analyzer passes never fire, because they appear zero
times there. They do fire. `unnecessary_else` (621), `string_find_plain` (441),
`string_literal_concat` (189), `per_frame_callback` (88), `vector_alloc_in_loop` (78),
`unused_local_function` (71) and `redundant_not_eq` (66) are all live. The shipped HTML examples are
simply stale — they predate those passes — which is now an idea about the repo's sample files
(I-014) rather than an idea about the analyzer.

`global_write` alone is 5697 findings, 39% of everything reported, and it is RED so nothing is ever
done with it. Anomaly mod style writes globals deliberately. It buries every actionable finding
under it (I-031).

---

## 6. The beam — 39 ideas

Ranked by prior score. Categories per contract: `pattern | analyzer | transformer | safety |
alao-perf | tooling | corpus`. **Top 8 are generation-1 `queued`; the rest are `proposed`**, except
I-033 which the corpus agent has already delivered and which is marked `kept`.

### Queued (generation 1)

**I-003 — LuaJIT 2.0 microbenchmark harness `tools/microbench.py`** *(tooling, score 9.5)*
Hypothesis: the corpus harness proves ALAO is stable and that its edits land, but nothing in the
repo or the lab measures whether a rewrite is *faster* — and the first version of this document
proves how easily that goes wrong. It reported ALAO's highest-volume fix as a 23% regression on the
strength of a benchmark with no GC control, and the conclusion survived until someone re-measured by
hand. A harness with the section-2 protocol baked in prevents the next one. Change: new
`tools/microbench.py` plus a `bench/` folder of paired snippets keyed by `Finding.pattern_name`,
timed under `lupa.luajit20` in both JIT-on and JIT-off mode, with warm-up, `collectgarbage` per run
and best-of-N mandatory; wired into `pytest` as a slow-marked test so a pattern with no bench entry
fails. Measure: reproduces the section-2 table within 10%; every GREEN pattern must clear gate G2.
Gain: high. Risk: low.

**I-001 — Counter-based append for append-only tables in loops** *(pattern, 9.4)*
Hypothesis: the real append win is a hoisted counter — **12.44x** with the JIT on and **4.80x**
interpreted, the largest all-round win available and confirmed by two independent measurements.
`t[#t+1]=v` recomputes `#t`, an O(log n) array-boundary search, on every append; a counter does not.
Change: new `ast_analyzer.py::_analyze_append_loop` proving a local table is created immediately
before the loop, only appended to inside it, and never passed somewhere that could resize it; plus
`ast_transformer.py::_edit_append_loop` inserting `local n = 0` and rewriting to
`n = n + 1; t[n] = v`. Measure: I-003 bench plus differential tests over tables with holes and early
`break`. Gain: high. Risk: high — the aliasing proof is the whole idea. Corpus: 232 `table.insert`
calls inside loops, and the same transform applies to the 701 sites ALAO already rewrites to
`t[#t+1]`. Promoted above I-002 on the team lead's recommendation and the corrected numbers.

**I-008 — Fix `_apply_edits()` containment, the confirmed dropped-edit bug** *(transformer, 9.2)*
Hypothesis: `_apply_edits()` (`ast_transformer.py:2272`) resolves overlaps by priority and drops the
loser, but when a cacheable-global rewrite lands *inside* the argument list of a `table.insert(...)`
that also has a `table_insert_append` edit, the inner edit wins and the outer append rewrite is
discarded, never retried. Containment is not conflict. Because `--fix` skips any file with an
existing `.alao-bak`, the loss is permanent for a normal user. Change: apply the contained edit to
the container's replacement text instead of cancelling the container; failing that, iterate edit
generation to a fixpoint. Also return and report the dropped-edit count. Measure: G5 idempotence
goes 8+1 -> 0, and the 8 GREEN `table_insert_append` stragglers in the post-fix re-analysis
disappear. Gain: high. Risk: med. Confirmed on 9 files, reproducer in
`reports/corpus-sanity-report.md` section 4; canonical case is
`G.A.M.M.A. UI/gamedata/scripts/ui_inventory.script:807`.

**I-039 — Promote `string_concat_in_loop` out of `--experimental`** *(safety, 9.0)*
Hypothesis: this is the best-measuring transform in the whole table — **8.69x with the JIT on and
7.89x interpreted**, the only one that wins big in both modes — it is already implemented, and it is
locked behind a flag almost nobody passes. 205 corpus findings are sitting unclaimed. Change:
`stalker_lua_lint.py` and `ast_analyzer.py` — move `string_concat_in_loop` from `--experimental` to
`--fix-yellow`, or to GREEN if I-017's differential tests clear it. The existing safety conditions
(variable initialized to `""` before the loop, simple `var = var .. expr` shape) already encode the
preconditions. Measure: I-003 bench, differential tests on the accumulator shape including early
`break` and nested loops, and G4-G7 clean on a full corpus run with the flag promoted. Gain: high.
Risk: med — it is the most structurally invasive rewrite ALAO has, which is presumably why it was
gated, so I-017 should land alongside it. Corpus: 205 GAMMA findings, 15 vanilla.

**I-004 — Compile-check rewritten files inside ALAO, not only in the lab** *(safety, 8.5)*
Hypothesis: the harness compile-checks ALAO's output externally and found 0 failures across 513
rewritten files, which is excellent — but the guard lives in the lab, so an ALAO user gets none of
it. Change: `ast_transformer.py::transform_file` — behind a `--verify-compile` flag (default on when
`lupa` imports), `loadstring` the new source under `lupa.luajit20` and refuse the write on failure.
Measure: `compile_failures_after_fix` stays empty, and a deliberately corrupted transform is caught
by a test. Gain: high. Risk: low — the flag must degrade cleanly when `lupa` is absent, since it is
not in `requirements.txt`.

**I-013 — Trace-abort / NYI awareness for LuaJIT 2.0** *(analyzer, 8.4)*
Hypothesis: the corrected section-2 table shows the mode split is not about avoiding regressions, it
is about picking the right transform. Everything ALAO ships is 1.00-1.03x on a compiled trace and
1.05-1.63x interpreted, while the transforms it does not ship split violently by mode — `pairs` to
`ipairs` is 5.90x compiled and 0.29x interpreted, scratch-vector reuse is 1.01x compiled and 10.47x
interpreted. A classifier that says which of the 325 per-frame bodies actually run interpreted is
worth more than any single new pattern, because it decides which pattern to apply. Change: a new
`LUAJIT20_NYI` constant table beside the others at the top of `ast_analyzer.py`, plus
`_analyze_trace_aborts()` emitting a per-function mode classification that `_analyze_uncached_globals`
and `_analyze_table_insert` consult before choosing a severity. Measure: classify all 325 per-frame
bodies; cross-check a sample against `jit.dumpon` output under `lupa.luajit20`. Gain: high.
Risk: med — the NYI list must be verified against the bundled 2.0 build, not recalled.

**I-029 — Put failure and edit data in the JSON report** *(tooling, 8.3)*
Hypothesis: `reporter.py::_save_json` writes only `generated`, `summary` and `findings`, so nothing
a regression harness needs about *failures* is available. The corpus harness had to re-run ALAO's
own `analyze_file_worker` over every file just to attribute failures, roughly doubling its runtime,
and `edits_dropped_overlap` is written as `null` in every `results.json` because there is no source
for it. That missing counter is exactly what would have exposed I-008 on day one. Change:
`reporter.py` — add `parse_failures`, `timeouts`, `crashes` (full paths and error text),
`findings_by_pattern`, `findings_by_severity`, and per-file edit and dropped-edit counts. Measure:
the harness drops its duplicate analyze pass and corpus run time roughly halves. Gain: high.
Risk: low.

**I-005 — `pairs(t)` -> `ipairs(t)` on provably array-like tables** *(pattern, 8.2)*
Hypothesis: 4.41x on a compiled trace, and `pairs` is by far the most common iteration construct in
the corpus. Change: new analyzer pass accepting only tables built by a literal with contiguous
integer keys or by append in the same scope, with no non-integer key assignment anywhere; emit
YELLOW, gated on the I-013 classification. Measure: bench in both modes plus differential tests on
tables with holes, string keys and embedded `nil`. Gain: high. Risk: high — it changes iteration
order, silently truncates at the first hole, and is 0.33x in the interpreter. Corpus: 2333 `pairs`
calls, 256 inside loops, 50 inside per-frame bodies.

### Proposed

**I-031 — Make `global_write` grouped or default-off** *(analyzer, 8.0)* — 5697 findings, 39% of
everything ALAO reports, RED so never acted on, and Anomaly mod style writes globals on purpose.
Change `_analyze_global_writes` to emit one grouped finding per function with a count, the way
`uncached_globals_summary` already does, and add a `--no-global-writes` switch. Measure: total
finding count drops sharply while distinct affected functions is unchanged. Gain: med, risk low.
Upgraded from the first draft now that the real share is known.

**I-017 — Differential execution harness under a stubbed Anomaly API** *(safety, 8.0)* — every
transform is argued safe in a comment and never demonstrated. New `tests/differential/` loading
original and rewritten chunks into two `lupa.luajit20` states behind a stub providing `db`, `alife`,
`level`, `vector`, `time_global`, comparing return values and recorded stub calls over generated
inputs. Slightly less urgent than in the first draft: the harness has now shown output compiles on
513 files and the one real bug was caught by idempotence, not by semantics. Gain high, risk med.

**I-009 — Scratch-vector reuse in loops** *(pattern, 8.0)* — 10.47x interpreted, 1.01x compiled, so
this is the flagship case for I-013's mode gating. ALAO already detects it (`vector_alloc_in_loop`,
78 findings) but only reports it RED. Promote to a YELLOW transform hoisting one
`local _v = vector()` and rewriting to `_v:set(...)`. Corpus: 1081 `vector()` constructions, 1007 of
them `vector():set(...)`, 52 in loops, 27 in per-frame bodies. Risk high: the vector must not escape
the iteration. Gain high.

**I-012 — Retarget the `math.pow(x,0.5)` rewrite at `math.sqrt`** *(pattern, 7.2)* — ALAO rewrites
`math.pow(x,0.5)` to `x^0.5`, which is a small genuine improvement (1.05x interpreted, 1.00x
compiled) and **not** the regression the first draft of this document claimed. But it leaves most of
the win on the table: `math.pow(x,0.5)` to `math.sqrt(x)` measures **4.25x** interpreted, and the
residual `x^0.5` to `math.sqrt(x)` step is still **4.12x**. Change `_analyze_math_pow` /
`_edit_math_pow` to emit `math.sqrt(x)`. Corpus: 25 `math.pow` sites, so this fails gate G1 on
volume and is cheap enough to do anyway. Gain med, risk low.

**I-002 — Confirm `table_insert_append` is worth keeping, then leave it alone** *(safety, 5.5)* —
the first draft of this document ranked this second overall on a measurement showing `t[#t+1]=v` at
0.77x under the JIT, and recommended demoting ALAO's most visible feature from GREEN. **That
measurement was an artifact of running without GC control and is retracted.** Corrected, and
confirmed independently by the team lead, the transform is **neutral under the JIT (1.00x) and
1.2-1.5x interpreted** — a small real win, no regression, nothing to demote. What remains is a
verification task, not a change: once I-013 can classify per-callback JIT mode, check that the 701
sites are not concentrated in JIT-compiled bodies where the fix buys nothing, and fix
`reporter.py::PERFORMANCE_IMPACT` and the README, which rate it "High" when the evidence says
"small, and only interpreted". Do not touch the transform before that classification exists.
Gain low, risk low. Corpus: 682 call sites, 701 findings.

**I-038 — `--fix` must not create new findings** *(safety, 7.5)* — re-analysis after the fix pass
shows YELLOW rising 3987 -> 3989: two new `string_concat_in_loop` findings were manufactured by
ALAO's own rewrites. Not a correctness problem, since YELLOW is never auto-fixed without
`--fix-yellow`, but a transform that creates work for itself is a smell and may compound once
`--fix-yellow` is in play. Change: identify the two sites from
`data/corpus/20260910-222415-gamma-fix/`, then guard the responsible `_edit_*` method. Measure:
gate G7 — post-fix findings are a subset of pre-fix findings. Gain med, risk low.

**I-014 — Replace the stale shipped report examples** *(corpus, 7.4)* — the two
`*_report_example.html` files in the repo root (22 MB and 4 MB) predate at least seven analyzer
passes and show a finding distribution that no longer matches reality: they report 1172
`table_insert_append` against today's 701, and zero `unnecessary_else` against today's 621. They are
the first thing a reader of the repo sees. Change: regenerate both from the current corpus runs, or
replace them with a link to the lab run records and drop 26 MB from the repo. Gain med, risk low.

**I-021 — Widen `EXPENSIVE_INDEXES` beyond `db.actor`** *(analyzer, 7.0)* — the table has exactly
one entry, and `repeated_db_actor` fires 78 times, while `db.actor` alone appears 306 times inside
per-frame callback bodies. Add `db.storage`, `db.offline_objects`, `db.script_ids`,
`db.actor_binder`; the repeated-call machinery already folds indexes into the same buckets as calls.
Gain med, risk low.

**I-010 — Per-frame callback cost model** *(analyzer, 6.9)* — `per_frame_callback` does fire (88
findings), but `PER_FRAME_CALLBACKS` lists only four names and misses all 97 class `:update` methods
on binders and `se_` classes, so it covers roughly a quarter of the 325 real per-frame bodies. Widen
it, then flag the expensive constructs measured inside those bodies: 306 `db.actor`, 145 concats, 50
`pairs`, 27 `vector()`, 18 `alife()`, 15 `game.translate_string`, 11 `level.object_by_id`.
Report-only, so risk is low. Gain med.

**I-036 — `alao_exclude.txt` ships with `VANILLA_SCRIPTS` in it** *(safety, 6.9)* — the file
auto-loads whenever it sits next to `stalker_lua_lint.py`, so anyone who names their vanilla corpus
folder the obvious thing gets a silent zero-file run with no warning. The corpus harness had to
override it with an empty list and add `--use-repo-exclude` to restore repo behaviour. Change: ship
the file empty or rename it to a sample, and print a loud line naming every excluded mod whenever
the exclude list is non-empty. Gain med, risk low. Cheap and purely a footgun removal.

**I-027 — Raise the nil-fix and dead-code actionable rate** *(safety, 6.8)* — `potential_nil_access`
is 1499 findings and `unused_local_variable` 1581, yet almost none are actionable: on a 207-file
sample only 8 of 215 nil findings (3.7%) and 0 of 317 dead-code findings (0%) were fixable. Either
`is_safe_to_fix` / `is_safe_to_remove` are too strict or the detectors over-report. Instrument
`_is_safe_nil_fix` to record a rejection reason, aggregate, relax the dominant reason. Gain med,
risk med.

**I-019 — Take the `table.insert` value span from the AST, not from text** *(transformer, 6.7)* —
`_extract_table_insert_value` splits on the first comma with no string awareness before it, though
the argument node already carries a span. Use `_get_node_span(call.args[1])`. Corpus diff must be
byte-identical since no corpus file triggers the bug today. Natural companion to I-008, same method
family. Gain low, risk low.

**I-022 — Cache loop-invariant `level.object_by_id(id)`** *(pattern, 6.6)* — 496 call sites, 209
inside loops, plus 47 `alife():object` with the same shape. The analyzer explicitly declines this
because different ids give different objects; restrict to loop-invariant arguments whose result is
not stored across frames. Gain med, risk high.

**I-026 — Wire up or delete `whole_program_analyzer.py`** *(tooling, 6.5)* — 424 lines of cross-file
symbol tracking imported nowhere. Either connect it behind `--whole-program` or remove it. Given
that `unused_local_function` already fires 71 times and cross-file dead code in a mod ecosystem is
dangerous to act on, report-only is the likely landing spot. Gain med, risk med.

**I-030 — Cache `game.translate_string` results** *(pattern, 6.4)* — 66 in loops, 15 in per-frame
bodies; a C call plus a string allocation whose result is immutable for the session. Suggest a
module-level memo keyed by string id, only when the key cannot grow unbounded. Gain med, risk med —
an unbounded memo is a leak in a game that runs for hours.

**I-035 — Separate the timeout counter from the parse-error counter** *(tooling, 6.3)* —
`stalker_lua_lint.py:738` routes anything matching `TimeoutError` into the "Files with parse errors"
bucket. A file that exceeded `--timeout` and a file that will not parse are completely different
problems and the summary cannot tell you which you have. Zero of each on the current corpus, which
is why nobody has noticed. Gain low, risk low. Pairs with I-029.

**I-023 — Cache `get_console()` more aggressively** *(pattern, 6.3)* — 141 `get_console():execute`
sites against a threshold of 4 that catches few of them. Measure the real cost of `get_console()`
first; if it is a cheap accessor this prunes. Gain low, risk low.

**I-037 — Per-file failure detail is unusable at corpus scale** *(tooling, 6.1)* — the per-file
error lines need `-v`, which also triggers `reporter.print_detailed()` and dumps all 14,455
findings; and those lines print `script_path.name`, so the many mods that all ship `ui_inventory.script`
cannot be told apart. Change: print full paths in failure lines and decouple failure verbosity from
finding verbosity. Gain low, risk low.

**I-015 — Cache analysis results by file identity** *(alao-perf, 5.9)* — a `.alao-cache.json` keyed
by path, mtime, size, ALAO version and the flags that affect analysis. Downgraded from the first
draft: the real analyze pass is 13.5 s for 1503 files at 8 workers, so this saves seconds, not
minutes. Worth it only if the harness starts running on every commit. Gain low, risk low.

**I-033 — Corpus runner emitting the contract `results.json`** *(corpus, 5.9, `kept`)* —
**delivered.** `tools/corpus_extract.py`, `tools/corpus_run.py` and `tools/corpus_compare.py` exist,
extract enabled mods only, run analyze and fix, compile-check every rewritten file under LuaJIT 2.0,
run the second pass for idempotence, and write `manifest.json` + `results.json` per the contract. A
full GAMMA analyze + fix + idempotence cycle is about 90 s at 8 workers. Every other idea's measure
clause depends on it. Recorded as `kept` rather than dropped so the dependency stays visible.

**I-020 — Add `--force-refix` for the test harness** *(safety, 5.8)* — files with an existing
`.alao-bak` are skipped, which is right for users and hides idempotence bugs from automated checks.
An explicit opt-in flag for tests and the corpus runner. Small enabler for I-008. Gain low, risk low.

**I-028 — Stop shipping AST nodes across the multiprocessing boundary** *(alao-perf, 5.5)* —
`Finding.details` carries `node`, `scope`, `calls` and `globals_info` holding live AST references,
all pickled back to the parent, which may never need them since the transformer re-runs the analyzer
in-process. Downgraded alongside I-015: total analyze time is 13.5 s, so the ceiling is small.
Gain low, risk med.

**I-024 — Drop redundant `tostring()` in concat chains** *(pattern, 5.4)* — 503 `.. tostring(`
sites; `..` already coerces numbers. Only safe when the operand is provably a number or string, and
behaviour changes for `nil`, which currently prints "nil" and would instead raise. Probably stays
report-only. Gain low, risk high.

**I-016 — Drop the per-file thread used for the timeout** *(alao-perf, 5.3)* —
`analyze_file_with_timeout` creates a one-worker `ThreadPoolExecutor` per file, so a corpus run
spins up 1503 executors to guard against a case that occurs zero times. Gain low, risk low.

**I-025 — Encoding fixtures from the 27 non-UTF-8 corpus files** *(corpus, 5.0)* — 18 CP1251 and
9 latin-1 files in the enabled corpus. Copy a handful into `tests/fixtures/encoding/` and assert
`--fix` preserves bytes outside the edited spans exactly. Regression protection, not a bug hunt:
`detect_file_encoding()` handles 100% of the corpus today. Gain low, risk low.

**I-018 — Retune the default `--timeout` from 10 s** *(alao-perf, 4.9)* — zero timeouts across 1503
files at the current default, and the slowest single file parses in about 1 s. A 3 s default would
fail faster on pathological input. Marginal. Gain low, risk low.

**I-007 — Stop swallowing parse and encoding failures** *(analyzer, 4.8)* — `analyze_file()` returns
`[]` for both unreadable and unparseable files, so a broken file looks clean. Downgraded hard from
the first draft, where I ranked it top-8 on the strength of 4 parse failures found by parsing every
file on disk. All four are in **disabled** mods; the enabled corpus has zero. The design hazard is
real and the fix is cheap, but the surfacing half of it is now covered by I-029. Gain low, risk low.

**I-006 — `table.remove(t)` tail removal -> `t[n]=nil`** *(pattern, 4.0)* — the largest measured
speedup in the whole table (13.44x / 5.21x) attached to almost no corpus volume. Downgraded: the
enabled corpus contains **exactly one** 1-argument `table.remove` call, out of 42 total. Fails gate
G1 decisively. Recorded because the measurement is worth keeping. Gain low, risk med.

**I-032 — Context-gate `math.pow(x,2)` -> `x*x`** *(pattern, 2.0)* — **retracted.** The first draft
filed this because `x*x` measured 0.81x under the JIT and looked like a mode-dependent regression
worth gating. Corrected, it is 1.00x compiled and 1.58x interpreted: a small win in one mode and
harmless in the other, exactly what a GREEN fix should be. Nothing to gate and nothing to do. Kept
in the list only so the retraction is on the record next to the claim. Expect `pruned`.

**I-011 — Hoist `#t` out of a numeric `for` bound** *(pattern, 3.0)* — 764 corpus hits and a measured
speedup of **1.00x in both modes**, because LuaJIT already hoists the length itself.
Kept in the beam deliberately as the worked example of an idea that hit count alone would have
promoted straight to the top and that measurement kills. Expect `pruned`.

**I-034 — `string.sub(s,1,1) == 'c'` -> `string.byte`** *(pattern, 2.8)* — 1.00x compiled and 1.10x
interpreted over 87 `string.sub` sites: below the G2 threshold of 1.15x. LuaJIT interns short
strings, so the allocation the folklore worries about does not happen. Expect `pruned`.

---

## 7. What we learned about the corpus

**The corpus is 1503 files, not 2079.** The live `G.A.M.M.A` profile has 577 enabled mods, 191
disabled and 29 separators; 361 of the enabled mods ship scripts, totalling 1503 files and 14.2 MB.
2108 files exist across all 804 mod folders on disk, which is where `CONTRACT.md`'s 2079 comes from
— an all-mods figure, not an enabled-only one. Anything measured over the full on-disk set
overstates by roughly 40%, which is why every count in section 4 was recomputed.

**It is clean, and ALAO handles it.** All 1503 files decode (1476 UTF-8, 18 CP1251, 9 latin-1) with
zero encoding failures. Zero parse failures, zero analyzer timeouts, zero crashes. The four
`SyntaxException` files I originally found by parsing every file on disk all live in **disabled**
mods, and at least one of them (`410- 3DSS for GAMMA .. zzz_mspizza_Godis_ZoomCalc.script`) is
genuinely invalid Lua that LuaJIT 2.0 also rejects with `unexpected symbol near '/'`. So the
`luaparser` 4.2.0 versus `>=3.0.0` drift in `requirements.txt` is a documentation problem, not a
corpus problem. Pin it anyway.

**ALAO's rewrites are byte-safe and they land.** 513 of 1503 files rewritten with 4743 edits, zero
LuaJIT 2.0 compile failures, and GREEN findings drop 2023 -> 79 on re-analysis. The 79 stragglers
are 8 `table_insert_append` (the I-008 bug) and 71 `repeated_*` opportunities newly visible only
because pass 1 rewrote the surrounding code. This is a good baseline to defend, and it is why the
beam's centre of gravity moved from "is ALAO safe" to "is what ALAO does worth doing".

**The optimization headroom is real but concentrated.** Only 113 of 1503 files (7.5%) contain any
`local mfloor = math.floor` style cache, so 92.5% of the corpus has never been hand-optimized. But
caching wins measure 1.0x-1.2x on compiled traces, so that headroom is worth much less than the raw
number suggests. The allocation-shaped patterns are where the corpus is actually expensive: 1081
`vector()` constructions, 1711 string concatenations inside loops, 2333 `pairs()` iterations.

**Per-frame bodies are where to aim, and ALAO sees a quarter of them.** 228 engine per-frame
callbacks plus 97 class `:update` methods = 325 bodies. Inside them: 306 `db.actor` reads, 145
concats, 50 `pairs`, 27 `vector()`, 18 `alife()`, 15 `game.translate_string`, 11
`level.object_by_id`, 5 `tostring`, 3 `ini_file:r_*` reads. `PER_FRAME_CALLBACKS` lists four names
and catches none of the 97 `:update` methods.

Representative files worth using as fixtures:

| File | Why |
|---|---|
| `G.A.M.M.A. UI/gamedata/scripts/ui_inventory.script` line 807 | the canonical I-008 dropped-edit case, `unpack_` inside `table.insert` |
| `108- Remove dropping weapons from damage - Great_Day/.../actor_effects.script` line 1533 | I-008 again via `mrandom` inside `table.insert`; also a `Cmeet_manager:update` per-frame body |
| `VANILLA_SCRIPTS/gamedata/scripts/luapanda.lua` lines 2185-3515 | five I-008 sites in one file, with adjacent correctly-rewritten lines for contrast |
| `109- MCM Mod Configuration Menu - RavenAscendant/.../ui_mcm.script` | `pairs` in loops, `table.insert` with an indexed first arg, `for i=1,#t` |
| `184- Body Health System - Grokitach/.../itms_manager.script` | `ItemProcessor:update` with `pairs` and `level.object_by_id` per frame |
| `11- Preblowout Murder - Ethylia/.../surge_manager.script` | `CSurgeManager:update` with `db.actor`, `vector()` and `object_by_id` in loops |
| `156- No Exos in the South - Grokitach/.../grok_nes.script` | `ini_file:r_*` inside `npc_on_update` |
| `410- 3DSS for GAMMA .../zzz_mspizza_Godis_ZoomCalc.script` (disabled mod) | genuinely invalid Lua, good negative fixture |
