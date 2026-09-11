"""Empirically determine what LuaJIT 2.0 can and cannot compile into a trace.

The LuaJIT wiki's NYI page describes 2.1 and is stale for our target. So instead of
trusting it, we run each candidate construct inside a hot loop under the exact VM we
ship against (`lupa.luajit20`) with `jit.attach(cb, "trace")` hooked up, and record
whether a trace *stopped* (compiled) or *aborted*, and with which abort reason.

    py -3.12 lab/tools/nyi_probe.py            # print the table
    py -3.12 lab/tools/nyi_probe.py --md       # markdown, for lab/reports/luajit20-nyi.md

The abort reason comes back as a numeric code (LuaJIT's `traceerr` index).  The
bundled lib has no `jit.vmdef`, so `TRACE_ERR` below maps the codes we actually
observed back to names, and any code we have not seen is reported as `err<N>`.
Author's note: the names are cross-checked against what the construct *is* -- if
code 4 only ever shows up for `pairs`/`next`/`string.gsub`, it is the NYI-fastfunc
bucket, whatever lj_traceerr.h calls it.
"""

from __future__ import annotations

import argparse
import json
import sys

try:
    import lupa.luajit20 as luajit
except ImportError:  # pragma: no cover
    print("lupa is required: pip install lupa", file=sys.stderr)
    raise SystemExit(2)


# Abort codes as they come back in the callback's `otr` argument.  The bundled lib
# ships no `jit.vmdef`, so these were decoded from the probe itself and each one is
# pinned by a case that can only mean one thing:
#   5  -- `oerr` is a small integer (a bytecode number), and it only fires for `..`
#         (BC_CAT) and for creating a closure in a loop (BC_FNEW): NYI bytecode.
#   6  -- fires for a nested `for` and for varargs while the outer loop still
#         compiles: the benign "inner loop in root trace", not an NYI at all.
#   14 -- `oerr` is a builtin, and the builtin has no compiled path in any form
#         (pairs, next, string.gsub, os.clock): NYI FastFunc.
#   15 -- `oerr` is a builtin that DOES compile in another shape: `tostring(n)`
#         compiles but `tostring(t)` aborts 15, `table.insert(t,v)` compiles but
#         `table.insert(t,i,v)` aborts 15: NYI unsupported *variant* of a FastFunc.
TRACE_ERR = {
    1: "RECERR",      # error thrown during recording
    2: "TRACEOV",     # trace too long
    3: "STACKOV",     # trace too deep
    4: "SNAPOV",      # too many snapshots
    5: "NYIBC",       # NYI: bytecode N
    6: "LINNER",      # inner loop in root trace (benign)
    7: "LUNROLL",     # loop unroll limit reached
    8: "BADTYPE",
    9: "CJITOFF",
    10: "CUNROLL",
    11: "DOWNREC",
    12: "NYIRETL",
    13: "NYICF",      # NYI: C function -- where engine calls would land
    14: "NYIFF",      # NYI: FastFunc
    15: "NYIFFU",     # NYI: unsupported variant of FastFunc
}

# Abort codes that actually mean "this cannot be compiled".  6 (inner loop) is
# normal trace shaping and 7 (unroll limit) is a budget, not an NYI.
HARD_NYI = {5, 13, 14, 15}

# Bytecode numbers seen in NYIBC aborts, named by what provoked them.
NYI_BYTECODE = {36: "CAT (string concatenation)", 49: "FNEW (closure creation)"}

# (name, category, loop body).  `t` is an 8-element array, `h` a small hash table,
# `s` a numeric accumulator, `str` a string, `D` a float table.
CASES = [
    # --- baseline / arithmetic -------------------------------------------------
    ("numeric_for",            "control", "s = s + D[(i % 64) + 1]"),
    ("while_loop",             "control", "local j = 0 while j < 3 do j = j + 1 end s = s + j"),
    ("repeat_loop",            "control", "local j = 0 repeat j = j + 1 until j >= 3 s = s + j"),
    ("nested_for",             "control", "for k = 1, 4 do s = s + k end"),
    ("arith_mul_div",          "arith",   "s = s + D[(i % 64) + 1] * 1.5 / 3.0"),
    ("arith_mod_pow",          "arith",   "s = s + (i % 7) + D[(i % 64) + 1] ^ 2"),

    # --- table access ----------------------------------------------------------
    ("array_index",            "table",   "s = s + t[(i % 8) + 1]"),
    ("hash_index",             "table",   "s = s + h.a"),
    ("array_store",            "table",   "t[(i % 8) + 1] = i"),
    ("table_new_in_loop",      "table",   "local u = {} u[1] = i s = s + u[1]"),
    ("length_op_table",        "table",   "s = s + #t"),
    ("length_op_string",       "string",  "s = s + #str"),

    # --- iteration -------------------------------------------------------------
    ("pairs",                  "iter",    "for k, v in pairs(t) do s = s + v end"),
    ("ipairs",                 "iter",    "for k, v in ipairs(t) do s = s + v end"),
    ("next_explicit",          "iter",    "local k, v = next(t) s = s + (v or 0)"),
    ("pairs_hash",             "iter",    "for k, v in pairs(h) do s = s + v end"),
    ("numeric_for_over_len",   "iter",    "for j = 1, #t do s = s + t[j] end"),

    # --- table library ---------------------------------------------------------
    ("table_insert_append",    "tablelib", "local u = {} table.insert(u, i) s = s + #u"),
    ("table_insert_pos",       "tablelib", "local u = {1,2,3} table.insert(u, 2, i) s = s + #u"),
    ("table_remove_tail",      "tablelib", "local u = {1,2,3} table.remove(u) s = s + #u"),
    ("table_remove_pos",       "tablelib", "local u = {1,2,3} table.remove(u, 1) s = s + #u"),
    ("table_concat",           "tablelib", "s = s + #table.concat(strs, ',')"),
    ("table_sort",             "tablelib", "local u = {3,1,2} table.sort(u) s = s + u[1]"),
    ("table_getn",             "tablelib", "s = s + table.getn(t)"),
    ("unpack",                 "tablelib", "local a, b = unpack(t) s = s + a + b"),

    # --- string library --------------------------------------------------------
    ("string_format",          "stringlib", "s = s + #string.format('%d', i)"),
    ("string_format_s",        "stringlib", "s = s + #string.format('%s-%s', 'a', str)"),
    ("string_sub",             "stringlib", "s = s + #string.sub(str, 1, 3)"),
    ("string_byte",            "stringlib", "s = s + string.byte(str, 1)"),
    ("string_char",            "stringlib", "s = s + #string.char(65, 66)"),
    ("string_len",             "stringlib", "s = s + string.len(str)"),
    ("string_rep",             "stringlib", "s = s + #string.rep('ab', 3)"),
    ("string_upper",           "stringlib", "s = s + #string.upper(str)"),
    ("string_lower",           "stringlib", "s = s + #string.lower(str)"),
    ("string_reverse",         "stringlib", "s = s + #string.reverse(str)"),
    ("string_find_plain",      "stringlib", "s = s + (string.find(str, 'cd', 1, true) or 0)"),
    ("string_find_pattern",    "stringlib", "s = s + (string.find(str, 'c.') or 0)"),
    ("string_match",           "stringlib", "s = s + #(string.match(str, '%a+') or '')"),
    ("string_gmatch",          "stringlib", "for w in string.gmatch(str, '%a') do s = s + 1 end"),
    ("string_gsub",            "stringlib", "s = s + #(string.gsub(str, 'a', 'b'))"),

    # --- concatenation ---------------------------------------------------------
    ("concat_two",             "concat",  "local x = 'a' .. str s = s + #x"),
    ("concat_many",            "concat",  "local x = 'a' .. str .. 'b' .. str .. 'c' s = s + #x"),
    ("concat_number",          "concat",  "local x = 'a' .. i s = s + #x"),
    ("concat_tostring",        "concat",  "local x = 'a' .. tostring(i) s = s + #x"),

    # --- conversion / misc base lib -------------------------------------------
    ("tostring_number",        "baselib", "s = s + #tostring(i)"),
    ("tostring_table",         "baselib", "s = s + #tostring(t)"),
    ("tonumber",               "baselib", "s = s + (tonumber('42') or 0)"),
    ("type_call",              "baselib", "if type(t) == 'table' then s = s + 1 end"),
    ("rawget_rawset",          "baselib", "rawset(h, 'a', i) s = s + rawget(h, 'a')"),
    ("select_hash",            "baselib", "s = s + select('#', 1, 2, 3)"),
    ("select_n",               "baselib", "s = s + (select(1, 7, 8))"),
    ("assert_call",            "baselib", "assert(t) s = s + 1"),

    # --- functions / varargs / closures ---------------------------------------
    ("lua_call",               "func",    "s = s + addone(i)"),
    ("method_call",            "func",    "s = s + obj:get()"),
    ("vararg_pack",            "func",    "s = s + varsum(1, 2, 3)"),
    ("closure_in_loop",        "func",    "local f = function() return i end s = s + f()"),
    ("pcall_ok",               "func",    "local ok, v = pcall(addone, i) s = s + v"),
    ("pcall_error",            "func",    "local ok, e = pcall(thrower) s = s + (ok and 0 or 1)"),
    ("xpcall_ok",              "func",    "local ok = xpcall(noop, noop) s = s + 1"),
    ("error_caught",           "func",    "local ok = pcall(function() error('x') end) s = s + 1"),
    ("coroutine_resume",       "func",    "local co = coroutine.create(noop) coroutine.resume(co) s = s + 1"),
    ("coroutine_wrap_call",    "func",    "s = s + wrapped()"),
    ("string_method_colon",    "func",    "s = s + #str:sub(1, 2)"),

    # --- metatables ------------------------------------------------------------
    ("metatable_index_table",  "meta",    "s = s + mt_tbl.inherited"),
    ("metatable_index_func",   "meta",    "s = s + mt_fn.anything"),
    ("setmetatable_in_loop",   "meta",    "local u = setmetatable({}, mt) s = s + (u.inherited or 0)"),
    ("getmetatable",           "meta",    "if getmetatable(mt_tbl) then s = s + 1 end"),

    # --- math ------------------------------------------------------------------
    ("math_floor",             "math",    "s = s + math.floor(D[(i % 64) + 1])"),
    ("math_ceil",              "math",    "s = s + math.ceil(D[(i % 64) + 1])"),
    ("math_abs",               "math",    "s = s + math.abs(D[(i % 64) + 1])"),
    ("math_sqrt",              "math",    "s = s + math.sqrt(D[(i % 64) + 1])"),
    ("math_pow",               "math",    "s = s + math.pow(D[(i % 64) + 1], 2)"),
    ("math_min_max",           "math",    "s = s + math.min(i, 5) + math.max(i, 5)"),
    ("math_sin_cos",           "math",    "s = s + math.sin(D[(i % 64) + 1]) + math.cos(D[(i % 64) + 1])"),
    ("math_random",            "math",    "s = s + math.random()"),
    ("math_randomseed",        "math",    "math.randomseed(1) s = s + 1"),
    ("math_huge_cmp",          "math",    "if D[(i % 64) + 1] < math.huge then s = s + 1 end"),
    ("math_fmod",              "math",    "s = s + math.fmod(i, 7)"),
    ("math_modf",              "math",    "local a, b = math.modf(D[(i % 64) + 1]) s = s + a"),

    # --- os / io ---------------------------------------------------------------
    ("os_clock",               "os",      "s = s + os.clock()"),
    ("os_time",                "os",      "s = s + os.time()"),
    ("os_date",                "os",      "s = s + #os.date('%H')"),
    ("io_write_devnull",       "io",      "sink:write('') s = s + 1"),

    # --- globals ---------------------------------------------------------------
    ("global_read",            "global",  "s = s + GLOBALNUM"),
    ("global_write",           "global",  "GLOBALNUM = i"),
    ("global_func_call",       "global",  "s = s + globaladd(i)"),
    ("nested_global_index",    "global",  "s = s + db.actor.health"),

    # --- userdata stand-in (a table behind __index, like the engine's objects) --
    ("userdata_like_method",   "engine",  "s = s + stubobj:position()"),

    # --- the shapes ALAO actually rewrites, as they appear in mod code ----------
    ("concat_accumulator",     "alao",    "acc = acc .. 'x' s = s + #acc"),
    ("table_insert_in_loop",   "alao",    "local u = {} for j = 1, 4 do table.insert(u, j) end s = s + #u"),
    ("append_len_plus_one",    "alao",    "local u = {} for j = 1, 4 do u[#u + 1] = j end s = s + #u"),
    ("cached_math_floor",      "alao",    "s = s + mfloor(D[(i % 64) + 1])"),
    ("vector_alloc_in_loop",   "alao",    "local v = newvec() v:set(1, 2, 3) s = s + v.x"),
    ("distance_to_sqr",        "alao",    "s = s + stubobj:distance_to_sqr()"),
    ("pairs_then_concat",      "alao",    "for k, v in pairs(t) do s = s + #('a' .. v) end"),
]

PRELUDE = r"""
local D = {}
for i = 1, 64 do D[i] = i * 0.5 end
local t = {1,2,3,4,5,6,7,8}
local h = {a = 1, b = 2, c = 3}
local str = 'abcdefgh'
local strs = {'a','b','c','d'}
local function addone(x) return x + 1 end
local function noop() return 1 end
local function thrower() error('boom') end
local function varsum(...) local n = 0 for j = 1, select('#', ...) do n = n + select(j, ...) end return n end
local obj = {v = 2} function obj:get() return self.v end
local mt = {__index = {inherited = 1}}
local mt_tbl = setmetatable({}, mt)
local mt_fn = setmetatable({}, {__index = function() return 1 end})
local stubobj = setmetatable({}, {__index = {
  position = function() return 1 end,
  distance_to_sqr = function() return 4.0 end,
}})
local sink = {write = function() end}
local wrapped = coroutine.wrap(function() while true do coroutine.yield(1) end end)
local mfloor = math.floor
local acc = ''
local vecmt = {__index = {set = function(self, x, y, z) self.x = x return self end}}
local function newvec() return setmetatable({x = 0}, vecmt) end
GLOBALNUM = 1
function globaladd(x) return x + 1 end
db = {actor = {health = 1.0}}
"""


BUILTIN_NAMES = {}


def _resolve_builtins(rt):
    """Map `function: builtin#N` back to a dotted library name."""
    rt.execute(r"""
_G.__names = {}
local function scan(tbl, prefix)
  if not tbl then return end
  for k, v in pairs(tbl) do
    if type(v) == 'function' and _G.__names[tostring(v)] == nil then
      _G.__names[tostring(v)] = prefix .. k
    end
  end
end
scan(_G, '')
for _, m in ipairs({'string','table','math','os','io','coroutine','debug','bit','jit'}) do
  scan(_G[m], m .. '.')
end
""")
    names = rt.eval("__names")
    for key in names:
        if "builtin#" in key:
            BUILTIN_NAMES[key] = names[key]
    # string.gmatch and string.gfind share a tostring in the scan above
    BUILTIN_NAMES.setdefault("function: builtin#88", "string.gmatch")


def _fmt_reason(code, oerr):
    name = TRACE_ERR.get(code, "err%d" % code)
    if code == 5:
        try:
            return "%s: %s" % (name, NYI_BYTECODE.get(int(oerr), "bytecode %s" % oerr))
        except (TypeError, ValueError):
            return name
    if code in (13, 14, 15):
        return "%s: %s" % (name, BUILTIN_NAMES.get(oerr, oerr))
    return name


def build_runtime():
    rt = luajit.LuaRuntime(unpack_returned_tuples=False)
    rt.execute(r"""
_G.__evt = {}
_G.__hook = function(what, tr, func, pc, otr, oerr)
  _G.__evt[#_G.__evt + 1] = {what = what, otr = otr, oerr = tostring(oerr)}
end
""")
    return rt


def probe(rt, name, body, iters=300):
    """Run `body` in a hot loop and report the trace events attributed to it."""
    src = "%s\nreturn function(N)\n  local s = 0\n  for i = 1, N do\n    %s\n  end\n  return s\nend\n" % (
        PRELUDE,
        body,
    )
    rt.execute("__evt = {}; jit.flush(); jit.on(); jit.opt.start('hotloop=20')")
    chunk = rt.eval("loadstring([==[\n%s\n]==], '=%s')" % (src, name))
    if chunk is None:
        return {"name": name, "status": "loaderror", "reasons": []}
    fn = chunk()
    rt.execute('jit.attach(__hook, "trace")')
    try:
        fn(iters)
        err = None
    except Exception as exc:  # a case that genuinely throws
        err = str(exc)[:120]
    rt.execute("jit.attach(__hook)")
    evt = rt.eval("__evt")
    stops, aborts = 0, []
    for i in range(1, len(evt) + 1):
        e = evt[i]
        what = e["what"]
        if what == "stop":
            stops += 1
        elif what == "abort":
            code = int(e["otr"] or 0)
            aborts.append((code, e["oerr"]))
    hard = [(c, o) for c, o in aborts if c in HARD_NYI]
    if hard:
        status = "interpreted"
    elif stops:
        status = "compiled"
    elif aborts:
        status = "mixed"
    else:
        status = "no-trace"
    reasons = sorted({_fmt_reason(c, o) for c, o in aborts})
    return {"name": name, "status": status, "stops": stops,
            "aborts": len(aborts), "reasons": reasons, "error": err}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--md", action="store_true", help="emit markdown")
    ap.add_argument("--json", metavar="PATH", help="also write raw results as JSON")
    args = ap.parse_args()

    rt = build_runtime()
    _resolve_builtins(rt)
    version = rt.eval("jit.version")
    version_num = rt.eval("jit.version_num")
    arch = "%s %s" % (rt.eval("jit.arch"), rt.eval("jit.os"))
    import lupa

    rows = []
    for name, cat, body in CASES:
        r = probe(rt, name, body)
        r["category"] = cat
        r["body"] = body
        rows.append(r)

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump({"lupa": lupa.__version__, "jit_version": version,
                       "jit_version_num": version_num, "arch": arch, "rows": rows},
                      fh, indent=2)

    if args.md:
        print("| construct | category | trace result | abort reasons |")
        print("|---|---|---|---|")
        for r in rows:
            print("| `%s` | %s | **%s** | %s |" % (
                r["name"], r["category"], r["status"],
                ", ".join("`%s`" % x for x in r["reasons"]) or "-"))
    else:
        print("lupa %s / %s (%s) %s" % (lupa.__version__, version, version_num, arch))
        for r in rows:
            print("%-24s %-12s %-12s %s" % (r["name"], r["category"], r["status"],
                                            "; ".join(r["reasons"])))


if __name__ == "__main__":
    main()
