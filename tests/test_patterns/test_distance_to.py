"""distance_to(x) < N  ->  distance_to_sqr(x) < N*N - drops a sqrt per comparison."""

import pytest

from conftest import find_one, pattern_names


LESS_THAN = """
function f(pos, tgt)
    if pos:distance_to(tgt) < 10 then
        return true
    end
    return false
end
"""

GREATER_THAN = """
function f(pos, tgt)
    return pos:distance_to(tgt) > 5
end
"""

# near miss: the distance is kept, not compared, so squaring would change it
NOT_COMPARED = """
function f(pos, tgt)
    local d = pos:distance_to(tgt)
    return d
end
"""

# near miss: already the squared form
ALREADY_SQR = """
function f(pos, tgt)
    return pos:distance_to_sqr(tgt) < 100
end
"""


def test_less_than_comparison_is_flagged(analyze):
    finding = find_one(analyze(LESS_THAN), "distance_to_comparison")
    assert finding.severity == "GREEN"
    assert finding.line_num == 2


def test_greater_than_comparison_is_flagged(analyze):
    assert find_one(analyze(GREATER_THAN), "distance_to_comparison").line_num == 2


def test_bare_distance_is_not_flagged(analyze):
    assert "distance_to_comparison" not in pattern_names(analyze(NOT_COMPARED))


def test_already_squared_is_not_flagged(analyze):
    assert "distance_to_comparison" not in pattern_names(analyze(ALREADY_SQR))


def test_rewrite_squares_both_sides(transform, compiles):
    out = transform(LESS_THAN)
    assert "distance_to_sqr(tgt) < 100" in out
    assert "pos:distance_to(" not in out
    compiles(out)


def test_rewrite_preserves_the_comparison_result(transform, run_both):
    wrapper = """
    function call_it(d)
        local a = vector():set(0, 0, 0)
        local b = vector():set(d, 0, 0)
        return f(a, b)
    end
    """
    original = LESS_THAN + wrapper
    transformed = transform(LESS_THAN) + wrapper
    # inside, on, and outside the radius
    for d in (0, 9.5, 10, 10.5, 40):
        run_both(original, transformed, "call_it", d)
