"""Scratch-vector reuse: `vector():set(...)` in a loop -> one hoisted `local _v`.

The whole pattern lives or dies on escape analysis. A vector that survives
the iteration - stored in a table, returned, captured by a closure, handed to
a callee we can't vouch for - must stay RED and must come back from --fix
byte-identical. Every "reject" case below is one of those.
"""

import pytest

from conftest import find_one, findings_named, pattern_names


YELLOW_FLAGS = dict(fix_yellow=True)


# --- accepted shapes -------------------------------------------------------

LOCAL_READ_ONLY = """
function call_it()
    local acc = 0
    for i = 1, 5 do
        local p = vector():set(i, i * 2, i * 3)
        acc = acc + p.x + p.y + p.z
    end
    return acc
end
"""

BARE_STATEMENT = """
function call_it()
    for i = 1, 3 do
        vector():set(i, 0, 0)
    end
    return "ok"
end
"""

PASSED_TO_COPYING_METHOD = """
function call_it()
    local parts = make_particles()
    for i = 1, 3 do
        parts:play_at_pos(vector():set(i, 0, 0))
    end
    return parts:played()
end
"""

PASSED_TO_DISTANCE_TO = """
function call_it()
    local origin = vector():set(0, 0, 0)
    local acc = 0
    for i = 1, 3 do
        acc = acc + origin:distance_to(vector():set(i, 0, 0))
    end
    return acc
end
"""

TWO_VECTORS_ONE_BODY = """
function call_it()
    local acc = 0
    for i = 1, 4 do
        local a = vector():set(i, 0, 0)
        local b = vector():set(0, i, 0)
        acc = acc + a.x + b.y
    end
    return acc
end
"""

NESTED_LOOPS = """
function call_it()
    local acc = 0
    for i = 1, 3 do
        for j = 1, 3 do
            local p = vector():set(i, j, 0)
            acc = acc + p.x * p.y
        end
    end
    return acc
end
"""

SELF_METHOD_ON_SCRATCH = """
function call_it()
    local acc = 0
    for i = 1, 3 do
        local p = vector():set(i, 0, 0)
        acc = acc + p:distance_to_sqr(p)
    end
    return acc
end
"""


# --- rejected shapes -------------------------------------------------------

STORED_IN_TABLE = """
function call_it()
    local out = {}
    for i = 1, 3 do
        out[i] = vector():set(i, 0, 0)
    end
    return out[1].x .. "/" .. out[2].x .. "/" .. out[3].x
end
"""

STORED_VIA_LOCAL = """
function call_it()
    local out = {}
    for i = 1, 3 do
        local p = vector():set(i, 0, 0)
        out[i] = p
    end
    return out[1].x .. "/" .. out[3].x
end
"""

RETURNED = """
function call_it()
    for i = 1, 3 do
        if i == 2 then
            return vector():set(i, 0, 0)
        end
    end
    return nil
end
"""

CAPTURED_BY_CLOSURE = """
function call_it()
    local fns = {}
    for i = 1, 3 do
        local p = vector():set(i, 0, 0)
        fns[i] = function() return p.x end
    end
    return fns[1]() .. "/" .. fns[3]()
end
"""

PASSED_TO_UNKNOWN_FUNCTION = """
function call_it()
    local c = make_collector()
    for i = 1, 3 do
        keep_it(c, vector():set(i, 0, 0))
    end
    return c:dump()
end

function keep_it(c, v) c:keep(v) end
"""

PASSED_TO_UNKNOWN_METHOD = """
function call_it()
    local c = make_collector()
    for i = 1, 3 do
        c:keep(vector():set(i, 0, 0))
    end
    return c:dump()
end
"""

ASSIGNED_TO_UPVALUE = """
local last = nil
function call_it()
    for i = 1, 3 do
        last = vector():set(i, 0, 0)
    end
    return last.x
end
"""

STORED_ON_SELF = """
function call_it()
    local self = {}
    for i = 1, 3 do
        local p = vector():set(i, 0, 0)
        self.pos = p
    end
    return self.pos.x
end
"""

IN_CLOSURE_INSIDE_A_LOOP = """
function call_it()
    local acc = 0
    for i = 1, 3 do
        local g = function()
            local p = vector():set(i, 0, 0)
            return p.x
        end
        acc = acc + g()
    end
    return acc
end
"""

LOOP_NOT_AT_LINE_START = """
function call_it()
    local acc = 0
    if true then for i = 1, 3 do local p = vector():set(i, 0, 0) acc = acc + p.x end end
    return acc
end
"""


ACCEPTED = {
    "local_read_only": LOCAL_READ_ONLY,
    "bare_statement": BARE_STATEMENT,
    "passed_to_copying_method": PASSED_TO_COPYING_METHOD,
    "passed_to_distance_to": PASSED_TO_DISTANCE_TO,
    "two_vectors_one_body": TWO_VECTORS_ONE_BODY,
    "nested_loops": NESTED_LOOPS,
    "self_method_on_scratch": SELF_METHOD_ON_SCRATCH,
}

REJECTED = {
    "stored_in_table": STORED_IN_TABLE,
    "stored_via_local": STORED_VIA_LOCAL,
    "returned": RETURNED,
    "captured_by_closure": CAPTURED_BY_CLOSURE,
    "passed_to_unknown_function": PASSED_TO_UNKNOWN_FUNCTION,
    "passed_to_unknown_method": PASSED_TO_UNKNOWN_METHOD,
    "assigned_to_upvalue": ASSIGNED_TO_UPVALUE,
    "stored_on_self": STORED_ON_SELF,
    "in_closure_inside_a_loop": IN_CLOSURE_INSIDE_A_LOOP,
}


# --- analyzer --------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(ACCEPTED))
def test_non_escaping_vector_is_yellow_and_fixable(analyze, name):
    findings = findings_named(analyze(ACCEPTED[name]), "vector_alloc_in_loop")
    assert findings, "expected at least one vector_alloc_in_loop finding"
    for f in findings:
        assert f.severity == "YELLOW", f.message
        assert f.details["is_safe_to_fix"] is True
        assert f.details["hoist_line"]


@pytest.mark.parametrize("name", sorted(REJECTED))
def test_escaping_vector_stays_red(analyze, name):
    findings = findings_named(analyze(REJECTED[name]), "vector_alloc_in_loop")
    assert findings, "expected at least one vector_alloc_in_loop finding"
    for f in findings:
        assert f.severity == "RED", f.message
        assert f.details["is_safe_to_fix"] is False


def test_vector_outside_a_loop_is_not_a_finding(analyze):
    src = """
    function f()
        local p = vector():set(1, 2, 3)
        return p
    end
    """
    assert "vector_alloc_in_loop" not in pattern_names(analyze(src))


def test_plain_vector_with_no_set_is_not_promoted(analyze):
    """`vector()` on its own isn't the shape we rewrite - only vector():set()."""
    src = """
    function f(n, out)
        for i = 1, n do
            out[i] = vector()
        end
    end
    """
    finding = find_one(analyze(src), "vector_alloc_in_loop")
    assert finding.severity == "RED"


# --- transformer -----------------------------------------------------------

@pytest.mark.parametrize("name", sorted(REJECTED))
def test_escaping_vector_is_never_rewritten(transform, name):
    src = REJECTED[name]
    out = transform(src, fix_yellow=True, experimental=True, fix_nil=True)
    assert "vector()" in out
    assert "local _v = vector()" not in out


@pytest.mark.parametrize("name", sorted(ACCEPTED))
def test_hoist_happens_only_with_fix_yellow(transform, name):
    """YELLOW means --fix-yellow, nothing less."""
    assert "local _v = vector()" not in transform(ACCEPTED[name])


@pytest.mark.parametrize("name", sorted(ACCEPTED))
def test_hoisted_output_compiles(transform, compiles, name):
    compiles(transform(ACCEPTED[name], **YELLOW_FLAGS))


@pytest.mark.parametrize("name", sorted(ACCEPTED))
def test_behaviour_is_unchanged(transform, run_both, name):
    src = ACCEPTED[name]
    out = transform(src, **YELLOW_FLAGS)
    assert out.strip() != src.strip(), "expected the hoist to change something"
    run_both(src, out, "call_it")


@pytest.mark.parametrize("name", sorted(ACCEPTED))
def test_hoist_is_idempotent(transform, write_script, name):
    once = transform(ACCEPTED[name], **YELLOW_FLAGS)
    twice = transform(once, path=write_script(once, name="pass2.script"), **YELLOW_FLAGS)
    assert twice == once


def test_hoist_lands_above_the_outermost_loop(transform):
    out = transform(NESTED_LOOPS, **YELLOW_FLAGS)
    lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
    decl = lines.index("local _v = vector()")
    first_for = next(i for i, ln in enumerate(lines) if ln.startswith("for "))
    assert decl < first_for


def test_two_vectors_get_distinct_scratch_names(transform):
    out = transform(TWO_VECTORS_ONE_BODY, **YELLOW_FLAGS)
    assert out.count("= vector()") == 2
    assert "_v:set" in out and "_v_alao:set" in out


def test_scratch_name_does_not_shadow_an_existing_local(transform, compiles):
    src = """
    function call_it()
        local _v = 100
        local acc = 0
        for i = 1, 3 do
            local p = vector():set(i, 0, 0)
            acc = acc + p.x + _v
        end
        return acc
    end
    """
    out = transform(src, **YELLOW_FLAGS)
    compiles(out)
    assert "local _v = 100" in out
    assert "local _v_alao = vector()" in out


def test_loop_that_does_not_start_its_line_is_left_alone(transform):
    """We insert at the start of the loop's line, so a loop sharing a line with
    something else would get a declaration spliced into the middle of a
    statement. Refuse instead."""
    out = transform(LOOP_NOT_AT_LINE_START, **YELLOW_FLAGS)
    assert out.strip() == LOOP_NOT_AT_LINE_START.strip()


def test_reused_scratch_really_is_one_object(transform, run_both):
    """The point of the whole thing: one allocation, N iterations."""
    src = """
    function call_it()
        local seen = 0
        for i = 1, 4 do
            local p = vector():set(i, 0, 0)
            seen = seen + p.x
        end
        return seen
    end
    """
    out = transform(src, **YELLOW_FLAGS)
    assert out.count("vector()") == 1
    run_both(src, out, "call_it")
