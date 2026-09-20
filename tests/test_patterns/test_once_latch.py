"""I-059: a "do once" latch declared `local` inside a function body.

The flag is fresh on every call, so the guard is always taken and the work the
author meant to run once runs on every call. RED, never auto-fixed.

Most of this module is near misses: the whole point of the pattern is that a
wrong RED wastes a modder's time, so everything that looks like a latch but is
a perfectly good per-call flag has to stay silent.
"""

import pytest

from conftest import find_one, findings_named, pattern_names


PATTERN = "once_latch_local_to_function"


# the real site: zzz_player_injuries.script, on the actor_on_update path
PER_FRAME_LATCH = """
function actor_on_update()
    local hidehudonce = false
    if hide_default_hud and not hidehudonce then
        local ui = ActorMenu.get_maingame()
        ui.m_ui_hud_states.m_ui_health_bar_show = false
        hidehudonce = true
    end
end
"""

COLD_LATCH = """
function on_game_start()
    local done = false
    if not done then
        register_everything()
        build_tables()
        done = true
    end
end
"""

# the mirrored form: start true, flip to false
MIRRORED_LATCH = """
function actor_on_update()
    local first = true
    if first then
        setup_hud()
        warm_caches()
        first = false
    end
end
"""

NIL_LATCH = """
function actor_on_update()
    local shown = nil
    if not shown then
        show_message()
        play_sound()
        shown = true
    end
end
"""

LOCAL_FUNCTION_LATCH = """
local function actor_on_update()
    local once = false
    if not once then
        do_work()
        do_more()
        once = true
    end
end
"""

METHOD_UPDATE_LATCH = """
function binder:update(delta)
    local once = false
    if not once then
        self:expensive()
        self:also_expensive()
        once = true
    end
end
"""

# --- near misses -----------------------------------------------------------

# module-level flag: this is the shape the latch is supposed to have
MODULE_LEVEL_LATCH = """
local done = false

function actor_on_update()
    if not done then
        do_work()
        do_more()
        done = true
    end
end
"""

# declared outside the loop, flipped inside it - a real do-once-per-call flag
LATCH_OUTSIDE_LOOP = """
function actor_on_update()
    local warned = false
    for i = 1, 10 do
        if not warned then
            complain(i)
            log(i)
            warned = true
        end
    end
end
"""

# break-out flag: read by the loop condition
BREAK_OUT_FLAG = """
function f(t)
    local stop = false
    while not stop do
        step()
        if done() then
            cleanup()
            stop = true
        end
    end
end
"""

# the flag is read again after the guard, so the assignment is not dead
READ_AFTER_THE_GUARD = """
function actor_on_update()
    local shown = false
    if not shown then
        show()
        again()
        shown = true
    end
    if shown then
        remember()
    end
    return shown
end
"""

# a closure can outlive the call
CAPTURED_BY_CLOSURE = """
function actor_on_update()
    local done = false
    if not done then
        register(function() done = true end)
        arm()
        done = true
    end
end
"""

# compute a flag, test it somewhere else - the working idiom, and the single
# most common near miss in the corpus (6 sites)
COMPUTE_THEN_TEST = """
function f(distance)
    local go = true
    if has_helmet() then
        go = false
    end
    if distance < 5 and go then
        hit_actor()
        shake()
    end
end
"""

# two guards read it, so the second one does see the flip
TWO_GUARDS = """
function actor_on_update()
    local done = false
    if not done then
        work()
        done = true
    end
    if not done then
        other()
    end
end
"""

# the guard has an else branch, which may depend on the flip
GUARD_WITH_ELSE = """
function actor_on_update()
    local done = false
    if not done then
        work()
        more()
        done = true
    else
        fallback()
    end
end
"""

# nothing guarded but the flip itself
EMPTY_GUARD = """
function actor_on_update()
    local done = false
    if not done then
        done = true
    end
end
"""

# not a constant flip: this is memoisation into a local, not a latch
NOT_A_FLIP = """
function actor_on_update()
    local cached = false
    if not cached then
        prepare()
        cached = compute()
    end
end
"""

# the flip is not the tail: the work happens after the flag is set, which is
# still a working sequence to read
FLIP_NOT_AT_TAIL = """
function actor_on_update()
    local done = false
    if not done then
        done = true
        work()
        more()
    end
end
"""

# a latch at chunk level is exactly right
CHUNK_LEVEL_LATCH = """
local done = false
if not done then
    setup()
    more()
    done = true
end
"""

# multi-assign: `a, b = false` is not the latch shape
MULTI_ASSIGN = """
function actor_on_update()
    local a, b = false, false
    if not a then
        work()
        a, b = true, true
    end
end
"""


# ---------------------------------------------------------------------------
# positives
# ---------------------------------------------------------------------------

def test_per_frame_latch_is_red(analyze):
    f = find_one(analyze(PER_FRAME_LATCH), PATTERN)
    assert f.severity == "RED"
    assert f.line_num == 2
    assert f.details["flag_name"] == "hidehudonce"
    assert f.details["guard_line"] == 3
    assert f.details["assign_line"] == 6
    assert f.details["is_per_frame"] is True
    assert f.details["guarded_statements"] == 2
    assert "every frame" in f.message


def test_cold_function_latch_is_reported_with_a_per_call_message(analyze):
    f = find_one(analyze(COLD_LATCH), PATTERN)
    assert f.severity == "RED"
    assert f.details["is_per_frame"] is False
    assert "every call" in f.message
    assert "every frame" not in f.message


def test_mirrored_true_to_false_latch_is_reported(analyze):
    f = find_one(analyze(MIRRORED_LATCH), PATTERN)
    assert f.details["flag_name"] == "first"


def test_nil_declared_latch_is_reported(analyze):
    f = find_one(analyze(NIL_LATCH), PATTERN)
    assert f.details["flag_name"] == "shown"


def test_local_function_callback_is_reported(analyze):
    f = find_one(analyze(LOCAL_FUNCTION_LATCH), PATTERN)
    assert f.details["is_per_frame"] is True
    assert f.details["function_name"] == "actor_on_update"


def test_binder_update_method_is_reported(analyze):
    f = find_one(analyze(METHOD_UPDATE_LATCH), PATTERN)
    assert f.details["is_per_frame"] is True


# ---------------------------------------------------------------------------
# near misses
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("src", [
    MODULE_LEVEL_LATCH,
    LATCH_OUTSIDE_LOOP,
    BREAK_OUT_FLAG,
    READ_AFTER_THE_GUARD,
    CAPTURED_BY_CLOSURE,
    COMPUTE_THEN_TEST,
    TWO_GUARDS,
    GUARD_WITH_ELSE,
    EMPTY_GUARD,
    NOT_A_FLIP,
    FLIP_NOT_AT_TAIL,
    CHUNK_LEVEL_LATCH,
    MULTI_ASSIGN,
])
def test_near_misses_are_not_flagged(analyze, src):
    assert PATTERN not in pattern_names(analyze(src))


def test_one_finding_per_latch(analyze):
    assert len(findings_named(analyze(PER_FRAME_LATCH), PATTERN)) == 1


# ---------------------------------------------------------------------------
# RED means report-only
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("src,flag_line", [
    (PER_FRAME_LATCH, "local hidehudonce = false"),
    (COLD_LATCH, "local done = false"),
    (MIRRORED_LATCH, "local first = true"),
])
@pytest.mark.parametrize("flags", [
    {},
    {"fix_yellow": True},
    {"fix_nil": True, "fix_debug": True, "remove_dead_code": True,
     "experimental": True},
])
def test_no_fix_flag_touches_the_latch(transform, src, flag_line, flags):
    out = transform(src, **flags)
    assert flag_line in out
    # and the guard is still a guard
    assert out.count("if ") == src.count("if ")
