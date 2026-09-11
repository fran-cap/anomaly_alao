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


def test_math_pow_half_becomes_sqrt(transform, compiles):
    # I-012: x^0.5 is the same C pow() call in the interpreter; math.sqrt is 3x faster
    out = transform(POW_SQRT)
    assert "math.pow" not in out
    assert "math.sqrt(x)" in out
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


# ---------------------------------------------------------------------------
# I-012: `x ^ 0.5` -> `math.sqrt(x)`
# ---------------------------------------------------------------------------

POW_OP_SQRT = """
function f(x)
    return x ^ 0.5
end
"""

POW_OP_SQRT_EXPR = """
function f(a, b)
    return (a + b) ^ 0.5
end
"""

POW_OP_SQRT_UNARY = """
function f(x)
    return -x ^ 0.5
end
"""

POW_OP_SQRT_DIVIDED = """
function f(x)
    return 1 / x ^ 0.5
end
"""

# near miss: a literal negative base is the one input where sqrt and ^0.5 differ
POW_OP_SQRT_NEGATIVE_LITERAL = """
function f()
    return (-2) ^ 0.5
end
"""

MATH_POW_SQRT_NEGATIVE_LITERAL = """
function f()
    return math.pow(-2, 0.5)
end
"""

# near miss: right-associative chain - only the inner ^0.5 is ours
POW_OP_SQRT_CHAIN = """
function f(a, b)
    return a ^ b ^ 0.5
end
"""


def test_pow_op_sqrt_triggers_green(analyze):
    finding = find_one(analyze(POW_OP_SQRT), "pow_op_sqrt")
    assert finding.severity == "GREEN"
    assert finding.line_num == 2


def test_pow_op_sqrt_becomes_a_sqrt_call(transform, compiles):
    out = transform(POW_OP_SQRT)
    assert "math.sqrt(x)" in out
    assert "^" not in out
    compiles(out)


def test_pow_op_sqrt_takes_a_compound_base(transform, compiles, analyze):
    # unlike x^2 -> x*x the base is evaluated once, so it need not be simple
    assert "pow_op_sqrt" in pattern_names(analyze(POW_OP_SQRT_EXPR))
    out = transform(POW_OP_SQRT_EXPR)
    assert "math.sqrt(a + b)" in out
    compiles(out)


def test_pow_op_sqrt_keeps_unary_minus_outside(transform, compiles):
    out = transform(POW_OP_SQRT_UNARY)
    assert "-math.sqrt(x)" in out
    compiles(out)


def test_pow_op_sqrt_keeps_surrounding_precedence(transform, compiles):
    out = transform(POW_OP_SQRT_DIVIDED)
    assert "1 / math.sqrt(x)" in out
    compiles(out)


@pytest.mark.parametrize("src", [POW_OP_SQRT_NEGATIVE_LITERAL,
                                 MATH_POW_SQRT_NEGATIVE_LITERAL])
def test_literal_negative_base_is_left_alone(analyze, src):
    # (-0)^0.5 is 0 but math.sqrt(-0) is -0, and (-1/0)^0.5 is inf but
    # math.sqrt(-1/0) is nan. We can't prove a variable's sign, but a written
    # negative we can simply decline.
    names = pattern_names(analyze(src))
    assert "pow_op_sqrt" not in names
    assert "math_pow_simple" not in names


def test_pow_op_sqrt_chain_rewrites_only_the_inner_power(transform, compiles, run_both):
    out = transform(POW_OP_SQRT_CHAIN)
    assert "a ^ math.sqrt(b)" in out
    compiles(out)
    run_both(POW_OP_SQRT_CHAIN, out, "f", 3, 16)


@pytest.mark.parametrize("src,arg", [
    (POW_OP_SQRT, 16),
    (POW_OP_SQRT, 2),
    (POW_OP_SQRT_UNARY, 9),
    (POW_OP_SQRT_DIVIDED, 4),
])
def test_pow_op_sqrt_preserves_behaviour(transform, run_both, src, arg):
    run_both(src, transform(src), "f", arg)


HOT_SQRT = """
function f(o)
    local p = math.sqrt(o.a) + math.sqrt(o.b) + math.sqrt(o.c) + math.sqrt(o.d)
    local q = o.e ^ 0.5
    return p + q
end
"""


def test_synthesized_sqrt_uses_the_hoisted_local(transform, compiles):
    # the uncached-globals cacher hoists math.sqrt here; the call we synthesize
    # from `o.e ^ 0.5` must use that local rather than a fresh global lookup
    out = transform(HOT_SQRT)
    assert "local msqrt = math.sqrt" in out
    assert "msqrt(o.e)" in out
    assert "math.sqrt(" not in out.split("local msqrt = math.sqrt", 1)[1]
    compiles(out)


FILE_LEVEL_ALIAS = """
local sqrt = math.sqrt

function f(x)
    return x ^ 0.5 + sqrt(x + 1)
end
"""

ALIAS_DECLARED_AFTER_USE = """
function f(x)
    return x ^ 0.5
end

local sqrt = math.sqrt
"""

ALIAS_SHADOWED_BY_PARAM = """
local sqrt = math.sqrt

function f(sqrt, x)
    return x ^ 0.5
end
"""


def test_existing_file_level_alias_is_reused(transform, compiles):
    out = transform(FILE_LEVEL_ALIAS)
    assert "sqrt(x)" in out
    assert "math.sqrt(x)" not in out
    compiles(out)


def test_alias_declared_after_the_use_is_not_used(transform, compiles):
    out = transform(ALIAS_DECLARED_AFTER_USE)
    assert "math.sqrt(x)" in out
    compiles(out)


def test_alias_shadowed_by_a_parameter_is_not_used(transform, compiles, run_both):
    out = transform(ALIAS_SHADOWED_BY_PARAM)
    assert "math.sqrt(x)" in out
    compiles(out)
    run_both(ALIAS_SHADOWED_BY_PARAM, out, "f", 2, 16)


# ---------------------------------------------------------------------------
# I-012 x distance_to_comparison: the two must not both claim the same source
# ---------------------------------------------------------------------------

DISTANCE_SQRT_COMPARE = """
function f(pos, t)
    if pos:distance_to_sqr(t) ^ 0.5 < 10 then return 1 end
end
"""

DISTANCE_PLAIN_COMPARE = """
function f(pos, t)
    if pos:distance_to(t) < 10 then return 1 end
end
"""


def test_sqrt_of_a_distance_does_not_double_fire(analyze, transform, compiles):
    # distance_to_comparison wants a bare :distance_to() as a comparison
    # operand; wrapped in ^0.5 the operand is an ExpoOp, so only ours matches
    # and exactly one edit lands.
    names = pattern_names(analyze(DISTANCE_SQRT_COMPARE))
    assert "pow_op_sqrt" in names
    assert "distance_to_comparison" not in names
    out = transform(DISTANCE_SQRT_COMPARE)
    assert "math.sqrt(pos:distance_to_sqr(t)) < 10" in out
    compiles(out)


def test_plain_distance_comparison_is_still_only_the_distance_fix(analyze, transform, compiles):
    names = pattern_names(analyze(DISTANCE_PLAIN_COMPARE))
    assert "distance_to_comparison" in names
    assert "pow_op_sqrt" not in names
    out = transform(DISTANCE_PLAIN_COMPARE)
    assert "distance_to_sqr(t) < 100" in out
    compiles(out)
