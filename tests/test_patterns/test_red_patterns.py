"""RED findings: report only, never auto-fixed under any flag combination."""

import pytest

from conftest import find_one, findings_named, pattern_names


GLOBAL_WRITE = """
function f()
    my_global = 5
end
"""

# near miss: a local of the same shape
LOCAL_WRITE = """
function f()
    local my_local = 5
    return my_local
end
"""

VECTOR_IN_LOOP = """
function f(n)
    for i = 1, n do
        local v = vector():set(1, 2, 3)
    end
end
"""

# near miss: one allocation outside any loop is fine
VECTOR_OUTSIDE_LOOP = """
function f()
    local v = vector():set(1, 2, 3)
    return v
end
"""

PER_FRAME_WITH_WORK = """
function actor_on_update()
    local t = 1.5
    return math.floor(t) + math.floor(t + 1) + math.floor(t + 2)
end
"""

PER_FRAME_CLEAN = """
function actor_on_update()
    return 1
end
"""


def test_global_write_is_red(analyze):
    finding = find_one(analyze(GLOBAL_WRITE), "global_write")
    assert finding.severity == "RED"
    assert finding.line_num == 2
    assert "my_global" in finding.message


def test_local_write_is_not_flagged(analyze):
    assert "global_write" not in pattern_names(analyze(LOCAL_WRITE))


def test_vector_allocation_in_loop_is_red(analyze):
    finding = find_one(analyze(VECTOR_IN_LOOP), "vector_alloc_in_loop")
    assert finding.severity == "RED"
    assert finding.line_num == 3


def test_vector_allocation_outside_a_loop_is_not_flagged(analyze):
    assert "vector_alloc_in_loop" not in pattern_names(analyze(VECTOR_OUTSIDE_LOOP))


def test_per_frame_callback_with_uncached_globals_is_yellow(analyze):
    finding = find_one(analyze(PER_FRAME_WITH_WORK), "per_frame_callback")
    assert finding.severity == "YELLOW"
    assert "actor_on_update" in finding.message


def test_clean_per_frame_callback_is_informational(analyze):
    finding = find_one(analyze(PER_FRAME_CLEAN), "per_frame_callback")
    assert finding.severity == "DEBUG"


ALL_FIX_FLAGS = dict(
    fix_debug=True,
    fix_yellow=True,
    experimental=True,
    fix_nil=True,
    remove_dead_code=True,
)


@pytest.mark.parametrize(
    "src", [GLOBAL_WRITE, VECTOR_IN_LOOP, VECTOR_OUTSIDE_LOOP]
)
def test_red_findings_are_never_rewritten(transform, src):
    """Even with every fix flag on, RED patterns must come back untouched."""
    assert transform(src, **ALL_FIX_FLAGS).strip() == src.strip()


def test_clean_per_frame_callback_survives_fix_debug(transform):
    """per_frame_callback is emitted with DEBUG severity when the callback is
    clean, so it reaches the --fix-debug filter. There is no edit method for it
    and there must not be: commenting out a callback would break the mod."""
    out = transform(PER_FRAME_CLEAN, fix_debug=True)
    assert out.strip() == PER_FRAME_CLEAN.strip()
