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


# --- I-040: time_global() ------------------------------------------------
#
# It used to be excluded from repeated-call caching on the grounds that it
# "returns a different value each call". Within one body it does not: it is
# `Device.dwTimeGlobal`, the render device's time stamp, written once per frame
# (which is also why the engine ships a separate `time_global_async()`).
# Threshold is 2, not 4, because every read is an engine C call.

TIME_GLOBAL = """
function f()
    local a = time_global()
    local b = time_global()
    local c = time_global()
    local d = time_global()
    return a, b, c, d
end
"""

TIME_GLOBAL_TWO = """
function f()
    local a = time_global()
    local b = time_global()
    return a, b
end
"""

TIME_GLOBAL_ONCE = """
function f()
    local a = time_global()
    return a
end
"""

# the shape the census found everywhere: throttle guard at the top of a
# per-frame body, then the timestamp written back
TIME_GLOBAL_THROTTLE = """
local last_upd = 0

function actor_on_update()
    if time_global() - last_upd < 250 then return end
    last_upd = time_global()
    return last_upd
end
"""


def test_time_global_two_reads_are_cached(analyze, transform, compiles):
    finding = find_one(analyze(TIME_GLOBAL_TWO), "repeated_time_global")
    assert finding.severity == "GREEN"
    assert finding.details["count"] == 2

    out = transform(TIME_GLOBAL_TWO)
    assert "local tg = time_global()" in out
    assert out.count("time_global()") == 1
    compiles(out)


def test_time_global_single_read_is_left_alone(analyze):
    assert "repeated_time_global" not in pattern_names(analyze(TIME_GLOBAL_ONCE))


def test_time_global_caching_preserves_behaviour(transform, run_both):
    run_both(TIME_GLOBAL, transform(TIME_GLOBAL), "f")


def test_time_global_throttle_guard_is_cached(analyze, transform, run_both):
    find_one(analyze(TIME_GLOBAL_THROTTLE), "repeated_time_global")
    out = transform(TIME_GLOBAL_THROTTLE)
    assert out.count("time_global()") == 1
    run_both(TIME_GLOBAL_THROTTLE, out, "actor_on_update")


# time_global_async() is the asynchronous clock: it really does move between
# two reads, and it is the documented way to time sub-frame work.
TIME_GLOBAL_ASYNC = """
function f()
    local a = time_global_async()
    local b = time_global_async()
    local c = time_global_async()
    local d = time_global_async()
    return a, b, c, d
end
"""


def test_time_global_async_is_never_cached(analyze):
    assert not [
        f for f in analyze(TIME_GLOBAL_ASYNC) if f.pattern_name.startswith("repeated_")
    ]


# --- I-040 safety guards --------------------------------------------------

# a loop that waits on the clock would never terminate with a hoisted read
TIME_GLOBAL_WHILE = """
function f()
    local t0 = 0
    while time_global() - t0 < 100 do
        t0 = t0 + 1
    end
    return time_global()
end
"""

# "how long did this take": folding the two reads makes the answer a constant 0
TIME_GLOBAL_SELF_TIMING = """
function f()
    local t0 = time_global()
    do_work()
    local dt = time_global() - t0
    return dt
end
"""

# a hoisted declaration would call the clock on the bail-out path, where the
# original called nothing. The reads sit in different branches, which is what
# makes the transformer hoist to the top of the body.
TIME_GLOBAL_EARLY_RETURN = """
function f(obj)
    if not obj then return end
    if obj.a then
        obj.x = time_global()
    end
    for i = 1, 3 do
        obj.y = time_global()
    end
    return obj.x
end
"""


# a yield is the one thing a Lua body can do that reaches the next frame, so
# the cached stamp could be stale after it
TIME_GLOBAL_YIELD = """
function f()
    local a = time_global()
    coroutine.yield()
    local b = time_global()
    return a, b
end
"""


@pytest.mark.parametrize("src", [
    TIME_GLOBAL_WHILE,
    TIME_GLOBAL_SELF_TIMING,
    TIME_GLOBAL_EARLY_RETURN,
    TIME_GLOBAL_YIELD,
])
def test_time_global_unsafe_shapes_are_skipped(analyze, transform, src):
    assert "repeated_time_global" not in pattern_names(analyze(src))
    assert "local tg = time_global()" not in transform(src)


# reads inside a `for` are the best case: the loop terminates whatever the
# clock says, and the hoist saves one call per iteration
TIME_GLOBAL_IN_FOR = """
function f()
    local acc = 0
    for i = 1, 10 do
        acc = acc + time_global()
    end
    return acc + time_global()
end
"""


def test_time_global_in_numeric_for_is_cached(analyze, transform, run_both):
    find_one(analyze(TIME_GLOBAL_IN_FOR), "repeated_time_global")
    out = transform(TIME_GLOBAL_IN_FOR)
    assert out.count("time_global()") == 1
    run_both(TIME_GLOBAL_IN_FOR, out, "f")


# a body that already has its own `local tg` must not be shadowed
TIME_GLOBAL_NAME_TAKEN = """
function f()
    local tg = "not a time at all"
    local a = time_global()
    local b = time_global()
    return tg, a, b
end
"""


def test_time_global_cache_name_does_not_shadow(transform, run_both, compiles):
    out = transform(TIME_GLOBAL_NAME_TAKEN)
    assert 'local tg = "not a time at all"' in out
    assert "local tg_alao = time_global()" in out
    compiles(out)
    run_both(TIME_GLOBAL_NAME_TAKEN, out, "f")


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


# --- I-038: --fix must not create new findings ----------------------------

ALIFE_WITH_ARGS = """
function f()
    alife(l08_yantar):create("snork", 1, 2, 3)
    alife(l10_red_forest):create("burer", 4, 5, 6)
    alife(k00_marsh):create("informer", 7, 8, 9)
    alife(l08_yantar):create("controller", 10, 11, 12)
end
"""

ALIFE_CHAIN_WE_DO_NOT_WARN_ABOUT = """
function f()
    alife():create("snork", 1, 2, 3)
    alife():create("burer", 4, 5, 6)
    alife():create("informer", 7, 8, 9)
    alife():create("controller", 10, 11, 12)
end
"""

ALIFE_CHAIN_WE_DO_WARN_ABOUT = """
function f(id)
    local a = alife():object(id)
    local b = alife():actor()
    local c = alife():story_object(id)
    local d = alife():object(id + 1)
    return a, b, c, d
end
"""


def test_calls_with_arguments_are_not_coalesced_into_one_cache(analyze):
    """`alife(a)` and `alife(b)` are different calls; caching one drops the arg.

    Real site: operacia_monolith.script, where --fix turned
    `alife(l08_yantar):create(...)` into `sim:create(...)` off a single
    `local sim = alife()`.
    """
    assert "repeated_alife" not in pattern_names(analyze(ALIFE_WITH_ARGS))


def test_nil_returning_call_is_not_cached_when_the_chain_is_unwarned(analyze):
    """Caching `alife()` here would invent potential_nil_access findings.

    `alife():object(id)` is in NIL_RETURNING_FUNCTIONS and flagged either way,
    but `alife():create(...)` is not - so only the rewritten
    `local sim = alife(); sim:create(...)` gets flagged and --fix ends up
    creating work for itself.
    """
    assert "repeated_alife" not in pattern_names(analyze(ALIFE_CHAIN_WE_DO_NOT_WARN_ABOUT))


def test_a_nil_returning_call_we_already_warn_about_is_still_cached(analyze):
    """The guard above must not kill the ordinary alife() caching case."""
    assert "repeated_alife" in pattern_names(analyze(ALIFE_CHAIN_WE_DO_WARN_ABOUT))
