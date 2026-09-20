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


# every read is inside a branch, so the body does not always read the clock.
# Hoisting would add a call on the path where neither branch is taken.
TIME_GLOBAL_ALL_CONDITIONAL = """
function f(x)
    local r
    if x == 1 then
        r = time_global()
    elseif x == 2 then
        r = time_global() + 1
    end
    if x == 3 then
        r = time_global() + 2
    end
    return r
end
"""


def test_time_global_with_no_unconditional_read_is_skipped(analyze, transform):
    assert "repeated_time_global" not in pattern_names(analyze(TIME_GLOBAL_ALL_CONDITIONAL))
    assert "local tg = time_global()" not in transform(TIME_GLOBAL_ALL_CONDITIONAL)


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


# the name collision that matters in the wild: the throttle state is a
# MODULE-level local called tg, which the body reads as an upvalue. Shadowing
# it turns the guard into `tg == tg` and the function returns on its first
# line for ever (real site: drx_da_main_artefacts_movement.script).
TIME_GLOBAL_MODULE_STATE = """
local tg = 0

function f()
    if time_global() == tg then return "skipped" end
    tg = time_global()
    return "ran"
end
"""


def test_time_global_cache_does_not_shadow_module_state(transform, run_both, compiles):
    out = transform(TIME_GLOBAL_MODULE_STATE)
    assert "local tg = 0" in out
    assert "local tg = time_global()" not in out
    assert "tg == tg" not in out
    compiles(out)
    run_both(TIME_GLOBAL_MODULE_STATE, out, "f")


# same hole, same fix, older pattern: an upvalue the cache name would capture
DB_ACTOR_MODULE_LOCAL = """
local actor = "a string, not the actor"

function f()
    local a = db.actor.health
    local b = db.actor.health
    local c = db.actor.health
    local d = db.actor.health
    return actor, a == b, c == d
end
"""


def test_db_actor_cache_does_not_shadow_a_module_local(transform, run_both):
    """Predates I-040 and applies to the whole repeated_* family.

    `_collect_function_locals` only walked the body and its descendants, so a
    module-level local of the same name was invisible and the cache captured
    it.
    """
    out = transform(DB_ACTOR_MODULE_LOCAL)
    assert 'local actor = "a string, not the actor"' in out
    assert "local actor = db.actor" not in out
    run_both(DB_ACTOR_MODULE_LOCAL, out, "f")


# third shape of the same hole, found by agent-I021 in
# 463- Tasks QoL Pack - Serious/tasks_fetch.script: the colliding binding is an
# enclosing function's PARAMETER, not a `local` anywhere, so no grep for
# `local actor` finds it. The cache lands in a nested closure and captures it.
DB_ACTOR_ENCLOSING_PARAM = """
function run(actor)
    local inner = function()
        local a = db.actor.health
        local b = db.actor.health
        local c = db.actor.health
        local d = db.actor.health
        return actor, a == b, c == d
    end
    return inner()
end

function call_it()
    return run("a string, not the actor")
end
"""


def test_cache_does_not_capture_an_enclosing_parameter(transform, run_both):
    out = transform(DB_ACTOR_ENCLOSING_PARAM)
    assert "local actor = db.actor" not in out
    assert "local actor_alao = db.actor" in out
    run_both(DB_ACTOR_ENCLOSING_PARAM, out, "call_it")


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


# --- I-021: db.actor as a method receiver ---------------------------------

DB_ACTOR_METHOD_CALLS = """
function f()
    local a = db.actor:position()
    local b = db.actor:health()
    local c = db.actor:id()
    local d = db.actor:name()
    return a, b, c, d
end
"""


def test_db_actor_as_method_receiver_should_be_cached(analyze):
    """Fixed by I-021: _visit_Invoke no longer suppresses an EXPENSIVE_INDEXES
    receiver. 3615 of the 6298 `db.actor` reads in the enabled GAMMA corpus are
    method receivers, so this was the dominant real-world shape going unseen."""
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


# --- I-021: widened EXPENSIVE_INDEXES and CACHEABLE_OBJECT_METHODS ---------

DB_ACTOR_MIXED_SHAPES = """
function f()
    local a = db.actor:health()
    local b = db.actor.some_field
    db.actor:give_info_portion("x")
    local c = db.actor:position()
    return a, b, c
end
"""


def test_db_actor_counts_receivers_and_plain_reads_together(analyze):
    """A body that mixes `db.actor.x` with `db.actor:m()` reaches the threshold."""
    f = find_one(analyze(DB_ACTOR_MIXED_SHAPES), "repeated_db_actor")
    assert f.details["count"] == 4


def test_db_actor_receiver_rewrite_is_syntactically_whole(transform, compiles):
    out = transform(DB_ACTOR_MIXED_SHAPES)
    assert "local actor = db.actor" in out
    assert "actor:health()" in out and "db.actor:health()" not in out
    assert "actor:give_info_portion" in out
    assert compiles(out)


DB_STORAGE = """
function f(obj)
    local a = db.storage[obj:id()]
    local b = db.storage[1]
    local c = db.storage
    local d = db.storage[2]
    return a, b, c, d
end
"""


def test_db_storage_is_cached(analyze):
    f = find_one(analyze(DB_STORAGE), "repeated_db_storage")
    assert f.severity == "GREEN"
    assert f.source_line == "local db_storage = db.storage"


def test_db_storage_rewrite_preserves_behaviour(transform, run_both):
    out = transform(DB_STORAGE)
    assert "local db_storage = db.storage" in out
    harness = """
function g()
    db.storage[7] = 'seven'
    db.storage[1] = 'one'
    db.storage[2] = 'two'
    return f(make_object_stub(7, 'x'))
end
"""
    run_both(DB_STORAGE + harness, out + harness, "g")


# db.* tables that are NOT in EXPENSIVE_INDEXES stay untracked - the table is
# the contract, not "any db field".
DB_UNLISTED = """
function f()
    local a = db.spawn_cop_heli
    local b = db.spawn_cop_heli
    local c = db.spawn_cop_heli
    local d = db.spawn_cop_heli
    return a, b, c, d
end
"""


def test_unlisted_db_field_is_not_cached(analyze):
    assert not [
        f for f in analyze(DB_UNLISTED) if f.pattern_name.startswith("repeated_db_")
    ]


OBJ_NAME = """
function f(obj)
    local a = obj:name()
    local b = obj:name()
    local c = obj:name()
    local d = obj:name()
    return a, b, c, d
end
"""


def test_obj_name_is_green(analyze):
    f = find_one(analyze(OBJ_NAME), "repeated_obj_name()")
    assert f.severity == "GREEN"


def test_obj_name_rewrite_preserves_behaviour(transform, run_both):
    out = transform(OBJ_NAME)
    assert "local obj_name = obj:name()" in out
    harness_orig = OBJ_NAME + "\nfunction g() return f(make_object_stub(7, 'wpn_ak74')) end\n"
    harness_new = out + "\nfunction g() return f(make_object_stub(7, 'wpn_ak74')) end\n"
    run_both(harness_orig, harness_new, "g")


# :character_community() is stable only because no engine tick happens inside a
# body, which is a weaker argument than :id()/:section() being stored members.
OBJ_COMMUNITY = """
function f(obj)
    local a = obj:character_community()
    local b = obj:character_community()
    local c = obj:character_community()
    local d = obj:character_community()
    return a, b, c, d
end
"""


def test_character_community_is_yellow(analyze):
    assert find_one(analyze(OBJ_COMMUNITY), "repeated_obj_character_community()").severity == "YELLOW"


def test_yellow_method_is_not_rewritten_without_fix_yellow(transform):
    assert "local obj_comm" not in transform(OBJ_COMMUNITY)
    assert "local obj_comm" in transform(OBJ_COMMUNITY, fix_yellow=True)


# The cache-name regex must not truncate the receiver: `section_name` has to win
# over `name`, exactly as `story_id` wins over `id`.
OBJ_SECTION_NAME = """
function f(obj)
    local a = obj:section_name()
    local b = obj:section_name()
    local c = obj:section_name()
    local d = obj:section_name()
    return a, b, c, d
end
"""


def test_section_name_does_not_collide_with_name(transform, compiles):
    out = transform(OBJ_SECTION_NAME)
    assert "local obj_secname = obj:section_name()" in out
    assert "obj:name()" not in out
    assert compiles(out)


# Mutable engine reads stay out of the table on purpose.
OBJ_POSITION = """
function f(obj)
    local a = obj:position()
    local b = obj:position()
    local c = obj:position()
    local d = obj:position()
    return a, b, c, d
end
"""


def test_position_is_never_cached(analyze):
    """:position() hands back a fresh vector whose methods mutate in place, so a
    cached copy aliases where the original did not."""
    assert not [
        f for f in analyze(OBJ_POSITION) if f.pattern_name.startswith("repeated_obj_position")
    ]


# --- I-021 / agent-I041: the receiver must not be rebound -------------------

RECEIVER_REASSIGNED = """
function f(a, b)
    local obj = a
    local w = obj:id()
    local x = obj:id()
    obj = b
    local y = obj:id()
    local z = obj:id()
    return w, x, y, z
end
"""


def test_reassigned_receiver_is_never_cached(analyze):
    """`obj:id()` either side of `obj = b` is two different objects.

    The bucket key is the receiver's text, so both halves landed in one bucket
    and the rewrite answered all four with the first object's id. Silently
    wrong, and it predates I-021: `:id()` has been GREEN since the pattern
    existed.
    """
    assert not [
        f for f in analyze(RECEIVER_REASSIGNED) if f.pattern_name.startswith("repeated_")
    ]


def test_reassigned_receiver_rewrite_is_a_no_op(transform):
    assert "obj_id" not in transform(RECEIVER_REASSIGNED)


# The declaration itself sits before the first call and must not disarm the
# pattern - otherwise the fix above would turn every local receiver off.
RECEIVER_DECLARED_THEN_READ = """
function f(a)
    local obj = a
    local w = obj:id()
    local x = obj:id()
    local y = obj:id()
    local z = obj:id()
    return w, x, y, z
end
"""


def test_declaring_the_receiver_before_the_calls_still_caches(analyze, transform):
    assert find_one(analyze(RECEIVER_DECLARED_THEN_READ), "repeated_obj_id()")
    assert "local obj_id = obj:id()" in transform(RECEIVER_DECLARED_THEN_READ)


# Same rule for the property family: `db.storage` rebound mid-body.
DB_STORAGE_REASSIGNED = """
function f(t)
    local a = db.storage[1]
    local b = db.storage[2]
    db.storage = t
    local c = db.storage[3]
    local d = db.storage[4]
    return a, b, c, d
end
"""


def test_reassigned_db_field_is_never_cached(analyze):
    assert not [
        f for f in analyze(DB_STORAGE_REASSIGNED) if f.pattern_name.startswith("repeated_db_")
    ]


# Writing a FIELD of the receiver is not a rebind: the cache still points at
# the same object. Getting this wrong cost the actor_binder:update rewrite in
# vanilla bind_stalker.script, the most valuable site in I-021.
DB_ACTOR_FIELD_WRITE = """
function f()
    local a = db.actor:health()
    db.actor.afterFirstUpdate = true
    local b = db.actor:active_item()
    local c = db.actor.some_field
    return a, b, c
end
"""


def test_writing_a_field_of_the_receiver_is_not_a_rebind(analyze, transform):
    assert find_one(analyze(DB_ACTOR_FIELD_WRITE), "repeated_db_actor")
    out = transform(DB_ACTOR_FIELD_WRITE)
    assert "local actor = db.actor" in out
    assert "actor.afterFirstUpdate = true" in out
    assert "db.actor" not in out.split("local actor = db.actor", 1)[1]


OBJ_FIELD_WRITE = """
function f(obj)
    local a = obj:id()
    obj.marked = true
    local b = obj:id()
    local c = obj:id()
    local d = obj:id()
    return a, b, c, d
end
"""


def test_method_cache_survives_a_field_write_on_the_receiver(analyze):
    assert find_one(analyze(OBJ_FIELD_WRITE), "repeated_obj_id()")


# ---------------------------------------------------------------------------
# I-055: the first use inside a multi-line argument list / table constructor
#
# _edit_repeated_calls used to bail whenever the line of the first call sat at
# bracket depth > 0, so every repeated_* finding in a function like
# demonized_ledge_grabbing.script's checkLedgeGrabbing (5x device(), 8x
# db.actor, second-hottest listener in the game) was reported and never fixed.
# The declaration now goes to the start of the statement that contains the use.
# ---------------------------------------------------------------------------

MULTILINE_ARGS = """
function f()
    local pos = vector():set(
        device().time_delta,
        device().precache_frame,
        device().time_delta
    )
    local d = device().precache_frame
    return pos.x, pos.y, pos.z, d
end
"""


def test_first_use_inside_a_multiline_call_still_caches(analyze, transform, compiles):
    assert find_one(analyze(MULTILINE_ARGS), "repeated_device")
    out = transform(MULTILINE_ARGS)
    assert "local dev = device()" in out
    # the declaration must sit before the statement, not inside its arguments
    decl_at = out.index("local dev = device()")
    stmt_at = out.index("local pos = vector():set(")
    assert decl_at < stmt_at
    assert "device()" not in out[stmt_at:]
    compiles(out)


def test_multiline_call_rewrite_behaves_the_same(transform, run_both):
    run_both(MULTILINE_ARGS, transform(MULTILINE_ARGS), "f")


def test_multiline_call_rewrite_is_idempotent(transform, write_script):
    once = transform(MULTILINE_ARGS)
    twice = transform(once, name="again")
    assert twice == once


MULTILINE_TABLE = """
function f()
    local t = {
        hp = db.actor.health,
        id = db.actor:id(),
        nm = db.actor:name(),
    }
    local s = db.actor:section()
    return t.hp, t.id, t.nm, s
end
"""


def test_first_use_inside_a_table_constructor_still_caches(analyze, transform, compiles):
    assert find_one(analyze(MULTILINE_TABLE), "repeated_db_actor")
    out = transform(MULTILINE_TABLE)
    assert "local actor = db.actor" in out
    assert out.index("local actor = db.actor") < out.index("local t = {")
    compiles(out)


def test_multiline_table_rewrite_behaves_the_same(transform, run_both):
    run_both(MULTILINE_TABLE, transform(MULTILINE_TABLE), "f")


# The statement itself may start on an earlier line than the one that opens the
# bracket ("local x =" then "combine(" on the next line). Inserting between the
# two would produce `local x =\nlocal dev = device()\ncombine(...`, so the walk
# steps back over continuation lines.
CONTINUED_STATEMENT = """
local function combine(a, b, c, d) return a + b + c + d end

function f()
    local x =
        combine(
            device().time_delta,
            device().precache_frame,
            device().time_delta,
            device().precache_frame)
    return x
end
"""


def test_hoist_steps_back_over_a_continuation_line(transform, compiles, run_both):
    out = transform(CONTINUED_STATEMENT)
    compiles(out)
    if "local dev = device()" in out:
        assert out.index("local dev = device()") < out.index("local x =")
    run_both(CONTINUED_STATEMENT, out, "f")


# A closure passed as an argument: the function scope STARTS mid-statement, so
# the statement start lies outside the closure. Hoisting there would move the
# call out of the closure body (evaluated once instead of per invocation), so
# the hoist must be refused.
CLOSURE_ARGUMENT = """
local kept
local function register(fn, n) kept = fn; return n end

function f()
    register(function()
        local a = device().time_delta
        local b = device().precache_frame
        local c = device().time_delta
        local d = device().precache_frame
        return a + b + c + d
    end, 1)
    return kept()
end
"""


def test_a_closure_argument_is_never_hoisted_out_of_its_body(transform, compiles, run_both):
    out = transform(CLOSURE_ARGUMENT)
    compiles(out)
    before_closure = out.split("register(function()", 1)[0]
    assert "local dev = device()" not in before_closure
    run_both(CLOSURE_ARGUMENT, out, "f")


# The short-circuit guard that keeps a method cache honest can sit on an
# earlier line of the same multi-line statement; the guard scan follows the
# statement, not the line.
GUARDED_METHOD_IN_MULTILINE_ARGS = """
local function pick(a, b, c, d) return a or b or c or d end

function f(o)
    local t = pick(
        o and o:id(),
        o:id(),
        o:id(),
        o:id())
    return t
end
"""


def test_a_guard_on_an_earlier_line_of_the_statement_still_blocks_the_cache(transform):
    out = transform(GUARDED_METHOD_IN_MULTILINE_ARGS)
    assert "local o_id = o:id()" not in out


# A multi-line `elseif` condition has no legal insert point in front of it, and
# the line before it belongs to the previous branch - a declaration dropped
# there would not dominate the use in the condition at all (the use would read
# a nil global). The statement walk must stop at the branch keyword.
MULTILINE_ELSEIF_CONDITION = """
local function check(a, b, c, d) return a + b + c + d > 0 end

function f(a)
    if a == 1 then
        a = 2
    elseif check(
        device().time_delta,
        device().precache_frame,
        device().time_delta,
        device().precache_frame) then
        a = 3
    end
    return a
end
"""


def test_a_multiline_elseif_condition_is_left_alone(transform, compiles, run_both):
    out = transform(MULTILINE_ELSEIF_CONDITION)
    assert "local dev = device()" not in out
    compiles(out)
    run_both(MULTILINE_ELSEIF_CONDITION, out, "f", 1)
    run_both(MULTILINE_ELSEIF_CONDITION, out, "f", 5)


# A `while` condition is re-evaluated every iteration, so lifting a call out of
# it changes how OFTEN it runs, not just when - `while t - time_global() < x do`
# would never terminate. The hoist declines instead of reasoning about which
# getters are time-varying. (The `while ` spelling would also reach the
# hoist-to-function-top path below, which is fine for device(); `while(` would
# not, and that is the hole this closes. Either way this is no worse than the
# old bail-out.)
MULTILINE_WHILE_CONDITION = """
function f()
    local n = 0
    while math.max(
        device().time_delta,
        device().precache_frame,
        device().time_delta,
        device().precache_frame) < 3 do
        n = n + 1
    end
    return n
end
"""


def test_a_multiline_while_condition_is_left_alone(analyze, transform, compiles):
    assert find_one(analyze(MULTILINE_WHILE_CONDITION), "repeated_device")
    out = transform(MULTILINE_WHILE_CONDITION)
    assert "local dev = device()" not in out
    compiles(out)
