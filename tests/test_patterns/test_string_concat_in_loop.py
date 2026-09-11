"""The experimental YELLOW fix: s = s .. x inside a loop becomes a parts table
plus one table.concat. O(n^2) garbage -> O(n)."""

import pytest

from conftest import find_one, pattern_names


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


def test_experimental_flag_rewrites_to_a_parts_table(transform, compiles):
    out = transform(CONCAT_IN_LOOP, experimental=True)
    assert "local _s_parts = {}" in out
    assert "_s_parts[#_s_parts+1] = tostring(i)" in out
    assert "table.concat(_s_parts)" in out
    compiles(out)


def test_experimental_rewrite_preserves_the_built_string(transform, run_both):
    out = transform(CONCAT_IN_LOOP, experimental=True)
    for n in (0, 1, 5):
        run_both(CONCAT_IN_LOOP, out, "f", n)


@pytest.mark.parametrize("src", [CONCAT_NON_EMPTY_SEED, CONCAT_READ_INSIDE_LOOP])
def test_unsafe_shapes_are_reported_but_not_rewritten(analyze, transform_full, src):
    # still worth reporting - it really is a quadratic loop
    assert "string_concat_in_loop" in pattern_names(analyze(src))
    # but the rewrite would change behaviour, so it must be declined
    modified, content, count = transform_full(src, experimental=True)
    assert modified is False, f"unsafe concat was rewritten:\n{content}"
