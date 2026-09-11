"""Dead code: `if false`, `while false`, constant conditions, unnecessary else,
unused locals, and the --remove-dead-code gate."""

import pytest

from conftest import find_one, pattern_names


IF_FALSE = """
function g()
    if false then
        local q = 9
    end
    return 0
end
"""

WHILE_FALSE = """
function g()
    while false do
        local q = 9
    end
    return 0
end
"""

# near miss: the chain still has a live branch, removing it would drop behaviour
IF_FALSE_WITH_ELSE = """
function g(c)
    if false then
        return 1
    elseif c then
        return 2
    end
    return 0
end
"""

CONSTANT_CONDITION = """
function f()
    if true then
        return 1
    end
    return 0
end
"""

UNNECESSARY_ELSE = """
function f(x)
    if x then
        return 1
    else
        return 2
    end
end
"""

UNUSED_LOCAL_VAR = """
function f()
    local unused = 5
    return 1
end
"""

UNUSED_LOCAL_FUNC = """
local function never_called()
    return 1
end
"""


def test_if_false_block_is_flagged_green(analyze):
    finding = find_one(analyze(IF_FALSE), "dead_code_if_false")
    assert finding.severity == "GREEN"
    assert finding.line_num == 2
    assert finding.details["is_safe_to_remove"] is True


def test_while_false_loop_is_flagged_green(analyze):
    finding = find_one(analyze(WHILE_FALSE), "dead_code_while_false")
    assert finding.severity == "GREEN"
    assert finding.details["is_safe_to_remove"] is True


def test_if_false_with_a_live_elseif_is_not_flagged(analyze):
    assert "dead_code_if_false" not in pattern_names(analyze(IF_FALSE_WITH_ELSE))


def test_constant_true_condition_is_yellow_only(analyze):
    finding = find_one(analyze(CONSTANT_CONDITION), "constant_condition")
    assert finding.severity == "YELLOW"


def test_unnecessary_else_is_yellow_only(analyze):
    finding = find_one(analyze(UNNECESSARY_ELSE), "unnecessary_else")
    assert finding.severity == "YELLOW"
    assert finding.line_num == 4


def test_unused_local_variable_is_flagged(analyze):
    finding = find_one(analyze(UNUSED_LOCAL_VAR), "unused_local_variable")
    assert finding.severity == "YELLOW"
    assert finding.line_num == 2


def test_unused_local_function_is_flagged(analyze):
    finding = find_one(analyze(UNUSED_LOCAL_FUNC), "unused_local_function")
    assert finding.severity == "YELLOW"


def test_a_used_local_is_not_flagged(analyze):
    src = """
    function f()
        local used = 5
        return used
    end
    """
    assert "unused_local_variable" not in pattern_names(analyze(src))


def test_a_called_local_function_is_not_flagged(analyze):
    src = """
    local function helper()
        return 1
    end

    function f()
        return helper()
    end
    """
    assert "unused_local_function" not in pattern_names(analyze(src))


# ---------------------------------------------------------------------------
# the removal itself
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("src", [IF_FALSE, WHILE_FALSE])
def test_nothing_is_removed_without_the_flag(transform, src):
    out = transform(src)
    assert "false" in out


def test_if_false_block_is_removed(transform, compiles):
    out = transform(IF_FALSE, remove_dead_code=True)
    assert "if false" not in out
    assert "local q = 9" not in out
    compiles(out)


def test_while_false_loop_is_removed(transform, compiles):
    out = transform(WHILE_FALSE, remove_dead_code=True)
    assert "while false" not in out
    compiles(out)


@pytest.mark.parametrize("src", [IF_FALSE, WHILE_FALSE])
def test_removal_preserves_behaviour(transform, run_both, src):
    run_both(src, transform(src, remove_dead_code=True), "g")


def test_unused_locals_are_reported_but_never_removed(transform):
    # YELLOW-only and not wired into the transformer, on purpose: a "unused"
    # local may still be there to hold a reference alive
    out = transform(UNUSED_LOCAL_VAR, remove_dead_code=True, fix_yellow=True)
    assert "local unused = 5" in out


def test_constant_condition_is_never_rewritten(transform):
    out = transform(CONSTANT_CONDITION, remove_dead_code=True, fix_yellow=True)
    assert "if true then" in out


def test_unnecessary_else_is_never_rewritten(transform):
    out = transform(UNNECESSARY_ELSE, remove_dead_code=True, fix_yellow=True)
    assert "else" in out


# --- known ALAO gaps ------------------------------------------------------

# In Lua 5.1 `return` must be the last statement of a block, so the only way to
# write unreachable code after a return is to wrap the return in a do-block.
DEAD_AFTER_RETURN = """
function f()
    do return 1 end
    local x = 2
    return x
end
"""

DEAD_AFTER_BREAK = """
function f()
    local n = 0
    for i = 1, 10 do
        do break end
        n = n + 1
    end
    return n
end
"""


@pytest.mark.xfail(
    strict=True,
    reason="_walk_for_dead_after_terminator (ast_analyzer.py:2514) only treats a "
           "Return/Break that is a direct child of the block as a terminator and "
           "never descends into Do blocks. Since Lua 5.1 forbids statements after "
           "a bare return, `do return end` is the only legal shape of this pattern "
           "and dead_code_after_return can never fire on parseable source.",
)
def test_unreachable_code_after_a_do_return_should_be_flagged(analyze):
    assert "dead_code_after_return" in pattern_names(analyze(DEAD_AFTER_RETURN))


@pytest.mark.xfail(
    strict=True,
    reason="same walker limitation as dead_code_after_return: `do break end` is "
           "not recognised as a terminator, so dead_code_after_break never fires.",
)
def test_unreachable_code_after_a_do_break_should_be_flagged(analyze):
    assert "dead_code_after_break" in pattern_names(analyze(DEAD_AFTER_BREAK))
