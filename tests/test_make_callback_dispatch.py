"""I-043: differential tests for the axr_main.make_callback dispatcher rewrite.

This is not an ALAO transform - it is a hand patch to the live
`Anomaly/gamedata/scripts/axr_main.script`. What has to be proven is that the
candidate dispatcher (a sorted array rebuilt on register/unregister, replaced
wholesale so an in-flight dispatch keeps its snapshot) calls exactly the same
listeners, in the same order, with the same arguments as the shipped
`spairs(intercepts[name], sort_func_values_ascend)` loop - including the weird
cases nobody thinks about until a mod hits them:

* a listener unregistering itself (or another) mid-dispatch: the victim is
  SKIPPED in this pass (hspairs re-reads `t[key]` per step and drops nils)
* a listener registering another one mid-dispatch: not called this pass, the
  key array was snapshotted before the first call
* registering the same function twice: it moves to the END of the order and is
  still called once
* unregistering something that was never registered, and a callback name that
  does not exist at all
* userdata/table listeners dispatched as `t[name](t, ...)`

WHICH `spairs`: not the `table.sort` one in `_g.script`. The loose
`_g_patches.script` that GAMMA drops into `Anomaly/gamedata/scripts` replaces
`_G.spairs` with a min-heap `hspairs` whenever an order function other than
`sort_func_keys_ascend` is passed, and `make_callback` passes
`sort_func_values_ascend`.  So the shipped arm below is `hspairs`, copied
verbatim together with `safe_order`, both `sift_down`s and the patched `pairs`.
The two implementations differ on the unregister-during-dispatch case, which is
exactly why this has to be tested against the live one.

Both arms run in ONE LuaJIT 2.0 runtime over the same intercepts table, so any
divergence is the dispatcher and nothing else.
"""

import pytest

from lupa import luajit20 as _luajit

from conftest import _quiet_faulthandler


# The real thing, copied out of the live winners:
#   pairs / spairs / hspairs / safe_order / sift_down_* / sort_func_*
#                             _g_patches.script  (loose, Anomaly/gamedata/scripts)
#   intercepts / callback_* / make_callback
#                             axr_main.script    (loose, Anomaly/gamedata/scripts)
# (`_g.script`'s own spairs is dead code in GAMMA - _g_patches overwrites it.)
LUA = r"""
-- ------------------------------------------------ _g_patches.script (loose, verbatim)
local _p = pairs
_G.pairs = function(t, ...)
    local m = getmetatable(t)
    if not (m and m.__pairs) then return _p(t, ...) end
    return m.__pairs(t, ...)
end

local math_floor = math.floor
_G.nil_func = function() return nil end

local sift_down_order = function(keys, idx, end_idx, t, order)
    local hole_idx = idx
    local sifting_key = keys[hole_idx]
    local half_idx = math_floor(end_idx / 2)
    while hole_idx <= half_idx do
        local child = hole_idx * 2
        local right = child + 1
        if child < end_idx and order(t, keys[right], keys[child]) then
            child = right
        end
        if order(t, keys[child], sifting_key) then
            keys[hole_idx] = keys[child]
            hole_idx = child
        else
            break
        end
    end
    keys[hole_idx] = sifting_key
end

local sift_down_default = function(keys, idx, end_idx)
    local hole_idx = idx
    local sifting_key = keys[hole_idx]
    local half_idx = math_floor(end_idx / 2)
    while hole_idx <= half_idx do
        local child = hole_idx * 2
        local right = child + 1
        if child < end_idx and keys[right] < keys[child] then
            child = right
        end
        if keys[child] < sifting_key then
            keys[hole_idx] = keys[child]
            hole_idx = child
        else
            break
        end
    end
    keys[hole_idx] = sifting_key
end

local function safe_order(order)
    return function(t, a, b)
        local va = t[a]
        local vb = t[b]
        if va == nil then return false end
        if vb == nil then return true end
        return order(t, a, b)
    end
end

_G.hspairs = function(t, order)
    local n = 0
    local keys = {}
    for k in pairs(t) do
        n = n + 1
        keys[n] = k
    end
    if n == 0 then
        return nil_func
    end
    if n == 1 then
        local done = false
        return function()
            if done then return nil end
            done = true
            local val = t[keys[1]]
            if val ~= nil then
                return keys[1], val
            end
        end
    end
    if order then
        order = safe_order(order)
    end
    local sift_down = order and sift_down_order or sift_down_default
    for i = math_floor(n / 2), 1, -1 do
        sift_down(keys, i, n, t, order)
    end
    return function()
        while n > 0 do
            local best_key = keys[1]
            local val = t[best_key]
            keys[1] = keys[n]
            keys[n] = nil
            n = n - 1
            if n > 0 then
                sift_down(keys, 1, n, t, order)
            end
            if val ~= nil then
                return best_key, val
            end
        end
        return nil
    end
end

sort_func_keys_ascend = function(t, a, b) return a < b end
sort_func_values_ascend = function(t, a, b) return t[a] < t[b] end

-- this is the spairs the game actually calls
_G.spairs = function(t, order)
    if order and order ~= sort_func_keys_ascend then
        return hspairs(t, order)
    else
        return mspairs_default(t)
    end
end

-- ---------------------------------------------------------------- axr_main
intercepts = {}
next_index = {}
errors = {}
local function printf(fmt, ...) errors[#errors+1] = string.format(fmt, ...) end

function callback_add(name)
    if (not intercepts[name]) then
        intercepts[name] = {}
        next_index[name] = 1
        rebuild(name)
    else
        printf("![axr_main callback_add] callback %s already exists!", name)
    end
end

function callback_set(name, func_or_userdata)
    if (func_or_userdata == nil) then
        printf("![axr_main callback_set] trying to set callback %s to nil function!", name)
        return
    end
    if (intercepts[name]) then
        intercepts[name][func_or_userdata] = next_index[name]
        next_index[name] = next_index[name] + 1
        rebuild(name)
    else
        printf("![axr_main callback_set] callback %s doesn't exist!", name)
    end
end

function callback_unset(name, func_or_userdata)
    if (intercepts[name]) then
        intercepts[name][func_or_userdata] = nil
        rebuild(name)
    else
        printf("![axr_main callback_unset] callback %s doesn't exist!", name)
    end
end

-- SHIPPED dispatcher
function make_callback(name, ...)
    if (intercepts[name]) then
        for func_or_userdata, v in spairs(intercepts[name], sort_func_values_ascend) do
            if (type(func_or_userdata) == "function") then
                func_or_userdata(...)
            elseif (func_or_userdata[name]) then
                func_or_userdata[name](func_or_userdata, ...)
            end
        end
    else
        printf("![axr_main make_callback] can't make callback to non existing intercept %s!", name)
    end
end

-- CANDIDATE: sorted array, rebuilt on mutation, replaced (never edited in place)
order_list = {}

function rebuild(name)
    local t = intercepts[name]
    if (not t) then order_list[name] = nil return end
    local keys, n = {}, 0
    for k in pairs(t) do n = n + 1; keys[n] = k end
    table.sort(keys, function(a, b) return t[a] < t[b] end)
    order_list[name] = keys
    return keys
end

function make_callback_fast(name, ...)
    local t = intercepts[name]
    if (t) then
        local list = order_list[name] or rebuild(name)
        for i = 1, #list do
            local func_or_userdata = list[i]
            if (t[func_or_userdata] ~= nil) then
                if (type(func_or_userdata) == "function") then
                    func_or_userdata(...)
                elseif (func_or_userdata[name]) then
                    func_or_userdata[name](func_or_userdata, ...)
                end
            end
        end
    else
        printf("![axr_main make_callback] can't make callback to non existing intercept %s!", name)
    end
end

-- ------------------------------------------------------------------ harness
trace = {}
function reset_state()
    intercepts = {}
    next_index = {}
    order_list = {}
    errors = {}
    trace = {}
end
function note(s) trace[#trace+1] = s end
function join(t) return table.concat(t, "|") end
"""


def _rt():
    lua = _luajit.LuaRuntime(unpack_returned_tuples=True)
    lua.execute(LUA)
    return lua


def _run(scenario, dispatcher):
    """Build the scenario fresh, dispatch with `dispatcher`, return (trace, errors)."""
    lua = _rt()
    lua.globals().reset_state()
    lua.execute(scenario.replace("DISPATCH", dispatcher))
    return lua.globals().join(lua.globals().trace), lua.globals().join(lua.globals().errors)


def _both(scenario):
    a = _run(scenario, "make_callback")
    b = _run(scenario, "make_callback_fast")
    return a, b


def _assert_same(scenario):
    (ta, ea), (tb, eb) = _both(scenario)
    assert ta == tb, f"trace diverged\n  shipped:   {ta}\n  candidate: {tb}"
    assert ea == eb, f"errors diverged\n  shipped:   {ea}\n  candidate: {eb}"
    return ta, ea


# --------------------------------------------------------------------------- 1
def test_plain_dispatch_order_and_args():
    trace, errs = _assert_same("""
        callback_add("actor_on_update")
        for i = 1, 8 do
            callback_set("actor_on_update", function(binder, delta)
                note(i .. ":" .. tostring(binder) .. ":" .. tostring(delta))
            end)
        end
        DISPATCH("actor_on_update", "B", 17)
    """)
    # registration order, both args through
    assert trace == "|".join(f"{i}:B:17" for i in range(1, 9))
    assert errs == ""


def test_many_listeners_keep_registration_order():
    trace, _ = _assert_same("""
        callback_add("actor_on_update")
        for i = 1, 125 do
            callback_set("actor_on_update", function() note(i) end)
        end
        DISPATCH("actor_on_update")
    """)
    assert trace == "|".join(str(i) for i in range(1, 126))


# --------------------------------------------------------------------------- 2
def test_unregister_during_dispatch_skips_the_victim():
    """hspairs snapshots the key array up front but re-reads `t[key]` on every
    step and skips the entry when the value has gone nil, so a listener removed
    by an earlier listener in the SAME pass is not called.  The candidate gets
    this from its `t[func_or_userdata] ~= nil` guard.

    Note this is live-GAMMA behaviour, not stock Anomaly: the table.sort spairs
    in _g.script would have called the victim, because its iterator returns
    `keys[i], t[keys[i]]` and the loop body only uses the key."""
    trace, _ = _assert_same("""
        callback_add("actor_on_update")
        local f1, f3
        f3 = function() note("c") end
        f1 = function() note("a"); callback_unset("actor_on_update", f3) end
        callback_set("actor_on_update", f1)
        callback_set("actor_on_update", function() note("b") end)
        callback_set("actor_on_update", f3)
        DISPATCH("actor_on_update")
        note("--")
        DISPATCH("actor_on_update")
    """)
    assert trace == "a|b|--|a|b"


def test_listener_unregistering_itself():
    trace, _ = _assert_same("""
        callback_add("actor_on_update")
        local me
        me = function() note("me"); callback_unset("actor_on_update", me) end
        callback_set("actor_on_update", me)
        callback_set("actor_on_update", function() note("other") end)
        DISPATCH("actor_on_update")
        note("--")
        DISPATCH("actor_on_update")
    """)
    assert trace == "me|other|--|other"


# --------------------------------------------------------------------------- 3
def test_register_during_dispatch_not_called_this_pass():
    trace, _ = _assert_same("""
        callback_add("actor_on_update")
        callback_set("actor_on_update", function()
            note("a")
            callback_set("actor_on_update", function() note("late") end)
        end)
        callback_set("actor_on_update", function() note("b") end)
        DISPATCH("actor_on_update")
        note("--")
    """)
    assert trace == "a|b|--"


# --------------------------------------------------------------------------- 4
def test_reregister_during_dispatch_does_not_double_fire():
    """The nastiest one: a listener bumps an ALREADY registered listener's index
    while the pass is in flight.  The key stays in both snapshots, only its value
    changed, so it must still fire exactly once in this pass."""
    trace, _ = _assert_same("""
        callback_add("actor_on_update")
        local last = function() note("z") end
        callback_set("actor_on_update", function()
            note("a")
            callback_set("actor_on_update", last)   -- re-register mid-pass
        end)
        callback_set("actor_on_update", function() note("b") end)
        callback_set("actor_on_update", last)
        DISPATCH("actor_on_update")
        note("--")
        DISPATCH("actor_on_update")
    """)
    assert trace.count("z") == 2          # once per pass, never twice in one
    assert trace == "a|b|z|--|a|b|z"


# --------------------------------------------------------------------------- 4
def test_duplicate_register_moves_to_end_and_fires_once():
    trace, _ = _assert_same("""
        callback_add("actor_on_update")
        local f = function() note("f") end
        callback_set("actor_on_update", f)
        callback_set("actor_on_update", function() note("g") end)
        callback_set("actor_on_update", f)          -- re-register: moves to the back
        DISPATCH("actor_on_update")
    """)
    assert trace == "g|f"


# --------------------------------------------------------------------------- 5
def test_unregister_unknown_function_is_a_noop():
    trace, errs = _assert_same("""
        callback_add("actor_on_update")
        callback_set("actor_on_update", function() note("a") end)
        callback_unset("actor_on_update", function() note("never") end)
        DISPATCH("actor_on_update")
    """)
    assert trace == "a"
    assert errs == ""


def test_unknown_callback_name_reports_the_same_error():
    trace, errs = _assert_same("""
        callback_unset("no_such_callback", function() end)
        callback_set("no_such_callback", function() end)
        DISPATCH("no_such_callback")
    """)
    assert trace == ""
    assert "non existing intercept no_such_callback" in errs
    assert "callback no_such_callback doesn't exist" in errs


def test_setting_a_nil_listener_reports_and_registers_nothing():
    trace, errs = _assert_same("""
        callback_add("actor_on_update")
        callback_set("actor_on_update", nil)
        callback_set("actor_on_update", function() note("a") end)
        DISPATCH("actor_on_update")
    """)
    assert trace == "a"
    assert "nil function" in errs


# --------------------------------------------------------------------------- 6
def test_table_listeners_are_called_as_methods():
    trace, _ = _assert_same("""
        callback_add("actor_on_update")
        local obj = { tag = "T" }
        obj.actor_on_update = function(self, binder, delta)
            note(self.tag .. ":" .. binder .. ":" .. delta)
        end
        local noop = { tag = "N" }          -- no method for this name: skipped entirely
        callback_set("actor_on_update", function(binder, delta) note("fn:" .. binder) end)
        callback_set("actor_on_update", obj)
        callback_set("actor_on_update", noop)
        DISPATCH("actor_on_update", "B", 3)
    """)
    assert trace == "fn:B|T:B:3"


# --------------------------------------------------------------------------- 7
def test_empty_callback_dispatches_nothing_without_error():
    trace, errs = _assert_same("""
        callback_add("actor_on_update")
        DISPATCH("actor_on_update")
    """)
    assert trace == ""
    assert errs == ""


def test_full_churn_sequence_stays_in_lockstep():
    """Register, dispatch, unregister half, re-register some, dispatch again."""
    trace, _ = _assert_same("""
        callback_add("npc_on_update")
        local fs = {}
        for i = 1, 12 do
            fs[i] = function(o) note(i .. o) end
            callback_set("npc_on_update", fs[i])
        end
        DISPATCH("npc_on_update", "x")
        note("--")
        for i = 1, 12, 2 do callback_unset("npc_on_update", fs[i]) end
        DISPATCH("npc_on_update", "y")
        note("--")
        callback_set("npc_on_update", fs[3])
        callback_set("npc_on_update", fs[1])
        DISPATCH("npc_on_update", "z")
    """)
    assert trace == (
        "|".join(f"{i}x" for i in range(1, 13))
        + "|--|" + "|".join(f"{i}y" for i in range(2, 13, 2))
        + "|--|" + "|".join(f"{i}z" for i in range(2, 13, 2)) + "|3z|1z"
    )


@pytest.mark.parametrize("dispatcher", ["make_callback", "make_callback_fast"])
def test_dispatcher_survives_a_listener_that_errors(dispatcher):
    """Neither dispatcher pcalls its listeners - an erroring listener aborts the
    whole pass in both. Documented, not fixed: changing it would change behaviour."""
    lua = _rt()
    lua.globals().reset_state()
    lua.execute("""
        callback_add("actor_on_update")
        callback_set("actor_on_update", function() note("a") end)
        callback_set("actor_on_update", function() error("boom") end)
        callback_set("actor_on_update", function() note("c") end)
    """)
    with _quiet_faulthandler():
        ok = lua.eval("function(n) return (pcall(_G[n], 'actor_on_update')) end")(dispatcher)
    assert ok is False
    assert lua.globals().join(lua.globals().trace) == "a"
