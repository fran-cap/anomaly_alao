"""potential_nil_access: detection, the is_safe_to_fix gate, and the --fix-nil
if-then guard rewrite."""

import pytest

from conftest import find_one, findings_named, pattern_names


LEVEL_OBJECT = """
function f(id)
    local o = level.object_by_id(id)
    return o:name()
end
"""

DB_ACTOR = """
function f()
    local a = db.actor
    return a:health()
end
"""

STORY_OBJECT = """
function f(sid)
    local o = get_story_object(sid)
    return o:id()
end
"""

METHOD_RETURNING_NIL = """
function f(o)
    local p = o:parent()
    return p:id()
end
"""

# near miss: already guarded with a multi-line if, nothing to report
GUARDED = """
function f(id)
    local o = level.object_by_id(id)
    if o then
        return o:name()
    end
    return nil
end
"""

# near miss: the value never comes from a nil-returning source
PLAIN_LOCAL = """
function f(id)
    local o = {id = id}
    return o.id
end
"""


@pytest.mark.parametrize(
    "src,var", [
        (LEVEL_OBJECT, "o"),
        (DB_ACTOR, "a"),
        (STORY_OBJECT, "o"),
        (METHOD_RETURNING_NIL, "p"),
    ],
)
def test_unguarded_use_is_flagged_yellow_and_marked_fixable(analyze, src, var):
    finding = find_one(analyze(src), "potential_nil_access")
    assert finding.severity == "YELLOW"
    assert finding.line_num == 3
    assert finding.details["is_safe_to_fix"] is True
    assert var in finding.message


def test_guarded_use_is_not_flagged(analyze):
    assert "potential_nil_access" not in pattern_names(analyze(GUARDED))


def test_plain_local_is_not_flagged(analyze):
    assert "potential_nil_access" not in pattern_names(analyze(PLAIN_LOCAL))


def test_nothing_happens_without_the_fix_nil_flag(transform_full):
    modified, content, count = transform_full(LEVEL_OBJECT)
    assert modified is False


@pytest.mark.parametrize("src", [LEVEL_OBJECT, DB_ACTOR, STORY_OBJECT, METHOD_RETURNING_NIL])
def test_fix_nil_wraps_the_use_in_a_guard(transform, compiles, src):
    out = transform(src, fix_nil=True)
    assert "if " in out and "then" in out
    compiles(out)


def test_guard_keeps_the_happy_path_result(transform, run_both):
    # the stub level.object_by_id() always returns an object, so the guarded and
    # unguarded versions must produce the same value
    run_both(LEVEL_OBJECT, transform(LEVEL_OBJECT, fix_nil=True), "f", 5)


def test_guard_turns_a_crash_into_nil(transform, lua_call):
    """The whole point of --fix-nil: a nil source must stop crashing."""
    from conftest import LUA_STUB_PRELUDE

    prelude = LUA_STUB_PRELUDE + "\nfunction level.object_by_id(id) return nil end\n"

    ok, err = lua_call(LEVEL_OBJECT, "f", 1, prelude=prelude)
    assert ok is False
    assert "nil" in err

    ok, res = lua_call(transform(LEVEL_OBJECT, fix_nil=True), "f", 1, prelude=prelude)
    assert ok is True
    assert res is None


# Two unguarded uses of the same variable: a single if-then wrapper cannot
# cover two separate statements, so NEITHER is auto-fixable. Before I-052 the
# analyzer called the first one safe and the transformer caught it late with a
# "does the next line start with o:" check - which only worked when the second
# use happened to lead its line. Both findings stand; only the fix is off.
TWO_USES = """
function f(id)
    local o = level.object_by_id(id)
    o:name()
    o:section()
    return 1
end
"""


def test_second_use_is_marked_unsafe_and_nothing_is_rewritten(analyze, transform_full):
    hits = findings_named(analyze(TWO_USES), "potential_nil_access")
    assert [h.details["is_safe_to_fix"] for h in hits] == [False, False]
    # each one names the other use, which is why neither is auto-fixable
    for h in hits:
        others = h.details["other_access_lines"]
        assert others and h.line_num not in others
    modified, content, count = transform_full(TWO_USES, fix_nil=True)
    assert modified is False, f"unsafe multi-use nil access was rewritten:\n{content}"


# I-052, the other two shapes the GAMMA gate caught under --fix --fix-nil.

# nta_utils.script: `item and <expr using item>` short-circuits, so the
# expression never runs on a nil item. ALAO used to only know the
# `item and item:` shape and wrapped this in a pointless `if item then`.
SHORT_CIRCUIT_GUARD = """
function is_axe()
    local item = db.actor:active_item()
    return item and string.find(item:section(), "axe", 1, true)
end
"""

# ...but `not item and item:section()` guards the wrong way round and is a
# genuine crash, so the finding must survive.
INVERTED_SHORT_CIRCUIT = """
function is_axe()
    local item = db.actor:active_item()
    return not item and item:section()
end
"""

# soulslike_scenarios.script: the later uses are plain field writes, which never
# become nil accesses of their own, so the one-line guard would protect line 1
# of 5. Nothing is auto-fixable here.
FIELD_USES_AFTER = """
function heal()
    local actor = db.actor
    actor:set_health_ex(1)
    actor.power = 1
    actor.radiation = 0
end
"""


def test_short_circuit_and_counts_as_a_guard(analyze, transform_full):
    assert "potential_nil_access" not in pattern_names(analyze(SHORT_CIRCUIT_GUARD))
    modified, content, _ = transform_full(SHORT_CIRCUIT_GUARD, fix_nil=True)
    assert "if item then" not in content, content


def test_not_var_and_is_not_a_guard(analyze):
    assert "potential_nil_access" in pattern_names(analyze(INVERTED_SHORT_CIRCUIT))


def test_later_field_uses_block_the_one_line_guard(analyze, transform_full):
    hits = findings_named(analyze(FIELD_USES_AFTER), "potential_nil_access")
    assert hits, "the nil hazard must still be reported"
    assert all(h.details["is_safe_to_fix"] is False for h in hits)
    modified, content, _ = transform_full(FIELD_USES_AFTER, fix_nil=True)
    assert "if actor then" not in content, content


# --- known ALAO gap -------------------------------------------------------

SINGLE_LINE_GUARD = """
function f(id)
    local o = level.object_by_id(id)
    if o then return o:name() end
    return nil
end
"""


@pytest.mark.xfail(
    strict=True,
    reason="guard detection is line-based: a one-line `if o then ... end` is not "
           "recognised as a nil guard, so ALAO reports a false positive that the "
           "identical multi-line form does not produce.",
)
def test_single_line_guard_should_suppress_the_finding(analyze):
    assert "potential_nil_access" not in pattern_names(analyze(SINGLE_LINE_GUARD))
