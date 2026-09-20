"""I-050b: differential + crossing-count tests for the zzz_player_injuries patch.

Both arms are driven through the same scripted sequence of stubbed engine states
and their observable traces - every engine call that changes anything, in order,
with its arguments - must be byte-identical.  The crossing counts must not be.

The script under test is a third-party GAMMA mod file read from the read-only
overlay/corpus, so every test skips when those are not on this machine.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LAB / "tools"))

pytest.importorskip("lupa")

import i050b_injuries_env as env          # noqa: E402
import i050b_injuries_patch as patcher    # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def _quiet_faulthandler():
    """LuaJIT 2.0 unwinds through Windows SEH with code 0xe24c4a02, which
    faulthandler prints as a 'Windows fatal exception' even though nothing is
    wrong (it happens on the unpatched file too, and the run completes).  Keep
    it out of this module's output."""
    import faulthandler
    was = faulthandler.is_enabled()
    faulthandler.disable()
    yield
    if was:
        faulthandler.enable()

pytestmark = pytest.mark.skipif(
    not env.ALAO_LIVE.is_file(),
    reason=f"live ALAO copy not on this machine: {env.ALAO_LIVE}",
)


@pytest.fixture(scope="module")
def patched_path(tmp_path_factory):
    out = tmp_path_factory.mktemp("i050b") / "zzz_player_injuries.script"
    patcher.main(["patch", str(out)])
    return out


def _arm(path, mcm=None):
    lua, M = env.make_runtime(path, mcm)
    env.boot(lua, M)
    return lua, M


def _trace(lua):
    g = lua.globals()
    return [g["__trace"][i] for i in range(1, len(g["__trace"]) + 1)]


def _drive(lua, M, script):
    """Run a scripted sequence; return (observable trace, engine crossings)."""
    g = lua.globals()
    g["__reset_trace"]()
    cb = g["__callbacks"]
    for step in script:
        kind = step[0]
        if kind == "frame":
            for _ in range(step[1]):
                env.tick(lua)
        elif kind == "actor":                       # set a stubbed actor field
            g["__actor_fields"][step[1]] = step[2]
        elif kind == "limb":                        # set a body-part hp
            M["health"][step[1]] = step[2]
        elif kind == "damage":
            M["recieved_damage"](step[1], step[2])
        elif kind == "call":                        # a module function by name
            M[step[1]](*step[2:])
        elif kind == "cb":                          # a registered callback
            cb[step[1]](*step[2:])
        elif kind == "latch":                       # the 0.2 s scuffed_fix event
            M["worst_possible_fix"]()
        else:                                       # pragma: no cover
            raise AssertionError(kind)
    return _trace(lua), int(g["__engine_calls"])


# The scenarios.  Each one is (name, [steps]).
SCENARIOS = [
    ("healthy idle", [("frame", 10)]),
    (
        "taking damage",
        [("frame", 2), ("damage", 0.4, 12), ("frame", 3),
         ("damage", 0.9, 3), ("frame", 5)],
    ),
    (
        "healing",
        [("damage", 0.6, 12), ("frame", 2),
         ("cb", "actor_on_item_use", None, "medkit_army"), ("frame", 4),
         ("cb", "actor_on_item_use", None, "bandage"), ("frame", 4)],
    ),
    (
        "limb states",
        [("limb", "leftleg", 0), ("frame", 3),
         ("limb", "rightleg", 0), ("frame", 3),
         ("limb", "leftarm", 0), ("limb", "rightarm", 0), ("frame", 3),
         ("limb", "head", 2), ("frame", 3),
         ("limb", "leftleg", 5), ("limb", "rightleg", 5), ("frame", 3)],
    ),
    (
        "actor condition moves",
        [("actor", "health", 0.8), ("frame", 2),
         ("actor", "power", 0.3), ("frame", 2),
         ("actor", "power", 1.0), ("actor", "health", 1.0), ("frame", 2)],
    ),
    (
        "hud toggled through all three modes",
        [("frame", 2),
         ("cb", "on_key_press", 35), ("frame", 3), ("latch",), ("frame", 3),
         ("cb", "on_key_press", 35), ("frame", 3), ("latch",), ("frame", 3),
         ("cb", "on_key_press", 35), ("frame", 3), ("latch",), ("frame", 3)],
    ),
    (
        "inventory preview bars",
        [("frame", 2),
         ("cb", "ActorMenu_on_mode_changed", 1, 0), ("frame", 2),
         ("cb", "ActorMenu_on_mode_changed", 0, 1), ("frame", 2)],
    ),
    (
        "sleep and death",
        [("frame", 2), ("cb", "actor_on_sleep", 4), ("frame", 3),
         ("cb", "actor_on_before_death"), ("frame", 3)],
    ),
    (
        "footsteps",
        [("frame", 2),
         ("cb", "actor_on_footstep", "mat", 1.0, False, 0), ("frame", 2),
         ("cb", "actor_on_footstep", "mat", 1.0, False, 0), ("frame", 2)],
    ),
    (
        "mcm option change mid game",
        [("frame", 2), ("cb", "on_option_change"), ("frame", 3)],
    ),
    (
        "limping legs drive the footstep animations",
        [("limb", "leftleg", 1), ("frame", 4),
         ("cb", "actor_on_footstep", "mat", 1.0, False, 0), ("frame", 2),
         ("limb", "rightleg", 1), ("frame", 4),
         ("cb", "actor_on_footstep", "mat", 1.0, False, 0), ("frame", 2)],
    ),
]


@pytest.mark.parametrize("name,script", SCENARIOS, ids=[s[0] for s in SCENARIOS])
@pytest.mark.parametrize("text_hud", [False, True], ids=["picture-hud", "text-hud"])
def test_observable_behaviour_is_identical(patched_path, name, script, text_hud):
    mcm = dict(env.MCM, TEXT_BASED_PATCH=text_hud)
    base_lua, base_M = _arm(env.ALAO_LIVE, mcm)
    new_lua, new_M = _arm(patched_path, mcm)
    base_trace, base_calls = _drive(base_lua, base_M, script)
    new_trace, new_calls = _drive(new_lua, new_M, script)
    assert new_trace == base_trace, (
        f"{name}: first divergence at step "
        f"{next((i for i, (a, b) in enumerate(zip(base_trace, new_trace)) if a != b), '<length>')}"
    )
    assert new_calls <= base_calls


@pytest.mark.parametrize("text_hud", [False, True], ids=["picture-hud", "text-hud"])
def test_script_state_matches_after_the_long_scenario(patched_path, text_hud):
    """Beyond the trace: the tables the script keeps must end up the same."""
    mcm = dict(env.MCM, TEXT_BASED_PATCH=text_hud)
    script = [s for _, steps in SCENARIOS for s in steps]
    out = []
    for path in (env.ALAO_LIVE, patched_path):
        lua, M = _arm(path, mcm)
        _drive(lua, M, script)
        out.append({
            k: dict(M[k]) for k in ("health", "timedhp", "maxhp", "hud_blink_timer")
        })
    assert out[0] == out[1]


def test_crossings_per_frame_drop_in_the_live_configuration(patched_path):
    """The whole point: fewer Lua->C crossings in the steady state the profiler saw."""
    base_lua, base_M = _arm(env.ALAO_LIVE)
    new_lua, new_M = _arm(patched_path)
    base_n, base_counts, base_trace = env.frame_profile(base_lua, 1)
    new_n, new_counts, new_trace = env.frame_profile(new_lua, 1)
    assert base_trace == new_trace
    # the numbers quoted in the report
    assert base_n == 104, base_counts
    assert new_n == 45, new_counts
    # the individual claims
    assert base_counts["get_hud"] == 16 and new_counts["get_hud"] == 1
    assert base_counts["time_global"] == 16 and new_counts["time_global"] == 1
    assert base_counts["hud:GetCustomStatic"] == 29
    assert new_counts["hud:GetCustomStatic"] == 15
    assert base_counts.get("ini:r_string", 0) == 2      # TEXT_BASED_PATCH, twice
    assert "ini:r_string" not in new_counts
    assert base_counts["actor:get_movement_speed"] == 1
    assert "actor:get_movement_speed" not in new_counts
    # SetProgressPos is the real HUD work and must be untouched
    assert base_counts["bar:SetProgressPos"] == new_counts["bar:SetProgressPos"] == 14


def test_patch_is_deterministic_and_reapplies_cleanly(tmp_path):
    a = tmp_path / "a.script"
    b = tmp_path / "b.script"
    patcher.main(["patch", str(a)])
    patcher.main(["patch", str(b)])
    assert a.read_bytes() == b.read_bytes()
    # and it refuses to patch its own output (every anchor is gone)
    with pytest.raises(AssertionError):
        patcher.patch(a.read_bytes().decode("cp1251"))


def test_patched_file_compiles_under_luajit(patched_path):
    from lupa import luajit20 as lupa

    lua = lupa.LuaRuntime()
    src = patched_path.read_bytes().decode("cp1251")
    lua.globals()["__src"] = src
    ok = lua.execute("local f, err = loadstring(__src, '@zzz'); return f ~= nil and 'ok' or err")
    assert ok == "ok", ok
