"""math.pow / ^ folding - the GREEN patterns that turn a call into MUL opcodes."""

import pytest

from conftest import find_one, pattern_names


POW_SQUARE = """
function f(x)
    return math.pow(x, 2)
end
"""

POW_CUBE = """
function f(x)
    return math.pow(x, 3)
end
"""

POW_SQRT = """
function f(x)
    return math.pow(x, 0.5)
end
"""

# near miss: exponent is a variable, so there is nothing to fold
POW_VARIABLE_EXPONENT = """
function f(x, y)
    return math.pow(x, y)
end
"""

POW_OP = """
function f(x)
    return x^2
end
"""

# near miss: ^ with a non-foldable exponent stays put
POW_OP_VARIABLE = """
function f(x, y)
    return x^y
end
"""


@pytest.mark.parametrize("src", [POW_SQUARE, POW_CUBE, POW_SQRT])
def test_math_pow_triggers_green_on_line_2(analyze, src):
    finding = find_one(analyze(src), "math_pow_simple")
    assert finding.severity == "GREEN"
    assert finding.line_num == 2


def test_math_pow_variable_exponent_is_not_flagged(analyze):
    assert "math_pow_simple" not in pattern_names(analyze(POW_VARIABLE_EXPONENT))


def test_pow_operator_square_triggers(analyze):
    finding = find_one(analyze(POW_OP), "pow_op_simple")
    assert finding.severity == "GREEN"
    assert finding.line_num == 2


def test_pow_operator_variable_exponent_is_not_flagged(analyze):
    assert "pow_op_simple" not in pattern_names(analyze(POW_OP_VARIABLE))


def test_math_pow_square_becomes_multiplication(transform, compiles):
    out = transform(POW_SQUARE)
    assert "math.pow" not in out
    assert "x*x" in out
    compiles(out)


def test_math_pow_cube_becomes_three_factors(transform, compiles):
    out = transform(POW_CUBE)
    assert "x*x*x" in out
    compiles(out)


def test_math_pow_half_becomes_native_operator(transform, compiles):
    out = transform(POW_SQRT)
    assert "math.pow" not in out
    assert "x^0.5" in out
    compiles(out)


@pytest.mark.parametrize(
    "src,arg",
    [(POW_SQUARE, 7), (POW_CUBE, 3), (POW_SQRT, 16), (POW_OP, 5)],
)
def test_pow_fixes_preserve_behaviour(transform, run_both, src, arg):
    run_both(src, transform(src), "f", arg)


def test_pow_fixes_preserve_behaviour_on_negatives(transform, run_both):
    # x*x and math.pow(x,2) must agree for negative inputs too
    run_both(POW_SQUARE, transform(POW_SQUARE), "f", -4)
    run_both(POW_CUBE, transform(POW_CUBE), "f", -4)


MIXED = """
function f(x)
    return math.pow(x, 2) + math.pow(x, 3) + math.pow(x, 0.5)
end
"""


def test_three_pow_calls_on_one_line_all_get_fixed(transform, run_both, analyze):
    assert len([f for f in analyze(MIXED) if f.pattern_name == "math_pow_simple"]) == 3
    out = transform(MIXED)
    assert "math.pow" not in out
    run_both(MIXED, out, "f", 9)
