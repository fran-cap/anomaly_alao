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

## What the reports do not tell you

Worth knowing if you are building a harness on top of ALAO rather than reading
its output by eye. Both are pinned as strict xfails in `test_cli.py`.

- **The JSON report carries findings only.** `reporter.py:368` `_save_json`
  writes `generated`, `summary` and `findings`. Parse failures, timeouts,
  crashes, per-file edit counts and dropped-edit counts appear nowhere, so
  anything about *failures* has to be scraped from stdout.
- **Timeouts are filed as parse errors.** `stalker_lua_lint.py:738` matches
  `TimeoutError` into the same branch as `SyntaxError` and counts it under
  "Files with parse errors", printing `[PARSE ERROR]` even with `-v`. A file
  ALAO simply ran out of time on looks like a file it could not parse.

Related: `--timeout` guards only the analyze phase. `transform_file_worker`
applies no timeout, so a file declared too slow to analyze is still fully
rewritten by `--fix`.
