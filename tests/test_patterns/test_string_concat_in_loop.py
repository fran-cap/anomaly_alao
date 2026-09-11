"""The experimental YELLOW fix: s = s .. x inside a loop becomes a parts table
plus one table.concat.

Read the header of `STRING_CONCAT_BREAKEVEN_ITERS` in ast_analyzer.py before
touching any of this. The short version, measured by agent-I039 on 2026-09-11:
the rewrite is NOT a free win. table.concat has setup cost, and below ~30
iterations the rewrite is slower than the naive concat. That is why the fix is
still gated behind --experimental and why a numeric `for` with a small literal
bound is deliberately not rewritten.
"""

import pytest

from conftest import find_one, findings_named, pattern_names


CONCAT_IN_LOOP = """
function f(n)
    local s = ""
    for i = 1, n do
        s = s .. tostring(i)
    end
    return s
end
"""

# near miss: the concat is outside any loop, so there is no quadratic growth
CONCAT_OUTSIDE_LOOP = """
function f(a, b)
    local s = ""
    s = s .. a
    s = s .. b
    return s
end
"""

# safety gate: not initialised to the empty string, so the fix would drop the seed
CONCAT_NON_EMPTY_SEED = """
function f(n)
    local s = "head:"
    for i = 1, n do
        s = s .. tostring(i)
    end
    return s
end
"""

# safety gate: the accumulator is read inside the loop, so it must stay a string
CONCAT_READ_INSIDE_LOOP = """
function f(n)
    local s = ""
    for i = 1, n do
        s = s .. tostring(i)
        log(s)
    end
    return s
end
"""

# safety gate: the accumulator is read *inside the concatenated expression*.
# There used to be a rescue that turned `s == ""` into `#parts == 0`; it is
# wrong whenever an appended piece is itself "", so it is gone.
CONCAT_EMPTY_CHECK_IN_EXPR = """
function f(t)
    local s = ""
    for i = 1, 3 do
        s = s .. (s == "" and "" or ",") .. t[i]
    end
    return s
end
"""

# safety gate: a closure created inside the loop captures the accumulator
CONCAT_CLOSURE_CAPTURE = """
function f(t)
    local s = ""
    local fns = {}
    for i = 1, 3 do
        s = s .. t[i]
        fns[i] = function() return s end
    end
    return s
end
"""

# safety gate: an early return hands the half-built accumulator out of the loop
CONCAT_RETURN_INSIDE_LOOP = """
function f(t)
    local s = ""
    for i = 1, 5 do
        s = s .. t[i]
        if i == 2 then return s end
    end
    return s
end
"""

# safety gate: one line, two locals. Replacing the whole init line would drop
# `extra` on the floor.
CONCAT_MULTI_LOCAL_INIT = """
function f(t)
    local s, extra = "", 42
    for i = 1, #t do
        s = s .. t[i]
    end
    return s, extra
end
"""

# perf gate: 5 iterations is far below the measured breakeven, so rewriting it
# makes the code SLOWER (0.53x interpreted). Report, do not fix.
CONCAT_SMALL_LITERAL_BOUND = """
function f(t)
    local s = ""
    for i = 1, 5 do
        s = s .. t[i]
    end
    return s
end
"""

# perf gate: 200 iterations is comfortably past the breakeven in both modes
CONCAT_LARGE_LITERAL_BOUND = """
function f(t)
    local s = ""
    for i = 1, 200 do
        s = s .. "x"
    end
    return s
end
"""

# shapes that are rewritten and must behave identically
CONCAT_EARLY_BREAK = """
function f(t, stop)
    local s = ""
    for i = 1, 10 do
        if i > stop then break end
        s = s .. t[i]
    end
    return s
end
"""

CONCAT_WHILE_LOOP = """
function f(t, n)
    local s = ""
    local i = 1
    while i <= n do
        s = s .. t[i]
        i = i + 1
    end
    return s
end
"""

CONCAT_ASSIGNED_AFTER_LOOP = """
function f(t, n)
    local s = ""
    for i = 1, n do
        s = s .. t[i]
    end
    s = s .. "!"
    return s
end
"""

# an accumulator of numbers: `..` coerces and so does table.concat, and the
# formatting must match exactly (%.14g both sides, including -0 and floats)
CONCAT_NUMBERS = """
function build()
    return {1, 1.5, -0, 1e15, 1/3, 2^53, -0.0, 1e300, 0, -17}
end
function f(n)
    local t = build()
    local s = ""
    for i = 1, n do
        s = s .. t[i] .. "|"
    end
    return s
end
"""


# ---------------------------------------------------------------------------
# detection
# ---------------------------------------------------------------------------

def test_concat_in_loop_is_flagged_yellow(analyze):
    finding = find_one(analyze(CONCAT_IN_LOOP), "string_concat_in_loop")
    assert finding.severity == "YELLOW"
    assert finding.line_num == 4


def test_concat_outside_a_loop_is_not_flagged(analyze):
    assert "string_concat_in_loop" not in pattern_names(analyze(CONCAT_OUTSIDE_LOOP))


def test_not_fixed_without_the_experimental_flag(transform_full):
    modified, content, count = transform_full(CONCAT_IN_LOOP)
    assert modified is False
    assert count == 0


def test_fix_yellow_alone_does_not_enable_it(transform_full):
    """It is --experimental, not --fix-yellow. Promoting it was evaluated
    (I-039) and declined: every rewritable site in the enabled GAMMA corpus is
    a 3-10 element UI string builder, i.e. below the breakeven."""
    modified, content, count = transform_full(CONCAT_IN_LOOP, fix_yellow=True)
    assert modified is False


# ---------------------------------------------------------------------------
# the trip-count gate
# ---------------------------------------------------------------------------

def test_literal_trip_count_is_recorded(analyze):
    f = find_one(analyze(CONCAT_SMALL_LITERAL_BOUND), "string_concat_in_loop")
    assert f.details["iter_bound"] == 5
    assert f.details["is_safe"] is False


def test_unknown_trip_count_stays_unknown(analyze):
    f = find_one(analyze(CONCAT_IN_LOOP), "string_concat_in_loop")
    assert f.details["iter_bound"] is None


def test_small_literal_loop_is_reported_but_not_rewritten(analyze, transform_full):
    assert "string_concat_in_loop" in pattern_names(analyze(CONCAT_SMALL_LITERAL_BOUND))
    modified, content, _ = transform_full(CONCAT_SMALL_LITERAL_BOUND, experimental=True)
    assert modified is False, f"a 5-iteration loop was rewritten (that is slower):\n{content}"


def test_large_literal_loop_is_rewritten(analyze, transform, compiles):
    f = find_one(analyze(CONCAT_LARGE_LITERAL_BOUND), "string_concat_in_loop")
    assert f.details["iter_bound"] == 200
    assert f.details["is_safe"] is True
    out = transform(CONCAT_LARGE_LITERAL_BOUND, experimental=True)
    assert "table.concat(_s_parts" in out
    compiles(out)


# ---------------------------------------------------------------------------
# the shape of the rewrite
# ---------------------------------------------------------------------------

def test_experimental_flag_rewrites_to_a_counted_parts_table(transform, compiles):
    out = transform(CONCAT_IN_LOOP, experimental=True)
    assert "local _s_parts, _s_n = {}, 0" in out
    assert "_s_n = _s_n + 1; _s_parts[_s_n] = tostring(i)" in out
    # explicit 1..n range: a nil operand must stay an error, not truncate
    assert 'table.concat(_s_parts, "", 1, _s_n)' in out
    compiles(out)


def test_rewrite_is_idempotent(transform):
    once = transform(CONCAT_IN_LOOP, experimental=True)
    twice = transform(once, experimental=True)
    assert twice == once


# ---------------------------------------------------------------------------
# differential execution - the rewritten shapes
# ---------------------------------------------------------------------------

def test_rewrite_preserves_the_built_string(transform, run_both):
    out = transform(CONCAT_IN_LOOP, experimental=True)
    for n in range(0, 25):
        run_both(CONCAT_IN_LOOP, out, "f", n)


def test_rewrite_preserves_early_break(transform, run_both):
    out = transform(CONCAT_EARLY_BREAK, experimental=True)
    src = CONCAT_EARLY_BREAK + """
function call_it(stop)
    local t = {}
    for i = 1, 10 do t[i] = "p" .. i end
    return f(t, stop)
end
"""
    out_full = out + """
function call_it(stop)
    local t = {}
    for i = 1, 10 do t[i] = "p" .. i end
    return f(t, stop)
end
"""
    for stop in range(-1, 12):
        run_both(src, out_full, "call_it", stop)


def test_rewrite_preserves_a_while_loop(transform, run_both):
    out = transform(CONCAT_WHILE_LOOP, experimental=True)
    tail = """
function call_it(n)
    local t = {}
    for i = 1, 20 do t[i] = "q" .. i end
    return f(t, n)
end
"""
    for n in range(0, 21):
        run_both(CONCAT_WHILE_LOOP + tail, out + tail, "call_it", n)


def test_rewrite_preserves_assignment_after_the_loop(transform, run_both):
    out = transform(CONCAT_ASSIGNED_AFTER_LOOP, experimental=True)
    tail = """
function call_it(n)
    local t = {}
    for i = 1, 20 do t[i] = "z" .. i end
    return f(t, n)
end
"""
    for n in range(0, 21):
        run_both(CONCAT_ASSIGNED_AFTER_LOOP + tail, out + tail, "call_it", n)


def test_rewrite_preserves_number_formatting(transform, run_both):
    """`..` and table.concat both format numbers with %.14g, including -0,
    fractions and values past 2^53. Proven rather than assumed."""
    out = transform(CONCAT_NUMBERS, experimental=True)
    for n in range(0, 11):
        run_both(CONCAT_NUMBERS, out, "f", n)


def test_a_nil_operand_still_raises(transform, lua_call):
    """The original raises "attempt to concatenate a nil value". The rewrite
    must also raise - `table.concat(parts)` over a hole would silently return a
    truncated string, which is strictly worse than a crash."""
    out = transform(CONCAT_IN_LOOP, experimental=True)
    src = """
function call_it()
    local t = {"a", nil, "c"}
    local s = ""
    for i = 1, 3 do
        s = s .. t[i]
    end
    return s
end
"""
    rewritten = """
function call_it()
    local t = {"a", nil, "c"}
    local _s_parts, _s_n = {}, 0
    for i = 1, 3 do
        _s_n = _s_n + 1; _s_parts[_s_n] = t[i]
    end
    local s = table.concat(_s_parts, "", 1, _s_n)
    return s
end
"""
    # sanity: that rewritten body is what the transformer actually emits
    assert 'table.concat(_s_parts, "", 1, _s_n)' in out
    ok_before, _ = lua_call(src, "call_it")
    ok_after, _ = lua_call(rewritten, "call_it")
    assert ok_before is False
    assert ok_after is False, "a nil operand was silently swallowed by the rewrite"


# ---------------------------------------------------------------------------
# shapes that must be declined
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("src", [
    CONCAT_NON_EMPTY_SEED,
    CONCAT_READ_INSIDE_LOOP,
    CONCAT_EMPTY_CHECK_IN_EXPR,
    CONCAT_CLOSURE_CAPTURE,
    CONCAT_RETURN_INSIDE_LOOP,
    CONCAT_MULTI_LOCAL_INIT,
], ids=["non_empty_seed", "read_inside_loop", "empty_check_in_expr",
        "closure_capture", "return_inside_loop", "multi_local_init"])
def test_unsafe_shapes_are_reported_but_not_rewritten(analyze, transform_full, src):
    # still worth reporting - it really is a quadratic loop
    assert "string_concat_in_loop" in pattern_names(analyze(src))
    # but the rewrite would change behaviour, so it must be declined
    modified, content, count = transform_full(src, experimental=True)
    assert modified is False, f"unsafe concat was rewritten:\n{content}"
