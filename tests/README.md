# ALAO test suite

Run everything from the repo root:

```bash
py -3.12 -m pip install pytest lupa   # luaparser + jinja2 you already have
py -3.12 -m pytest -q                 # fast, hermetic, no game install needed
py -3.12 -m pytest -q --corpus        # also analyzes the real vanilla corpus, read-only
```

`pytest.ini` at the repo root sets `testpaths = tests` and registers the `corpus`
marker. Nothing in the suite ever writes outside pytest's `tmp_path`.

## Layout

| Path | What it covers |
|------|----------------|
| `conftest.py` | Fixtures, Lua execution helpers, the CLI runner |
| `test_patterns/` | One module per pattern family from the README tables |
| `test_transformer.py` | `SourceEdit` overlap resolution, enabler groups, encoding, `.alao-bak`, idempotence |
| `test_cli.py` | Subprocess runs of `stalker_lua_lint.py` end to end |
| `test_corpus_smoke.py` | Read-only pass over the vanilla Anomaly scripts (`--corpus` only) |

## How a pattern test is built

Every pattern family gets the same three-part treatment:

1. **A hit.** A minimal snippet that must produce the finding, asserted on
   pattern name, severity and line number.
2. **A near miss.** A snippet that looks like the pattern but must *not* trigger
   it, usually the exact shape the fix would break (`table.insert(t, 1, v)`,
   `not a == b`, `string.find(s, "a.c")`, a concat accumulator that is not seeded
   with `""`).
3. **A proof the fix is safe.** The rewritten source is compiled under LuaJIT,
   and where the behaviour can be pinned down, run differentially.

## The Lua side

`lupa` is used through `lupa.luajit20`, not the default `lupa.LuaRuntime`. The
default is Lua 5.5 and would accept syntax LuaJIT 2.0 rejects, which would make
the compile checks meaningless. Anomaly runs LuaJIT 2.0.4.

- `luajit_compiles(src)` -> `(ok, error)`, compile only, never runs the chunk.
- The `compiles` fixture is the assert-flavoured version.
- `run_both(original, transformed, fn, *args)` loads each side into its own fresh
  runtime with a fresh copy of the stubbed engine globals, calls `fn`, and
  compares the results. Separate runtimes matter: caching a stubbed singleton on
  one side must not be able to leak into the other.
- `lua_call(src, fn, *args)` runs under Lua's own `pcall` and returns
  `(ok, result_or_error)`. Use it when the call is *supposed* to fail, e.g. the
  nil-guard test that proves `--fix-nil` turns a crash into a `nil` return.

`LUA_STUB_PRELUDE` in `conftest.py` holds the fake engine: `log`/`printf` and
friends, `vector()` with `distance_to`/`distance_to_sqr`, `db.actor`, `alife()`,
`level.*`, `device()`, `system_ini()`, `get_console()`, `get_hud()`. Add to it
rather than defining stubs inside individual tests, so every differential run
sees the same world.

## Writing snippets

Snippets are plain triple-quoted strings; a leading newline and common
indentation are stripped, so they can sit at whatever indent reads best.
`write_script` puts them on disk because both the analyzer and the transformer
take a `Path` and do their own encoding detection - handing them a string would
test a code path the CLI never runs.

Two things to watch when writing Lua fixtures:

- **`return` must be the last statement in its block.** `return 1 local x = 2`
  is a syntax error, luaparser refuses it, and `analyze_file()` silently returns
  `[]`. A snippet that quietly fails to parse makes a test pass for the wrong
  reason, so assert on a positive finding wherever you can.
- Build tables inside Lua (`function call_it() return f({1, 2, 3}) end`) rather
  than passing a Python list through `run_both` - a Python list does not index
  like a Lua table.

## Known ALAO bugs

Tests that document a real defect are marked `xfail(strict=True)` with the cause
in the reason string. Strict means they fail the run if ALAO starts passing them,
which is the signal to delete the marker. `pytest -rx` lists them all with their
reasons.

The one that used to bite: a cacheable-global rewrite that lands *inside* a
`table.insert(...)` argument list is *contained* by the `table_insert_append`
rewrite of that call. `_apply_edits` used to treat the two as peers and drop
the container, so the append was never applied. Since 2026-09-10 the container
folds the inner edits into its own replacement text instead.
`test_fix_is_a_fixpoint_for_nested_edits` is the general guard: run `--fix`
twice and demand identical bytes. Anything a second pass still changes is an
optimization ALAO reported and then threw away.

## What the reports tell you now (I-029 / I-035 / I-037, 2026-09-11)

Three strict xfails in `test_cli.py` were dropped here because the defects they
documented are fixed. If you are building a harness on top of ALAO:

- **The JSON report carries failure and edit data**, not just findings.
  Alongside the original `generated` / `summary` / `findings`:
  `parse_failures`, `timeouts`, `crashes`, `compile_failures`, `fix_failures`,
  `findings_by_pattern`, `findings_by_severity`, per-file `edits`
  (`edits_generated` / `edits_applied` / `edits_dropped_overlap`),
  `edits_totals`, `alao_version`, `flags` and `run`. Paths are absolute.
- **A timeout is a timeout.** It has its own counter and its own
  "Files with timeouts: N" summary line; failure lines print the full path and
  do not need `-v`.
- **`--timeout` guards both phases**, and a file that failed analysis is not
  rewritten by `--fix` at all.
- **`--verify-compile`** (default on when `lupa` imports) LuaJIT-compiles a
  rewrite before writing it and refuses the write on failure;
  `test_transformer.py` injects a deliberately broken rewrite to prove it, and
  the inverse test proves the guard is what saves the file.

One thing the report still cannot tell you: nothing distinguishes a file that
ALAO analyzed cleanly from one where every finding was suppressed by a guard.
