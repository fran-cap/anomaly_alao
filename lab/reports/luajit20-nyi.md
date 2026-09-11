# What LuaJIT 2.0 can and cannot compile — measured, not recalled

**Date:** 2026-09-11 · **Idea:** I-013 · **Harness:** `lab/tools/nyi_probe.py`
(`py -3.12 lab/tools/nyi_probe.py --md --json lab/data/luajit20-nyi.json`)

## The VM

| | |
|---|---|
| Probed VM | `lupa` 2.8, `lupa.luajit20` |
| `jit.version` | `LuaJIT 2.0.1774896119` |
| `jit.version_num` | `20099` (v2.0 branch head — 2.0.4 plus later 2.0-branch fixes) |
| `jit.arch` / `jit.os` | `x64` / `Windows` |
| `jit.status()` flags | `CMOV SSE2 SSE3 SSE4.1 AMD` + `fold cse dce fwd dse narrow loop abc sink fuse` |
| Target VM | `D:\...\Anomaly\bin\AnomalyDX11AVX.exe` embeds **LuaJIT 2.0.4**, same optimizer flag set (`fold cse dce fwd dse narrow loop abc sink fuse`), so allocation sinking is on in the game too |

The two are not byte-identical (the game is 2.0.4, the probe is the 2.0 branch head), but they
are the same major VM with the same optimizer flags, and every NYI recorded below is a
2.0-wide limitation rather than a point-release detail.

**The LuaJIT wiki's NYI page describes 2.1 and is wrong for us.** Nothing in this file comes
from it. Everything comes from running the construct in a hot loop under the VM above with
`jit.attach(cb, "trace")` attached and recording whether a trace *stopped* or *aborted*.

## Decoding the abort codes

The bundled lib ships no `jit.vmdef`, so the callback's `otr` argument arrives as a bare
integer. These four were observed, and each is pinned by a case that can only mean one thing:

| code | name | how it was pinned |
|---|---|---|
| 5 | `NYIBC` — NYI: bytecode N | `oerr` is a small integer. Only ever 36 (`BC_CAT`, from `..`) and 49 (`BC_FNEW`, from creating a closure in a loop). |
| 6 | `LINNER` — inner loop in root trace | fires for a nested `for` and for varargs *while the outer loop still compiles*. Benign trace shaping, **not** an NYI. |
| 13 | `NYICF` — NYI: C function | `oerr` is a raw `function: 0x...` pointer, not a `builtin#N`. Reproduced deliberately by calling a non-fastfunc C function. |
| 14 | `NYIFF` — NYI: FastFunc | `oerr` is a `builtin#N` with no compiled path in any shape (`pairs`, `next`, `string.gsub`, `os.clock`). |
| 15 | `NYIFFU` — NYI: unsupported *variant* of a FastFunc | `oerr` is a builtin that compiles in another shape: `tostring(n)` compiles but `tostring(t)` aborts 15; `table.insert(t,v)` compiles but `table.insert(t,i,v)` aborts 15. |

Codes 5, 13, 14, 15 mean "cannot be compiled". Code 6 does not, and the classifier must not
count it — three of the cases below abort with `LINNER` and still compile.

## The headline: engine calls are uncompilable

Deliberate extra probe, outside the table: a **non-fastfunc C function** called in a hot loop
aborts every attempt with `NYICF (13)`, `oerr` a bare function pointer.

Anomaly's whole API surface — `level.object_by_id`, `alife()`, `time_global()`,
`game.translate_string`, `get_console():execute`, every method on an engine userdata such as
`obj:position()` or `obj:health()` — is exactly that: C functions registered by the engine
through LuaBind, none of which LuaJIT has a fast path for. **Any per-frame body that touches
the engine at all runs interpreted.** That single fact decides more about which ALAO transform
is worth applying than anything else in this file, and it is why the interpreted column of the
section-2 speedup table is the one that matters for real mod code.

(Caveat, stated plainly: the probe cannot call the real engine. It establishes the *mechanism* —
LuaJIT 2.0 aborts on any C function that is not one of its own fastfuncs — and the engine's
functions are not among LuaJIT's fastfuncs by construction. What the probe cannot tell you is
whether a particular engine call is unexpectedly cheap or expensive once interpreted.)

## What this means for ALAO's transforms

1. **`..` is NYI.** `BC_CAT` cannot be recorded at all in 2.0 — not even two operands, not even
   `'a' .. n`. The corpus has 1711 concatenations inside loops and 145 inside per-frame bodies,
   and *every one of them* poisons the enclosing trace. This is the strongest single argument in
   the beam for I-039 (promote `string_concat_in_loop` out of `--experimental`).
2. **`pairs` is NYI, `ipairs` is not.** `pairs`/`next` abort; `ipairs` and `for j = 1, #t`
   compile cleanly. But `pairs` -> `ipairs` measures 0.29x *interpreted*, so it is only a win in
   a body that is otherwise fully compiled — which, per the point above, is rare in mod code.
   Gate it hard.
3. **`table.insert(t, v)` compiles.** The two-argument form has a fast path; only the
   three-argument positional form aborts. So ALAO's flagship `table_insert_append` rewrite is
   not rescuing anything from the interpreter — consistent with its measured 1.00x compiled /
   1.2-1.5x interpreted (I-002).
4. **Closure creation in a loop is NYI** (`BC_FNEW`), which no ALAO pattern currently sees.
5. **`string.format`, `gsub`, `match`, `gmatch`, `find`, `rep`, `upper`, `lower`, `char`,
   `reverse` are all NYI**; `sub`, `byte`, `len` compile. `string_find_plain` (441 findings)
   does not make the call compilable — it only makes the interpreted call cheaper.
6. **`table.concat`, `table.sort`, `unpack` are NYI**; `table.getn` and `#t` compile.
7. **All of `math.*` compiles** except `randomseed` and `fmod`. `math.random` compiles.
   So the cached-`math.floor` transform is an interpreter-only win, as measured (0.90x/1.23x).
8. **`os.clock`/`time`/`date` are NYI.** The corpus uses `time_global()` (engine, also
   uncompilable) rather than `os.clock`, so this is moot in practice.
9. **Metatables, `setmetatable`, `__index` chains, method calls and table allocation all
   compile.** The scratch-vector idea (I-009) is therefore not fixing an NYI; it is an
   allocation win, and its 10.47x is interpreted-only — which, again, is the common case.

## Full result table

<!-- generated by lab/tools/nyi_probe.py --md -->

| construct | category | trace result | abort reasons |
|---|---|---|---|
| `numeric_for` | control | **compiled** | - |
| `while_loop` | control | **compiled** | - |
| `repeat_loop` | control | **compiled** | - |
| `nested_for` | control | **compiled** | `LINNER` |
| `arith_mul_div` | arith | **compiled** | - |
| `arith_mod_pow` | arith | **compiled** | - |
| `array_index` | table | **compiled** | - |
| `hash_index` | table | **compiled** | - |
| `array_store` | table | **compiled** | - |
| `table_new_in_loop` | table | **compiled** | - |
| `length_op_table` | table | **compiled** | - |
| `length_op_string` | string | **compiled** | - |
| `pairs` | iter | **interpreted** | `NYIFF: pairs` |
| `ipairs` | iter | **compiled** | - |
| `next_explicit` | iter | **interpreted** | `NYIFF: next` |
| `pairs_hash` | iter | **interpreted** | `NYIFF: pairs` |
| `numeric_for_over_len` | iter | **compiled** | - |
| `table_insert_append` | tablelib | **compiled** | - |
| `table_insert_pos` | tablelib | **interpreted** | `NYIFFU: table.insert` |
| `table_remove_tail` | tablelib | **compiled** | - |
| `table_remove_pos` | tablelib | **interpreted** | `NYIFFU: table.remove` |
| `table_concat` | tablelib | **interpreted** | `NYIFF: table.concat` |
| `table_sort` | tablelib | **interpreted** | `NYIFF: table.sort` |
| `table_getn` | tablelib | **compiled** | - |
| `unpack` | tablelib | **interpreted** | `NYIFF: unpack` |
| `string_format` | stringlib | **interpreted** | `NYIFF: string.format` |
| `string_format_s` | stringlib | **interpreted** | `NYIFF: string.format` |
| `string_sub` | stringlib | **compiled** | - |
| `string_byte` | stringlib | **compiled** | - |
| `string_char` | stringlib | **interpreted** | `NYIFF: string.char` |
| `string_len` | stringlib | **compiled** | - |
| `string_rep` | stringlib | **interpreted** | `NYIFF: string.rep` |
| `string_upper` | stringlib | **interpreted** | `NYIFF: string.upper` |
| `string_lower` | stringlib | **interpreted** | `NYIFF: string.lower` |
| `string_reverse` | stringlib | **interpreted** | `NYIFF: string.reverse` |
| `string_find_plain` | stringlib | **interpreted** | `LINNER`, `NYIFF: string.find` |
| `string_find_pattern` | stringlib | **interpreted** | `NYIFF: string.find` |
| `string_match` | stringlib | **interpreted** | `NYIFF: string.match` |
| `string_gmatch` | stringlib | **interpreted** | `NYIFF: string.gmatch` |
| `string_gsub` | stringlib | **interpreted** | `NYIFF: string.gsub` |
| `concat_two` | concat | **interpreted** | `NYIBC: CAT (string concatenation)` |
| `concat_many` | concat | **interpreted** | `NYIBC: CAT (string concatenation)` |
| `concat_number` | concat | **interpreted** | `NYIBC: CAT (string concatenation)` |
| `concat_tostring` | concat | **interpreted** | `NYIBC: CAT (string concatenation)` |
| `tostring_number` | baselib | **compiled** | - |
| `tostring_table` | baselib | **interpreted** | `NYIFFU: tostring` |
| `tonumber` | baselib | **compiled** | - |
| `type_call` | baselib | **compiled** | - |
| `rawget_rawset` | baselib | **compiled** | - |
| `select_hash` | baselib | **compiled** | - |
| `select_n` | baselib | **compiled** | - |
| `assert_call` | baselib | **compiled** | - |
| `lua_call` | func | **compiled** | - |
| `method_call` | func | **compiled** | - |
| `vararg_pack` | func | **compiled** | `LINNER` |
| `closure_in_loop` | func | **interpreted** | `NYIBC: FNEW (closure creation)` |
| `pcall_ok` | func | **compiled** | - |
| `pcall_error` | func | **interpreted** | `NYIFF: error` |
| `xpcall_ok` | func | **compiled** | - |
| `error_caught` | func | **interpreted** | `NYIBC: FNEW (closure creation)`, `NYIFF: error` |
| `coroutine_resume` | func | **interpreted** | `NYIFF: coroutine.create` |
| `coroutine_wrap_call` | func | **interpreted** | `NYIFF: coroutine.yield`, `NYIFF: function: builtin#35` |
| `string_method_colon` | func | **compiled** | - |
| `metatable_index_table` | meta | **compiled** | - |
| `metatable_index_func` | meta | **compiled** | - |
| `setmetatable_in_loop` | meta | **compiled** | - |
| `getmetatable` | meta | **compiled** | - |
| `math_floor` | math | **compiled** | - |
| `math_ceil` | math | **compiled** | - |
| `math_abs` | math | **compiled** | - |
| `math_sqrt` | math | **compiled** | - |
| `math_pow` | math | **compiled** | - |
| `math_min_max` | math | **compiled** | - |
| `math_sin_cos` | math | **compiled** | - |
| `math_random` | math | **compiled** | - |
| `math_randomseed` | math | **interpreted** | `NYIFF: math.randomseed` |
| `math_huge_cmp` | math | **compiled** | - |
| `math_fmod` | math | **interpreted** | `NYIFF: math.mod` |
| `math_modf` | math | **compiled** | - |
| `os_clock` | os | **interpreted** | `NYIFF: os.clock` |
| `os_time` | os | **interpreted** | `NYIFF: os.time` |
| `os_date` | os | **interpreted** | `NYIFF: os.date` |
| `io_write_devnull` | io | **compiled** | - |
| `global_read` | global | **compiled** | - |
| `global_write` | global | **compiled** | - |
| `global_func_call` | global | **compiled** | - |
| `nested_global_index` | global | **compiled** | - |
| `userdata_like_method` | engine | **compiled** | - |
| `concat_accumulator` | alao | **interpreted** | `NYIBC: CAT (string concatenation)` |
| `table_insert_in_loop` | alao | **compiled** | `LINNER` |
| `append_len_plus_one` | alao | **compiled** | `LINNER` |
| `cached_math_floor` | alao | **compiled** | - |
| `vector_alloc_in_loop` | alao | **compiled** | - |
| `distance_to_sqr` | alao | **compiled** | - |
| `pairs_then_concat` | alao | **interpreted** | `NYIFF: pairs` |
