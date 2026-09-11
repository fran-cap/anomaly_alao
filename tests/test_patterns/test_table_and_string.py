"""The cheap GREEN rewrites: table.insert, table.getn, string.len, string.find,
literal concat folding and `not (a == b)`."""

import pytest

from conftest import find_one, pattern_names


TABLE_INSERT = """
function f(t, v)
    table.insert(t, v)
    return t
end
"""

# near miss: the 3-arg form inserts at a position, #t+1 would be wrong
TABLE_INSERT_POSITIONAL = """
function f(t, v)
    table.insert(t, 1, v)
    return t
end
"""

TABLE_GETN = """
function f(t)
    return table.getn(t)
end
"""

STRING_LEN = """
function f(s)
    return string.len(s)
end
"""

STRING_FIND_PLAIN = """
function f(s)
    return string.find(s, "abc")
end
"""

# near miss: the needle has a magic character, so it is a real pattern
STRING_FIND_PATTERN = """
function f(s)
    return string.find(s, "a.c")
end
"""

LITERAL_CONCAT = """
function f()
    return "a" .. "b"
end
"""

# near miss: one side is a variable, nothing to fold at parse time
LITERAL_CONCAT_VARIABLE = """
function f(x)
    return "a" .. x
end
"""

REDUNDANT_NOT_EQ = """
function f(a, b)
    return not (a == b)
end
"""

# near miss: `not a == b` parses as `(not a) == b`, a different expression
REDUNDANT_NOT_EQ_UNPARENTHESIZED = """
function f(a, b)
    return not a == b
end
"""


def test_table_insert_append_triggers(analyze):
    finding = find_one(analyze(TABLE_INSERT), "table_insert_append")
    assert finding.severity == "GREEN"
    assert finding.line_num == 2


def test_table_insert_with_position_is_not_flagged(analyze):
    assert "table_insert_append" not in pattern_names(analyze(TABLE_INSERT_POSITIONAL))


def test_table_insert_becomes_index_assignment(transform, compiles):
    out = transform(TABLE_INSERT)
    assert "t[#t+1] = v" in out
    assert "table.insert" not in out
    compiles(out)


def test_table_insert_keeps_behaviour(transform, run_both):
    src = """
    function f(n)
        local t = {}
        for i = 1, n do
            table.insert(t, i * 2)
        end
        return t
    end
    """
    run_both(src, transform(src), "f", 5)


def test_table_getn_triggers_and_becomes_length_operator(analyze, transform, compiles):
    assert find_one(analyze(TABLE_GETN), "table_getn").severity == "GREEN"
    out = transform(TABLE_GETN)
    assert "#t" in out and "table.getn" not in out
    compiles(out)


def test_string_len_triggers_and_becomes_length_operator(analyze, transform, compiles):
    assert find_one(analyze(STRING_LEN), "string_len").severity == "GREEN"
    out = transform(STRING_LEN)
    assert "#s" in out and "string.len" not in out
    compiles(out)


def test_length_rewrites_keep_behaviour(transform, run_both):
    src = """
    function f(s)
        local t = {1, 2, 3, 4}
        return table.getn(t) + string.len(s)
    end
    """
    run_both(src, transform(src), "f", "hello")


def test_string_find_plain_literal_triggers(analyze):
    finding = find_one(analyze(STRING_FIND_PLAIN), "string_find_plain")
    assert finding.severity == "GREEN"
    assert finding.line_num == 2


def test_string_find_with_magic_chars_is_not_flagged(analyze):
    assert "string_find_plain" not in pattern_names(analyze(STRING_FIND_PATTERN))


def test_string_find_gains_plain_argument(transform, compiles, run_both):
    out = transform(STRING_FIND_PLAIN)
    assert "string.find(s, \"abc\", 1, true)" in out
    compiles(out)
    run_both(STRING_FIND_PLAIN, out, "f", "xxabcxx")


def test_literal_concat_folds(analyze, transform, compiles, run_both):
    assert find_one(analyze(LITERAL_CONCAT), "string_literal_concat").severity == "GREEN"
    out = transform(LITERAL_CONCAT)
    assert '"ab"' in out
    compiles(out)
    run_both(LITERAL_CONCAT, out, "f")


def test_literal_concat_with_variable_is_not_flagged(analyze):
    assert "string_literal_concat" not in pattern_names(analyze(LITERAL_CONCAT_VARIABLE))


def test_redundant_not_eq_becomes_not_equal_operator(analyze, transform, compiles, run_both):
    assert find_one(analyze(REDUNDANT_NOT_EQ), "redundant_not_eq").severity == "GREEN"
    out = transform(REDUNDANT_NOT_EQ)
    assert "a ~= b" in out
    compiles(out)
    run_both(REDUNDANT_NOT_EQ, out, "f", 1, 2)
    run_both(REDUNDANT_NOT_EQ, out, "f", 3, 3)


def test_unparenthesized_not_eq_is_not_flagged(analyze):
    # `not a == b` means `(not a) == b`; rewriting it to `a ~= b` would be wrong
    assert "redundant_not_eq" not in pattern_names(
        analyze(REDUNDANT_NOT_EQ_UNPARENTHESIZED)
    )


# --- I-038: the fix must not orphan the alias it rewrote away -------------

ALIAS_ONLY_USE = """
local tinsert = table.insert

function f(t, v)
    tinsert(t, v)
    return t
end
"""

ALIAS_WITH_ANOTHER_USE = """
local tinsert = table.insert

function f(t, v)
    tinsert(t, v)
    tinsert(t, 1, v)
    return t
end
"""


def test_rewriting_the_last_alias_use_is_declined(analyze):
    """`local tinsert = table.insert` + its only call site.

    Rewriting `tinsert(t, v)` to `t[#t+1] = v` leaves the alias dead, and the
    next analyze pass reports a brand new unused_local_variable that --fix
    invented. Six of those on GAMMA; decline the rewrite instead.
    """
    findings = analyze(ALIAS_ONLY_USE)
    assert "table_insert_append" not in pattern_names(findings)
    assert "unused_local_variable" not in pattern_names(findings)


def test_an_alias_that_survives_the_rewrite_is_still_fixed(analyze):
    """The 3-arg call keeps the alias alive, so the 2-arg one is fair game."""
    assert "table_insert_append" in pattern_names(analyze(ALIAS_WITH_ANOTHER_USE))


def test_a_plain_table_insert_is_unaffected_by_the_alias_guard(analyze):
    assert "table_insert_append" in pattern_names(analyze(TABLE_INSERT))


@pytest.mark.xfail(strict=True, reason=(
    "ALAO bug found on the vanilla scripts.db0 corpus (xr_logic.script:741, run "
    "20260911-141502-vanilla-db): a string_find_plain edit nested inside the value "
    "of a table.insert that table_insert_append also rewrites is dropped on pass 1 "
    "and only lands on pass 2, so --fix is not a fixpoint. _apply_edits containment "
    "folds contained edits, but the append rewrite rebuilds the value from text "
    "(_extract_table_insert_value, I-019) so the inner edit has nothing to fold into."))
def test_string_find_inside_an_appended_constructor_lands_in_one_pass(transform):
    src = """
function f(lst, infop)
    table.insert(lst, {infop, 1, (string.find(infop, "npcx_"))})
    return lst
end
"""
    once = transform(src)
    assert "lst[#lst+1]" in once
    assert 'string.find(infop, "npcx_", 1, true)' in once, "inner edit must land on the first pass"
