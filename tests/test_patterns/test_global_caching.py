"""Uncached-global caching: the threshold, the hot-callback N-1 rule, branch-aware
counting, cache-name collision handling and caching inside closures."""

import pytest

from conftest import find_one, pattern_names


FOUR_CALLS = """
function f(t)
    local n = 0
    n = n + math.floor(t[1])
    n = n + math.floor(t[2])
    n = n + math.floor(t[3])
    n = n + math.floor(t[4])
    return n
end
"""

THREE_CALLS = """
function f(t)
    return math.floor(t) + math.floor(t + 1) + math.floor(t + 2)
end
"""

HOT_CALLBACK_THREE_CALLS = """
function actor_on_update()
    local t = 1.5
    return math.floor(t) + math.floor(t + 1) + math.floor(t + 2)
end
"""

COLD_CALLBACK_THREE_CALLS = """
function some_cold_function()
    local t = 1.5
    return math.floor(t) + math.floor(t + 1) + math.floor(t + 2)
end
"""


def test_four_calls_hits_the_default_threshold(analyze):
    finding = find_one(analyze(FOUR_CALLS), "uncached_globals_summary")
    assert finding.severity == "GREEN"
    assert finding.line_num == 1


def test_three_calls_is_below_the_default_threshold(analyze):
    assert "uncached_globals_summary" not in pattern_names(analyze(THREE_CALLS))


def test_hot_callback_uses_threshold_minus_one(analyze):
    # 3 calls is below the default 4, but actor_on_update is a hot callback
    assert find_one(
        analyze(HOT_CALLBACK_THREE_CALLS), "uncached_globals_summary"
    ).severity == "GREEN"


def test_same_three_calls_in_a_cold_function_do_not_trigger(analyze):
    assert "uncached_globals_summary" not in pattern_names(
        analyze(COLD_CALLBACK_THREE_CALLS)
    )


def test_cache_threshold_option_is_honoured(analyze):
    src = """
    function f(t)
        return math.floor(t) + math.floor(t + 1)
    end
    """
    assert "uncached_globals_summary" not in pattern_names(analyze(src))
    assert "uncached_globals_summary" in pattern_names(analyze(src, cache_threshold=2))


# ---------------------------------------------------------------------------
# branch-aware counting
# ---------------------------------------------------------------------------

# 2 + 2 + 1 across mutually exclusive branches: at most 2 can ever run, so this
# must NOT be summed to 5 and trigger caching.
BRANCHES_NEVER_COEXIST = """
function f(a, t)
    if a == 1 then
        return math.floor(t) + math.floor(t + 1)
    elseif a == 2 then
        return math.floor(t + 2) + math.floor(t + 3)
    else
        return math.floor(t + 4)
    end
end
"""

# 4 calls all inside one branch: that branch really does run them all.
FOUR_CALLS_IN_ONE_BRANCH = """
function f(a, t)
    if a == 1 then
        return math.floor(t) + math.floor(t + 1) + math.floor(t + 2) + math.floor(t + 3)
    else
        return math.floor(t + 4)
    end
end
"""

# 2 calls outside the chain + 2 inside one branch coexist on that path.
CALLS_SPLIT_ACROSS_CHAIN_AND_BODY = """
function f(a, t)
    local n = math.floor(t) + math.floor(t + 1)
    if a == 1 then
        n = n + math.floor(t + 2) + math.floor(t + 3)
    end
    return n
end
"""


def test_mutually_exclusive_branches_are_not_summed(analyze):
    assert "uncached_globals_summary" not in pattern_names(
        analyze(BRANCHES_NEVER_COEXIST)
    )


def test_four_calls_inside_one_branch_do_trigger(analyze):
    assert "uncached_globals_summary" in pattern_names(analyze(FOUR_CALLS_IN_ONE_BRANCH))


def test_calls_before_the_chain_add_to_the_branch_count(analyze):
    assert "uncached_globals_summary" in pattern_names(
        analyze(CALLS_SPLIT_ACROSS_CHAIN_AND_BODY)
    )


# ---------------------------------------------------------------------------
# the rewrite itself
# ---------------------------------------------------------------------------

def test_cache_declaration_is_inserted_and_call_sites_rewritten(transform, compiles):
    out = transform(FOUR_CALLS)
    assert "local mfloor = math.floor" in out
    assert "math.floor(" not in out
    assert out.count("mfloor(") == 4
    compiles(out)


def test_caching_preserves_behaviour(transform, run_both):
    # build the table on the Lua side - a Python list would not index like one
    wrapper = "\nfunction call_it() return f({1.7, 2.2, 3.9, 4.4}) end\n"
    run_both(FOUR_CALLS + wrapper, transform(FOUR_CALLS) + wrapper, "call_it")


# `local mfloor` already exists in the function, so the cache must pick another
# name rather than shadowing the user's variable.
SHADOWING = """
function f(t)
    local mfloor = 7
    return math.floor(t) + math.floor(t + 1) + math.floor(t + 2) + math.floor(t + 3) + mfloor
end
"""


def test_cache_name_collision_gets_an_alao_suffix(transform, compiles):
    out = transform(SHADOWING)
    assert "local mfloor_alao = math.floor" in out
    # the user's own local survives untouched
    assert "local mfloor = 7" in out
    compiles(out)


def test_cache_name_collision_preserves_behaviour(transform, run_both):
    run_both(SHADOWING, transform(SHADOWING), "f", 1.5)


CLOSURE = """
function outer()
    local cb = function(t)
        return math.floor(t) + math.floor(t + 1) + math.floor(t + 2) + math.floor(t + 3)
    end
    return cb
end
"""


def test_closure_gets_its_own_cache_inside_the_inner_function(transform, compiles):
    out = transform(CLOSURE)
    lines = out.splitlines()
    decl = [i for i, l in enumerate(lines) if "local mfloor = math.floor" in l]
    assert decl, f"no cache declaration in:\n{out}"
    # the declaration lands inside the anonymous function, not in `outer`
    assert "function(t)" in lines[decl[0] - 1]
    compiles(out)


def test_closure_caching_preserves_behaviour(transform, run_both):
    original = CLOSURE + "\nfunction call_it(t) return outer()(t) end\n"
    transformed = transform(CLOSURE) + "\nfunction call_it(t) return outer()(t) end\n"
    run_both(original, transformed, "call_it", 2.75)


# four `pairs` call sites, each in its own loop scope - the cache declaration has
# to land in the enclosing function and still be visible from every loop header
PAIRS_ACROSS_NESTED_SCOPES = """
function f(t)
    local acc = 0
    for _, v in pairs(t) do
        acc = acc + v
    end
    for _, v in pairs(t) do
        acc = acc + v
    end
    for _, v in pairs(t) do
        acc = acc + v
    end
    for _, v in pairs(t) do
        acc = acc + v
    end
    return acc
end
"""


def test_pairs_caching_preserves_behaviour(transform, run_both):
    out = transform(PAIRS_ACROSS_NESTED_SCOPES)
    assert "= pairs" in out
    wrapper = "\nfunction call_it() return f({1, 2, 3}) end\n"
    run_both(PAIRS_ACROSS_NESTED_SCOPES + wrapper, out + wrapper, "call_it")
