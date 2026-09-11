"""Repeated singleton lookups and immutable property reads: db.actor, alife(),
device(), system_ini(), get_console(), get_hud(), :section(), :id()."""

import pytest

from conftest import find_one, pattern_names


DB_ACTOR_READS = """
function f()
    local a = db.actor.health
    local b = db.actor.health
    local c = db.actor.health
    local d = db.actor.health
    return a, b, c, d
end
"""

# near miss: only three reads, under the default threshold of four
DB_ACTOR_THREE_READS = """
function f()
    local a = db.actor.health
    local b = db.actor.health
    local c = db.actor.health
    return a, b, c
end
"""

ALIFE = """
function f(id)
    local a = alife():object(id)
    local b = alife():actor()
    local c = alife():story_object(id)
    local d = alife():object(id + 1)
    return a, b, c, d
end
"""

DEVICE = """
function f()
    local a = device().time_delta
    local b = device().precache_frame
    local c = device().time_delta
    local d = device().precache_frame
    return a, b, c, d
end
"""

SYSTEM_INI = """
function f()
    local a = system_ini():r_string("a", "b")
    local b = system_ini():r_string("c", "d")
    local c = system_ini():r_string("e", "f")
    local d = system_ini():r_string("g", "h")
    return a, b, c, d
end
"""

GET_CONSOLE = """
function f()
    get_console():execute("a")
    get_console():execute("b")
    get_console():execute("c")
    get_console():execute("d")
end
"""

GET_HUD = """
function f()
    local a = get_hud()
    local b = get_hud()
    local c = get_hud()
    local d = get_hud()
    return a, b, c, d
end
"""

SECTION = """
function f(o)
    local a = o:section()
    local b = o:section()
    local c = o:section()
    local d = o:section()
    return a, b, c, d
end
"""

OBJ_ID = """
function f(o)
    local a = o:id()
    local b = o:id()
    local c = o:id()
    local d = o:id()
    return a, b, c, d
end
"""


@pytest.mark.parametrize(
    "src,pattern,decl",
    [
        (DB_ACTOR_READS, "repeated_db_actor", "local actor = db.actor"),
        (ALIFE, "repeated_alife", "local sim = alife()"),
        (DEVICE, "repeated_device", "local dev = device()"),
        (SYSTEM_INI, "repeated_system_ini", "local ini = system_ini()"),
        (GET_CONSOLE, "repeated_get_console", "local console = get_console()"),
        (GET_HUD, "repeated_get_hud", "local hud = get_hud()"),
        (SECTION, "repeated_o_section()", "local o_sec = o:section()"),
        (OBJ_ID, "repeated_o_id()", "local o_id = o:id()"),
    ],
)
def test_repeated_lookup_is_flagged_and_cached(analyze, transform, compiles, src, pattern, decl):
    finding = find_one(analyze(src), pattern)
    assert finding.severity == "GREEN"
    assert finding.line_num == 2
    assert finding.details["count"] == 4

    out = transform(src)
    assert decl in out
    compiles(out)


def test_three_db_actor_reads_stay_below_threshold(analyze):
    assert "repeated_db_actor" not in pattern_names(analyze(DB_ACTOR_THREE_READS))


@pytest.mark.parametrize("src,fn,args", [
    (ALIFE, "f", (3,)),
    (DEVICE, "f", ()),
    (SYSTEM_INI, "f", ()),
    (GET_HUD, "f", ()),
])
def test_singleton_caching_preserves_behaviour(transform, run_both, src, fn, args):
    run_both(src, transform(src), fn, *args)


def test_db_actor_caching_preserves_behaviour(transform, run_both):
    run_both(DB_ACTOR_READS, transform(DB_ACTOR_READS), "f")


@pytest.mark.parametrize("src", [SECTION, OBJ_ID])
def test_immutable_property_caching_preserves_behaviour(transform, run_both, src):
    wrapper = "\nfunction call_it() return f(make_object_stub(42, 'wpn_ak74')) end\n"
    run_both(src + wrapper, transform(src) + wrapper, "call_it")


# time_global() changes on every call, so caching it would break elapsed-time
# math. It must never be suggested.
TIME_GLOBAL = """
function f()
    local a = time_global()
    local b = time_global()
    local c = time_global()
    local d = time_global()
    return a, b, c, d
end
"""


def test_time_global_is_never_cached(analyze):
    assert not [f for f in analyze(TIME_GLOBAL) if f.pattern_name.startswith("repeated_")]


# level.object_by_id() takes an argument and may return a different object each
# call, so it is deliberately excluded from repeated-call caching.
OBJECT_BY_ID = """
function f(a, b, c, d)
    local w = level.object_by_id(a)
    local x = level.object_by_id(b)
    local y = level.object_by_id(c)
    local z = level.object_by_id(d)
    return w, x, y, z
end
"""


def test_object_by_id_is_never_cached(analyze):
    assert not [
        f for f in analyze(OBJECT_BY_ID) if f.pattern_name.startswith("repeated_")
    ]


# --- known ALAO gap -------------------------------------------------------

DB_ACTOR_METHOD_CALLS = """
function f()
    local a = db.actor:position()
    local b = db.actor:health()
    local c = db.actor:id()
    local d = db.actor:name()
    return a, b, c, d
end
"""


@pytest.mark.xfail(
    strict=True,
    reason="ast_analyzer.py:1612 suppresses the `db.actor` Index when it is the "
           "receiver of an Invoke, so `db.actor:method()` never counts toward "
           "repeated_db_actor - the dominant real-world shape of the pattern.",
)
def test_db_actor_as_method_receiver_should_be_cached(analyze):
    assert "repeated_db_actor" in pattern_names(analyze(DB_ACTOR_METHOD_CALLS))
