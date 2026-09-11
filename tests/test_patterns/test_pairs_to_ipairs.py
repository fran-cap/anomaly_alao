"""I-005: pairs(t) -> ipairs(t) on provably array-like tables.

The transform is a big win exactly once and a big loss the rest of the time:
3.3x-6.0x on a compiled trace (K = 1..2000) and 0.28x-0.70x in the interpreter,
where it never crosses 1.0x at any loop length (bench/pairs_to_ipairs.lua).
So the tests come in two halves:

* the sequence proof - ipairs stops at the first nil hole and pairs does not,
  so anything that could put a hole or a string key in the table must refuse;
* the JIT gate - the rewrite may only fire when the body genuinely compiles
  after the pairs calls are gone, which means the classifier has to be able to
  see through every call in that body.
"""

import pytest

from conftest import findings_named


def _hits(findings):
    return findings_named(findings, "pairs_to_ipairs")


def _one(findings):
    hits = _hits(findings)
    assert len(hits) == 1, [f"{f.severity}@{f.line_num}" for f in hits]
    return hits[0]


# ---------------------------------------------------------------------------
# the sequence proof: what counts as provably array-like
# ---------------------------------------------------------------------------

def test_append_built_table_is_a_sequence(analyze):
    f = _one(analyze("""
        function build()
            local t = {}
            for i = 1, 10 do t[#t+1] = i * 2 end
            local s = 0
            for _, v in pairs(t) do s = s + v end
            return s
        end
    """))
    assert f.severity == "GREEN"
    assert f.details["jit_mode"] == "compiled"
    assert f.details["table"] == "t"


def test_table_insert_two_arg_is_a_sequence(analyze):
    f = _one(analyze("""
        function build()
            local t = {}
            table.insert(t, 1)
            table.insert(t, 2)
            local s = 0
            for k, v in pairs(t) do s = s + v + k end
            return s
        end
    """))
    assert f.severity == "GREEN"


def test_literal_of_constants_is_a_sequence(analyze):
    f = _one(analyze("""
        function build()
            local t = {1, 2, 3}
            local s = 0
            for _, v in pairs(t) do s = s + v end
            return s
        end
    """))
    assert f.severity == "GREEN"


def test_literal_with_a_non_constant_element_is_refused(analyze):
    """`{x}` could be `{nil}`, which is a hole ipairs would stop at."""
    assert _hits(analyze("""
        function build(x)
            local t = {x, 2}
            local s = 0
            for _, v in pairs(t) do s = s + v end
            return s
        end
    """)) == []


def test_keyed_literal_is_refused(analyze):
    assert _hits(analyze("""
        function build()
            local t = {a = 1, 2}
            local s = 0
            for _, v in pairs(t) do s = s + 1 end
            return s
        end
    """)) == []


def test_string_key_assignment_is_refused(analyze):
    assert _hits(analyze("""
        function build()
            local t = {}
            t[#t+1] = 1
            t.name = "x"
            local s = 0
            for _, v in pairs(t) do s = s + 1 end
            return s
        end
    """)) == []


def test_non_append_index_write_is_refused(analyze):
    """`t[5] = 9` on a one-element table is a hole."""
    assert _hits(analyze("""
        function build()
            local t = {}
            t[#t+1] = 1
            t[5] = 9
            local s = 0
            for _, v in pairs(t) do s = s + 1 end
            return s
        end
    """)) == []


def test_table_escaping_into_a_call_is_refused(analyze):
    """The callee could add a string key or punch a hole; we cannot see it."""
    assert _hits(analyze("""
        function build()
            local t = {}
            t[#t+1] = 1
            helper(t)
            local s = 0
            for _, v in pairs(t) do s = s + 1 end
            return s
        end
    """)) == []


def test_returned_table_is_refused(analyze):
    assert _hits(analyze("""
        function build()
            local t = {}
            t[#t+1] = 1
            for _, v in pairs(t) do end
            return t
        end
    """)) == []


def test_table_captured_by_a_closure_is_refused(analyze):
    assert _hits(analyze("""
        function build()
            local t = {}
            t[#t+1] = 1
            local f = function() t.extra = 1 end
            for _, v in pairs(t) do end
            return f
        end
    """)) == []


def test_growing_the_table_inside_the_loop_is_refused(analyze):
    """pairs over a table being appended to is undefined; ipairs would see the
    new elements. Different undefined is still different."""
    assert _hits(analyze("""
        function build()
            local t = {}
            t[#t+1] = 1
            for _, v in pairs(t) do t[#t+1] = v end
            return t
        end
    """)) == []


def test_global_table_is_refused(analyze):
    """No `local` declaration in the function - anything could have touched it."""
    assert _hits(analyze("""
        function build()
            for _, v in pairs(some_global_table) do end
        end
    """)) == []


def test_parameter_table_is_refused(analyze):
    assert _hits(analyze("""
        function build(t)
            for _, v in pairs(t) do end
        end
    """)) == []


# ---------------------------------------------------------------------------
# the JIT gate
# ---------------------------------------------------------------------------

def test_interpreted_body_is_report_only(analyze):
    """string.format is NYIFF, so the loop cannot be traced either way and
    ipairs would be the slower of the two."""
    f = _one(analyze("""
        function build()
            local t = {}
            t[#t+1] = 1
            local s = 0
            for _, v in pairs(t) do s = s + v end
            return string.format("%d", s)
        end
    """))
    assert f.severity == "RED"
    assert f.details["jit_mode"] == "interpreted"
    assert f.details["is_safe_to_fix"] is False


def test_unknown_lua_call_makes_the_body_undecidable(analyze):
    """The classifier is intra-procedural: a call into another Lua function
    hides whatever that function aborts on, so `compiled` would be a guess."""
    f = _one(analyze("""
        function build()
            local t = {}
            t[#t+1] = 1
            local s = 0
            for _, v in pairs(t) do
                if utils_obj.npc_in_zone(v) then s = s + 1 end
            end
            return s
        end
    """))
    assert f.severity == "RED"
    assert f.details["jit_mode"] == "undecidable"
    assert f.details["calls_decidable"] is False


def test_unknown_method_makes_the_body_undecidable(analyze):
    f = _one(analyze("""
        function build()
            local t = {}
            t[#t+1] = 1
            local s = 0
            for _, v in pairs(t) do
                if v:section_name() == "x" then s = s + 1 end
            end
            return s
        end
    """))
    assert f.details["jit_mode"] == "undecidable"


def test_second_unconvertible_pairs_loop_blocks_the_first(analyze):
    """The body only compiles if EVERY pairs call in it goes away."""
    hits = _hits(analyze("""
        function build(other)
            local t = {}
            t[#t+1] = 1
            local s = 0
            for _, v in pairs(t) do s = s + v end
            for _, v in pairs(other) do s = s + v end
            return s
        end
    """))
    assert len(hits) == 1
    assert hits[0].severity == "RED"
    assert hits[0].details["jit_mode"] == "interpreted"


def test_module_level_loop_is_never_green(analyze):
    f = _one(analyze("""
        local t = {1, 2, 3}
        local s = 0
        for _, v in pairs(t) do s = s + v end
    """))
    assert f.severity == "RED"
    assert f.details["jit_mode"] == "unknown"


# ---------------------------------------------------------------------------
# the rewrite
# ---------------------------------------------------------------------------

GREEN_SRC = """
function build()
    local t = {}
    for i = 1, 5 do t[#t+1] = i * 2 end
    local s = 0
    for _, v in pairs(t) do s = s + v end
    return s
end
"""


def test_fix_swaps_only_the_iterator(transform):
    out = transform(GREEN_SRC)
    assert "for _, v in ipairs(t) do s = s + v end" in out
    assert "pairs(t)" not in out.replace("ipairs(t)", "")


def test_report_only_site_is_not_rewritten(transform):
    src = """
    function build()
        local t = {}
        t[#t+1] = 1
        local s = 0
        for _, v in pairs(t) do s = s + v end
        return string.format("%d", s)
    end
    """
    assert "pairs(t)" in transform(src)
    assert "ipairs(t)" not in transform(src)


def test_fix_is_idempotent(transform):
    once = transform(GREEN_SRC)
    twice = transform(once)
    assert once == twice


def test_rewrite_compiles(transform, compiles):
    assert compiles(transform(GREEN_SRC))


# ---------------------------------------------------------------------------
# semantic differential
# ---------------------------------------------------------------------------

def test_differential_append_built_table(transform, run_both):
    run_both(GREEN_SRC, transform(GREEN_SRC), "build")


def test_differential_literal_table(transform, run_both):
    src = """
    function build()
        local t = {4, 5, 6}
        local s = 0
        for k, v in pairs(t) do s = s + v * k end
        return s
    end
    """
    run_both(src, transform(src), "build")


def test_differential_empty_table(transform, run_both):
    src = """
    function build()
        local t = {}
        local n = 0
        for _, v in pairs(t) do n = n + 1 end
        return n
    end
    """
    run_both(src, transform(src), "build")


def test_differential_table_insert_built(transform, run_both):
    src = """
    function build()
        local t = {}
        table.insert(t, 10)
        table.insert(t, 20)
        table.insert(t, 30)
        local s = 0
        for k, v in pairs(t) do s = s + v + k end
        return s
    end
    """
    run_both(src, transform(src), "build")


@pytest.mark.parametrize("hostile", [
    # a hole: pairs sees 3 values, ipairs stops at index 1
    """
    function build()
        local t = {}
        t[#t+1] = 1
        t[3] = 3
        t[4] = 4
        local n = 0
        for _, v in pairs(t) do n = n + v end
        return n
    end
    """,
    # a string key: pairs sees it, ipairs never does
    """
    function build()
        local t = {}
        t[#t+1] = 1
        t.extra = 99
        local n = 0
        for _, v in pairs(t) do n = n + v end
        return n
    end
    """,
    # handed to a callee that punches a hole
    """
    function wreck(t) t[1] = nil end
    function build()
        local t = {}
        t[#t+1] = 1
        t[#t+1] = 2
        wreck(t)
        local n = 0
        for _, v in pairs(t) do n = n + v end
        return n
    end
    """,
])
def test_tables_where_ipairs_would_differ_are_never_rewritten(hostile, transform, lua_call):
    """The proof has to exclude exactly these. Each one would change the
    answer, so the only acceptable outcome is an untouched file."""
    out = transform(hostile)
    assert "ipairs" not in out
    # and the untouched program still means what it meant
    assert lua_call(hostile, "build") == lua_call(out, "build")
