"""DEBUG findings and --fix-debug comment-out, including the rule that a
commented-out call no longer counts toward global caching."""

import pytest

from conftest import find_one, findings_named, pattern_names


DEBUG_CALLS = """
function f(x)
    log("hi")
    printf("v %s", x)
    return x
end
"""

ALL_DEBUG_FUNCTIONS = """
function f(x)
    print(x)
    printf(x)
    printe(x)
    printd(x)
    log(x)
    log1(x)
    log2(x)
    log3(x)
    DebugLog(x)
    debug_log(x)
    trace(x)
    dump(x)
    return x
end
"""

# near miss: a user function whose name merely contains "log"
NOT_A_DEBUG_CALL = """
function f(x)
    my_log("hi")
    logger.write("hi")
    return x
end
"""


def test_debug_calls_are_flagged_with_line_numbers(analyze):
    hits = findings_named(analyze(DEBUG_CALLS), "debug_statement")
    assert [(h.severity, h.line_num) for h in hits] == [("DEBUG", 2), ("DEBUG", 3)]


def test_every_documented_debug_function_is_recognised(analyze):
    hits = findings_named(analyze(ALL_DEBUG_FUNCTIONS), "debug_statement")
    assert len(hits) == 12


def test_similar_user_functions_are_not_flagged(analyze):
    assert "debug_statement" not in pattern_names(analyze(NOT_A_DEBUG_CALL))


def test_debug_calls_are_left_alone_without_the_flag(transform_full):
    modified, content, count = transform_full(DEBUG_CALLS)
    assert modified is False


def test_fix_debug_comments_the_calls_out(transform, compiles):
    out = transform(DEBUG_CALLS, fix_debug=True)
    assert '-- log("hi")' in out
    assert '-- printf("v %s", x)' in out
    compiles(out)


def test_commented_out_calls_still_return_the_value(transform, run_both):
    run_both(DEBUG_CALLS, transform(DEBUG_CALLS, fix_debug=True), "f", 42)


# tostring() is called four times, but only from inside debug calls. Once those
# are commented out the cache declaration would have no callers left, so the
# enabler insertion must be dropped with them.
DEBUG_ONLY_CACHE = """
function f(x)
    log(tostring(x))
    log(tostring(x))
    log(tostring(x))
    log(tostring(x))
    return x
end
"""


def test_commented_out_code_does_not_leave_a_dead_cache_declaration(transform, compiles):
    out = transform(DEBUG_ONLY_CACHE, fix_debug=True)
    assert "= tostring" not in out, f"dead cache declaration left behind:\n{out}"
    assert out.count("-- log(tostring(x))") == 4
    compiles(out)


def test_debug_only_cache_preserves_behaviour(transform, run_both):
    run_both(DEBUG_ONLY_CACHE, transform(DEBUG_ONLY_CACHE, fix_debug=True), "f", 7)


# Here tostring() is used four times outside debug calls too, so the cache is
# still justified after the debug lines disappear.
MIXED_CACHE = """
function f(x)
    log(tostring(x))
    local a = tostring(x)
    local b = tostring(x)
    local c = tostring(x)
    local d = tostring(x)
    return a .. b .. c .. d
end
"""


def test_cache_survives_when_real_call_sites_remain(transform, compiles):
    out = transform(MIXED_CACHE, fix_debug=True)
    assert "= tostring" in out
    assert "-- log(" in out
    compiles(out)


def test_mixed_cache_preserves_behaviour(transform, run_both):
    run_both(MIXED_CACHE, transform(MIXED_CACHE, fix_debug=True), "f", 12)


def test_multiline_debug_call_is_commented_out_completely(transform, compiles):
    src = """
    function f(x)
        printf("a %s b %s",
               x,
               x)
        return x
    end
    """
    out = transform(src, fix_debug=True)
    compiles(out)
