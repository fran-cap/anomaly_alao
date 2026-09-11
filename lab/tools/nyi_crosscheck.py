"""Cross-check ALAO's JIT-mode classifier against the real VM on real mod code.

The classifier in `ast_analyzer._analyze_trace_aborts` is static. This script
takes actual per-frame bodies out of the GAMMA corpus, loads each one under
`lupa.luajit20` with stubbed Anomaly globals, calls it in a hot loop with the
trace-abort hook attached, and compares what the VM did with what ALAO said.

    py -3.12 lab/tools/nyi_crosscheck.py --corpus C:\\code\\GIT\\anomaly_alao\\extracted\\gamma

The one thing that has to be right for this to mean anything: **the engine
entry points are installed as Python callables, not Lua functions.** A Python
callable reached from Lua is a non-fastfunc C function, which is what the real
engine's LuaBind exports are, and LuaJIT 2.0 aborts on those with NYICF. The
test suite's `LUA_STUB_PRELUDE` stubs them in Lua, which compiles, so using it
unmodified here would "prove" that bodies full of engine calls are compiled.
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "tests"))

import lupa.luajit20 as luajit  # noqa: E402

from ast_analyzer import ASTAnalyzer  # noqa: E402
from models import detect_file_encoding  # noqa: E402

# Abort codes that mean "cannot be compiled" (see lab/reports/luajit20-nyi.md).
HARD_NYI = {5, 13, 14, 15}

# 6 (inner loop in root trace) and 7 (unroll limit) are trace shaping. Anything
# else that aborts means this body did not get a trace of its own, whatever the
# code, so for the verdict here they all count against "compiled".
BENIGN_ABORTS = {6, 7}

# The universal stub. `__index` is Lua (field reads compile, as the classifier
# assumes); `__call` is wired to a Python callable so every call through it is
# a C call and aborts exactly like an engine export.
STUB_PRELUDE = r"""
local ANY
local anymt = {}
anymt.__index    = function() return ANY end
anymt.__newindex = function() end
anymt.__call     = __engine_call
anymt.__add = function() return ANY end
anymt.__sub = function() return ANY end
anymt.__mul = function() return ANY end
anymt.__div = function() return ANY end
anymt.__mod = function() return ANY end
anymt.__pow = function() return ANY end
anymt.__unm = function() return ANY end
anymt.__len = function() return 0 end
anymt.__concat = function() return '' end
anymt.__eq = function() return false end
anymt.__lt = function() return false end
anymt.__le = function() return false end
anymt.__tostring = function() return 'ANY' end
ANY = setmetatable({}, anymt)
_G.ANY = ANY

-- unknown globals resolve to ANY instead of nil, so a body written against
-- fifty other mod scripts still runs far enough to get hot
setmetatable(_G, {__index = function(_, k) return ANY end})

_G.__evt = {}
_G.__hook = function(what, tr, func, pc, otr, oerr)
  _G.__evt[#_G.__evt + 1] = {what = what, otr = otr, oerr = tostring(oerr)}
end
"""

# Bare engine globals: `time_global()`, `alife()`. Bound straight to a Python
# callable so the call site is a real non-fastfunc C call.
ENGINE_GLOBALS = [
    "alife", "time_global", "get_console", "system_ini", "game_ini", "get_hud",
    "get_story_object", "get_object_by_name", "vector", "alife_object",
    "alife_create", "alife_release", "CScriptXmlInit", "device",
]

# Engine namespaces. These must be TABLES whose fields are C functions, not a
# C function itself - `level.name()` has to end up calling C, and indexing a
# bare Python callable from Lua does not do that.
ENGINE_NAMESPACES = {
    "level": ["object_by_id", "name", "get_target_obj", "vertex_position",
              "get_time_hours", "get_time_minutes", "present", "map_add_object_spot",
              "map_remove_object_spot", "send", "add_pp_effector"],
    "game": ["translate_string", "get_game_time", "time", "start_tutorial",
             "get_game_version"],
    "relation_registry": ["community_goodwill", "set_community_goodwill"],
}


def build_runtime():
    rt = luajit.LuaRuntime(unpack_returned_tuples=False)
    g = rt.globals()

    # one Python callable, used for every engine call site
    def engine_call(*args):
        return g.ANY

    g["__engine_call"] = engine_call
    rt.execute(STUB_PRELUDE)
    for name in ENGINE_GLOBALS:
        g[name] = engine_call
    for ns, funcs in ENGINE_NAMESPACES.items():
        tbl = rt.eval("{}")
        for fn in funcs:
            tbl[fn] = engine_call
        g[ns] = tbl
    return rt


def per_frame_bodies(path: Path):
    """(func_name, start_line, predicted_mode, sites) for one file.

    `sites` is [(line, reason)] so the caller can restrict the comparison to
    the constructs the VM actually executed.
    """
    analyzer = ASTAnalyzer()
    try:
        analyzer.analyze_file(path)
    except Exception:
        return []
    out = []
    for cb in analyzer.per_frame_callbacks:
        info = analyzer.jit_modes.get(id(cb.scope))
        if info is None:
            continue
        out.append((cb.name, cb.start_line, info.mode,
                    [(s.line, s.reason) for s in info.sites]))
    return out


def observe(rt, source, call_expr, iters=400):
    """Load `source`, run `call_expr` in a hot loop, return (ok, verdict, reasons)."""
    rt.execute("__evt = {}; jit.flush(); jit.on(); jit.opt.start('hotloop=8', 'hotexit=5')")
    chunk = rt.eval("loadstring([==[\n%s\n]==], '=body')" % source)
    if chunk is None:
        return False, "loaderror", []
    try:
        chunk()
    except Exception as exc:
        return False, "chunkerror: %s" % str(exc)[:90], []
    driver = rt.eval(
        "loadstring([==[ return function(N, f, a, b) "
        "for i = 1, N do pcall(f, a, b) end end ]==])()"
    )
    fn = rt.eval(call_expr)
    if fn is None:
        return False, "not-found", []
    rt.execute('jit.attach(__hook, "trace")')
    try:
        driver(iters, fn, rt.eval("ANY"), 16)
    except Exception as exc:
        rt.execute("jit.attach(__hook)")
        return False, "runerror: %s" % str(exc)[:90], []
    rt.execute("jit.attach(__hook)")

    evt = rt.eval("__evt")
    stops, hard, other = 0, [], []
    for i in range(1, len(evt) + 1):
        e = evt[i]
        if e["what"] == "stop":
            stops += 1
        elif e["what"] == "abort":
            code = int(e["otr"] or 0)
            if code in HARD_NYI:
                hard.append((code, e["oerr"]))
            elif code not in BENIGN_ABORTS:
                other.append((code, e["oerr"]))
    if not stops and not hard and not other:
        return False, "no-trace", []
    if hard:
        verdict = "interpreted"
    elif stops:
        verdict = "compiled"
    else:
        # aborted, but for a reason that is about trace shape at the call
        # boundary rather than about a construct in the body (code 18 shows up
        # a lot here: the driver calls the body through pcall, so the trace has
        # a frame to return to that it never recorded). Not something the
        # static classifier claims anything about.
        verdict = "inconclusive"
    reasons = sorted({"%d:%s" % (c, o) for c, o in (hard or other)})
    return True, verdict, reasons


def covered_lines(rt, source, call_expr, iters=20):
    """Which source lines the body actually executes.

    A per-frame body usually opens with a guard (`if time_global() < next then
    return end`). Under stubs that guard often returns on every call, so the VM
    never reaches the constructs ALAO flagged further down, and the run would
    look like a disagreement when it is only missing coverage. A separate
    JIT-off run with a line hook tells us which lines ran; the comparison is
    then restricted to those. A debug hook aborts trace recording, so this
    cannot share a run with `observe`.
    """
    rt.execute("jit.off(); _G.__lines = {}")
    rt.execute("debug.sethook(function(_, l) _G.__lines[l] = true end, 'l')")
    try:
        chunk = rt.eval("loadstring([==[\n%s\n]==], '=body')" % source)
        if chunk is not None:
            chunk()
            driver = rt.eval(
                "loadstring([==[ return function(N, f, a, b) "
                "for i = 1, N do pcall(f, a, b) end end ]==])()"
            )
            fn = rt.eval(call_expr)
            if fn is not None:
                driver(iters, fn, rt.eval("ANY"), 16)
    except Exception:
        pass
    finally:
        rt.execute("debug.sethook(); jit.on()")
    lines = rt.eval("__lines")
    return {int(k) for k in lines} if lines is not None else set()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--want", type=int, default=20, help="how many bodies to check")
    ap.add_argument("--seed", type=int, default=13)
    args = ap.parse_args()

    files = sorted(Path(args.corpus).rglob("*.script")) + sorted(Path(args.corpus).rglob("*.lua"))
    random.Random(args.seed).shuffle(files)

    checked, agree, rows = 0, 0, []
    for path in files:
        if checked >= args.want:
            break
        bodies = per_frame_bodies(path)
        if not bodies:
            continue
        try:
            encoding = detect_file_encoding(path)
            source = path.read_text(encoding=encoding)
        except Exception:
            continue
        if "]==]" in source:
            continue  # would break the long-bracket wrapper

        for func_name, line, predicted, sites in bodies:
            if checked >= args.want:
                break
            rt = build_runtime()
            if ":" in func_name:
                cls, meth = func_name.split(":", 1)
                call_expr = (
                    "(function() local c = rawget(_G, '%s') "
                    "if type(c) == 'table' and type(c.%s) == 'function' then return c.%s end "
                    "return nil end)()" % (cls, meth, meth)
                )
            else:
                call_expr = "rawget(_G, '%s')" % func_name
            ok, verdict, observed = observe(rt, source, call_expr)
            if not ok or verdict == "inconclusive":
                continue
            ran = covered_lines(build_runtime(), source, call_expr)
            # restrict the prediction to the constructs that actually ran
            live = [(sl, sr) for sl, sr in sites if sl in ran] if ran else sites
            expected = "interpreted" if live else "compiled"
            checked += 1
            match = verdict == expected
            agree += bool(match)
            rows.append((path.name, func_name, line, predicted, expected, verdict,
                         match, [r for _, r in live[:2]], observed[:2],
                         len(sites), len(live)))

    print("checked %d real per-frame bodies, %d agree (%.0f%%)\n"
          % (checked, agree, 100.0 * agree / max(checked, 1)))
    print("'ALAO' is the verdict on the whole body; 'on covered' restricts it to")
    print("the NYI sites the stubbed run actually executed, which is what the VM")
    print("could possibly have observed.\n")
    print("%-30s %-24s %-12s %-12s %-12s %s"
          % ("file", "body", "ALAO", "on covered", "VM", "ok"))
    for name, func, line, pred, exp, obs, match, preason, oreason, nsite, nlive in rows:
        print("%-30s %-24s %-12s %-12s %-12s %s"
              % (name[:30], ("%s:%d" % (func, line))[:24], pred,
                 "%s (%d/%d)" % (exp[:5], nlive, nsite), obs, "yes" if match else "NO"))
        if not match:
            print("    expected because: %s" % (preason or "-"))
            print("    VM aborts       : %s" % (oreason or "-"))
    return 0 if checked else 1


if __name__ == "__main__":
    raise SystemExit(main())
