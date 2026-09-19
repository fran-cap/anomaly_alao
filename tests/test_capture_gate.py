"""G9, the shadowing/capture gate (I-046): tools/capture_gate.py.

Two jobs here.

1. Prove the gate has TEETH. A gate that reports zero because it cannot see
   anything is worse than no gate - I-021 shipped two of those before this one
   worked. So every "clean at head" assertion below is paired with a run
   against the pre-fix behaviour on the same input, where the gate must fire.
   We cannot check out old code inside a test, so the two fixed holes are put
   back by hand: 18756a9's ancestor-scope union and I-046's own global sweep,
   both single methods on ASTTransformer.

2. Walk every insertion ALAO makes - cache decls, math/global aliases, the
   counter-append local, the scratch vector, the concat accumulator - with the
   name it wants already taken by an outer local, a parameter, an upvalue and
   a global, and assert it renames instead of binding over it.
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = REPO_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import capture_gate  # noqa: E402
from ast_transformer import ASTTransformer  # noqa: E402


# ---------------------------------------------------------------------------
# putting the two fixed holes back
# ---------------------------------------------------------------------------

def _collect_locals_without_ancestors(self, func_scope):
    """_collect_function_locals as it was at 18756a9^: body + descendants only."""
    if func_scope is None or self.analyzer is None:
        return set()
    target_id = id(func_scope)
    descendant_ids = {target_id}
    for s in self.analyzer.scopes:
        if id(s) == target_id:
            continue
        anc = s.parent
        while anc is not None:
            if id(anc) == target_id:
                descendant_ids.add(id(s))
                break
            anc = anc.parent
    names = set()
    for s in self.analyzer.scopes:
        if id(s) in descendant_ids:
            names.update(s.locals)
    return names


@pytest.fixture
def prefix_transformer(monkeypatch):
    """Run the gate against pre-fix transformer behaviour.

    `kind="locals"` reverts 18756a9 (ancestor scopes invisible), `kind="globals"`
    reverts I-046's sweep (the file's own globals invisible). Both are what
    shipped once; both are what the gate exists to catch.
    """
    def _apply(kind):
        if kind == "locals":
            monkeypatch.setattr(ASTTransformer, "_collect_function_locals",
                                _collect_locals_without_ancestors)
            monkeypatch.setattr(capture_gate.TaggingTransformer, "_collect_function_locals",
                                _collect_locals_without_ancestors, raising=False)
        elif kind == "globals":
            monkeypatch.setattr(ASTTransformer, "_file_global_names", lambda self: set())
            monkeypatch.setattr(capture_gate.TaggingTransformer, "_file_global_names",
                                lambda self: set(), raising=False)
        else:  # pragma: no cover - typo guard
            raise ValueError(kind)
    return _apply


@pytest.fixture
def scan(write_script):
    """Write a snippet and hand back the gate's ScanResult for it."""
    def _scan(src, name=None, **flags):
        path = write_script(src, name=name)
        flags.setdefault("fix_debug", True)
        flags.setdefault("fix_nil", True)
        return capture_gate.scan_file(path, **flags)
    return _scan


# ---------------------------------------------------------------------------
# the repros: the gate must flag them at the pre-fix behaviour
# ---------------------------------------------------------------------------

# 18756a9's own repro: drx_da_main_artefacts_movement.script. Pre-fix, the
# inserted `local tg` captured the module-level throttle and the guard became
# `if tg == tg then return end` - the function returns on its first line, for
# ever.
REPRO_TG_MODULE_LOCAL = """
local tg = 0

function update_artefacts()
    if time_global() == tg then return end
    tg = time_global()
    local a = time_global() + 1
    local b = time_global() + 2
    local c = time_global() + 3
    return a + b + c
end
"""

# the same hole with a module-level `local actor = "..."` and db.actor caching,
# which is what it looked like at bae4b0c
REPRO_ACTOR_MODULE_LOCAL = """
local actor = "the string an author parked here"

function who()
    local h = db.actor:health()
    local r = db.actor:rank()
    local n = db.actor:name()
    local i = db.actor:id()
    return actor, h, r, n, i
end
"""

# tasks_fetch.script: the insertion lands in a CLOSURE nested in a function
# whose PARAMETER is called actor, and the parameter is read after it. A
# differential run cannot see this one - under any stub the parameter and
# db.actor are the same object.
REPRO_TASKS_FETCH = """
function fetch_reward(actor, npc)
    local timer = function()
        local h = db.actor:health()
        local p = db.actor:position()
        local r = db.actor:rank()
        local n = db.actor:id()
        return h, p, r, n, actor:id()
    end
    return timer()
end
"""

# I-046's own: factionID_hud_mcm.script keeps its clock in a GLOBAL
REPRO_TG_GLOBAL = """
tg = 0
trigger = 0

function actor_on_update()
    tg = time_global()
    if trigger == 0 then
        grok_delay = tg + 100
        trigger = 1
    end
    local a = time_global() + 1
    local b = time_global() + 2
    return a + b
end
"""

REPROS_LOCALS = {
    "tg_module_local": (REPRO_TG_MODULE_LOCAL, "tg"),
    "actor_module_local": (REPRO_ACTOR_MODULE_LOCAL, "actor"),
    "tasks_fetch_parameter": (REPRO_TASKS_FETCH, "actor"),
}


@pytest.mark.parametrize("label", sorted(REPROS_LOCALS))
def test_gate_flags_the_18756a9_repros_at_prefix_behaviour(label, scan, prefix_transformer):
    src, name = REPROS_LOCALS[label]
    prefix_transformer("locals")
    res = scan(src, name=label + ".script")
    assert res.insertions >= 1, "nothing was inserted, so the repro proves nothing"
    assert [c.name for c in res.captures] == [name], res.summary()
    cap = res.captures[0]
    assert cap.reason == "ancestor-local"
    assert cap.read_line and cap.read_line > cap.line


def test_gate_flags_the_global_shadow_at_prefix_behaviour(scan, prefix_transformer):
    """I-046's own find: the same break one binding kind over."""
    prefix_transformer("globals")
    res = scan(REPRO_TG_GLOBAL, name="factionID.script")
    assert [c.name for c in res.captures] == ["tg"], res.summary()
    assert res.captures[0].reason == "free-name"


@pytest.mark.parametrize("label", sorted(REPROS_LOCALS))
def test_head_is_clean_on_the_same_repros(label, scan):
    src, name = REPROS_LOCALS[label]
    res = scan(src, name=label + ".script")
    assert res.insertions >= 1
    assert res.captures == [], res.summary()


def test_head_is_clean_on_the_global_repro(scan, transform):
    res = scan(REPRO_TG_GLOBAL, name="factionID.script")
    assert res.insertions >= 1
    assert res.captures == [], res.summary()
    out = transform(REPRO_TG_GLOBAL)
    # the global write survives, under a renamed cache
    assert "local tg_alao = time_global()" in out
    assert "\n    tg = tg_alao" in out


def test_the_prefix_output_really_is_broken(transform, prefix_transformer):
    """Sanity: the thing the gate flags is a genuine miscompile, not a style nit.

    `if tg == tg` is always true, so update_artefacts() returns on its first
    line. This is the known positive the gate is validated against.
    """
    prefix_transformer("locals")
    out = transform(REPRO_TG_MODULE_LOCAL)
    assert "local tg = time_global()" in out
    assert "if tg == tg then return end" in out


# ---------------------------------------------------------------------------
# the check itself, fed a hand-built edit list (no transformer involved)
# ---------------------------------------------------------------------------

def test_check_insertions_on_a_synthesized_edit(write_script):
    """The core check works off (source, edits, analyzer), so a caller can
    simulate any transformer, present or past."""
    from ast_analyzer import ASTAnalyzer
    from ast_transformer import SourceEdit

    src = REPRO_TG_MODULE_LOCAL
    path = write_script(src)
    an = ASTAnalyzer()
    an.analyze_file(path)
    source = an.source
    # insert `local tg = time_global()` at the start of the guard line
    anchor = source.index("    if time_global()")
    edit = SourceEdit(start_char=anchor, end_char=anchor,
                      replacement="    local tg = time_global()\n")
    res = capture_gate.check_insertions(source, [edit], an, "synthetic.script")
    assert len(res.captures) == 1
    assert res.captures[0].name == "tg"
    assert res.captures[0].reason == "ancestor-local"

    # the same edit under a name nobody uses is clean
    edit2 = SourceEdit(start_char=anchor, end_char=anchor,
                       replacement="    local tg_alao = time_global()\n")
    assert capture_gate.check_insertions(source, [edit2], an, "synthetic.script").captures == []


def test_a_shadow_nobody_reads_is_an_advisory_not_a_capture(write_script):
    from ast_analyzer import ASTAnalyzer
    from ast_transformer import SourceEdit

    src = """
local tg = 0

function f()
    local a = time_global()
    return a
end
"""
    path = write_script(src)
    an = ASTAnalyzer()
    an.analyze_file(path)
    anchor = an.source.index("    local a = time_global()")
    edit = SourceEdit(start_char=anchor, end_char=anchor,
                      replacement="    local tg = time_global()\n")
    res = capture_gate.check_insertions(an.source, [edit], an, "advisory.script")
    assert res.captures == []
    assert len(res.advisories) == 1
    assert res.advisories[0].reason == "ancestor-local"


def test_a_name_in_a_comment_is_not_a_read(write_script):
    """The occurrence sweep masks comments and strings - otherwise every
    `-- tg is the throttle` would be a fake capture."""
    from ast_analyzer import ASTAnalyzer
    from ast_transformer import SourceEdit

    src = """
local tg = 0

function f()
    local a = time_global()
    -- tg is the throttle, see above
    return a .. "tg"
end
"""
    path = write_script(src)
    an = ASTAnalyzer()
    an.analyze_file(path)
    anchor = an.source.index("    local a = time_global()")
    edit = SourceEdit(start_char=anchor, end_char=anchor,
                      replacement="    local tg = time_global()\n")
    res = capture_gate.check_insertions(an.source, [edit], an, "comment.script")
    assert res.captures == []


def test_an_insertion_that_never_lands_is_not_judged(scan):
    """--fix-debug comments out the only call site, so the enabler insertion is
    dropped by _apply_edits. A dropped insertion shadows nothing."""
    src = """
mfloor = "this file's own global"

function f(x)
    printf("%s", math.floor(x) .. math.floor(x + 1) .. math.floor(x + 2)
        .. math.floor(x + 3) .. math.floor(x + 4))
end
"""
    res = scan(src, name="dropped.script")
    assert res.captures == []


# ---------------------------------------------------------------------------
# every insertion kind, with the name already taken
# ---------------------------------------------------------------------------

UNCACHED_GLOBALS_BODY = """
    local a = math.floor(x)
    local b = math.floor(x + 1)
    local c = math.floor(x + 2)
    local d = math.floor(x + 3)
    local e = math.floor(x + 4)
"""

ADVERSARIAL = {
    # --- local alias enabler (`local mfloor = math.floor`)
    "alias_outer_local": ("""
local mfloor = "author's own"

function f(x)
%s
    return a + b + c + d + e, mfloor
end
""" % UNCACHED_GLOBALS_BODY, {}, "mfloor"),
    "alias_parameter": ("""
function f(mfloor, x)
%s
    return a + b + c + d + e, mfloor
end
""" % UNCACHED_GLOBALS_BODY, {}, "mfloor"),
    "alias_upvalue": ("""
function outer(mfloor)
    local inner = function(x)
%s
        return a + b + c + d + e, mfloor
    end
    return inner(1)
end
""" % UNCACHED_GLOBALS_BODY, {}, "mfloor"),
    "alias_global": ("""
mfloor = 41    -- this script's own global; other scripts read it

function f(x)
%s
    return a + b + c + d + e + mfloor
end
""" % UNCACHED_GLOBALS_BODY, {}, "mfloor"),

    # --- sqrt alias, which rides on the same hoist
    "sqrt_alias_global": ("""
msqrt = "this script's own global"

function f(a, b, c, d)
    local x = math.sqrt(a)
    local y = math.sqrt(b)
    local z = math.sqrt(c)
    local w = math.sqrt(d)
    local q = (a*a + b*b)^0.5
    return x + y + z + w + q + msqrt
end
""", {}, "msqrt"),

    # --- repeated_* cache decl
    "cache_outer_local": (REPRO_TG_MODULE_LOCAL, {}, "tg"),
    "cache_global": (REPRO_TG_GLOBAL, {}, "tg"),
    "cache_parameter_in_closure": (REPRO_TASKS_FETCH, {}, "actor"),

    # --- counter-append local (`local out_n = 0`)
    "counter_outer_local": ("""
local out_n = "author's"

function build(n)
    local out = {}
    for i = 1, n do
        out[#out+1] = i * 2
    end
    return out, out_n
end
""", dict(fix_yellow=True), "out_n"),
    "counter_parameter": ("""
function build(n, out_n)
    local out = {}
    for i = 1, n do
        out[#out+1] = i * 2
    end
    return out, out_n
end
""", dict(fix_yellow=True), "out_n"),
    "counter_global": ("""
out_n = 7

function build(n)
    local out = {}
    for i = 1, n do
        out[#out+1] = i * 2
    end
    return out, out_n
end
""", dict(fix_yellow=True), "out_n"),

    # --- scratch vector (`local _v = vector()`)
    "vector_outer_local": ("""
local _v = "author's"

function f(n)
    local out = 0
    for i = 1, n do
        local p = vector():set(i, i, i)
        out = out + p.x
    end
    return out, _v
end
""", dict(fix_yellow=True), "_v"),

    # --- string_concat_in_loop parts/counter locals
    "concat_parts_taken": ("""
local _s_parts = "author's"

function f(n)
    local s = ""
    for i = 1, n do
        s = s .. tostring(i)
    end
    return s, _s_parts
end
""", dict(fix_yellow=True, experimental=True), "_s_parts"),
}


@pytest.mark.parametrize("label", sorted(ADVERSARIAL))
def test_no_insertion_binds_over_a_taken_name(label, scan, transform, compiles):
    src, flags, taken = ADVERSARIAL[label]
    res = scan(src, name=label + ".script", **flags)
    assert res.insertions >= 1, f"{label}: nothing was inserted, the case is vacuous"
    assert res.captures == [], res.summary()
    out = transform(src, name=label + "_out.script", **flags)
    compiles(out)
    # it declared something, but not one more binding of the taken name than
    # the author already had
    needle = f"local {taken} ="
    assert out.count(needle) == src.count(needle), out


def test_every_declaring_insertion_site_is_covered_here():
    """Census guard over ast_transformer.py itself.

    Anyone who adds a new zero-width insertion that declares a local has to
    come back here and give it an adversarial case, or this fails. Matching is
    textual (`start_char=X, end_char=X` plus a `local ` in the replacement),
    which is exactly the shape the gate keys on.
    """
    import re
    src = (REPO_ROOT / "ast_transformer.py").read_text(encoding="utf-8")
    lines = src.split("\n")
    sites, current = set(), None
    for i, line in enumerate(lines):
        m = re.match(r"\s*def (\w+)", line)
        if m:
            current = m.group(1)
        if "SourceEdit(" not in line:
            continue
        block, depth, j = [], 0, i
        while j < len(lines):
            block.append(lines[j])
            depth += lines[j].count("(") - lines[j].count(")")
            j += 1
            if depth <= 0:
                break
        text = "\n".join(block)
        sa = re.search(r"start_char=(\w+)", text)
        ea = re.search(r"end_char=(\w+)", text)
        if not sa or not ea or sa.group(1) != ea.group(1):
            continue           # a replacement, not an insertion
        sites.add(current)

    known = {
        "_edit_append_loop",            # local <t>_n = 0
        "_edit_vector_alloc_in_loop",   # local _v = vector()
        "_edit_string_concat_in_loop",  # local <acc> = table.concat(...)
        "_edit_uncached_globals",       # local mfloor = math.floor (a block of them)
        "_edit_repeated_calls",         # local actor = db.actor / local tg = time_global()
        "_edit_string_find_plain",      # `, 1, true` - declares nothing, nothing to shadow
    }
    assert sites == known, (
        f"insertion sites changed: {sorted(sites)}.\n"
        "Add the new one to ADVERSARIAL in this file (outer local / parameter / "
        "upvalue / same-named global) before widening this set."
    )


@pytest.mark.parametrize("label,expected_kind", [
    ("alias_global", "uncached_globals"),
    ("cache_global", "repeated_calls[repeated_time_global]"),
    ("cache_parameter_in_closure", "repeated_calls[repeated_db_actor]"),
    ("counter_global", "append_loop"),
    ("vector_outer_local", "vector_alloc_in_loop"),
    ("concat_parts_taken", "string_concat_in_loop"),
])
def test_insertion_kinds_are_labelled(label, expected_kind, scan):
    src, flags, _taken = ADVERSARIAL[label]
    res = scan(src, name=label + ".script", **flags)
    assert expected_kind in res.by_kind, res.by_kind


# ---------------------------------------------------------------------------
# differential: the global-shadow fix, run under LuaJIT
# ---------------------------------------------------------------------------

GLOBAL_CLOCK = """
tg = 0
ticks = 0

function actor_on_update()
    tg = time_global()
    ticks = ticks + time_global() + time_global() + time_global()
    return tg
end

function read_the_global()
    return tg
end
"""


def test_a_global_the_file_writes_is_still_written_after_the_fix(transform, run_both):
    """The break the gate found: `tg = time_global()` under an inserted
    `local tg` becomes `tg = tg`, and the global is never assigned again. Other
    scripts (here `read_the_global`) then read nil for ever."""
    out = transform(GLOBAL_CLOCK)
    assert "local tg_alao = time_global()" in out
    run_both(GLOBAL_CLOCK, out, "actor_on_update")
    # and the global really survives: call the updater, then read the global
    driver = "\nfunction drive() actor_on_update() return read_the_global() end\n"
    run_both(GLOBAL_CLOCK + driver, out + driver, "drive")


def test_the_module_local_clock_still_throttles(transform, run_both):
    """18756a9's repro, differentially: the guard must not become always-true."""
    driver = """
function drive()
    local first = update_artefacts()
    local second = update_artefacts()
    return first, second
end
"""
    out = transform(REPRO_TG_MODULE_LOCAL)
    run_both(REPRO_TG_MODULE_LOCAL + driver, out + driver, "drive")
