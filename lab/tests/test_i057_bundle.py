"""I-057 track B: differential tests for the small-listener bundle.

For each patched file the test drives the real listener over a scripted
sequence of engine states and asserts that the EFFECT log - everything that
changes the game - is identical between the original and the patch, while
reporting how many engine QUERIES the patch removed.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "coord"))

import i057_bundle_harness as H  # noqa: E402
import i057_bundle_patch as P  # noqa: E402

pytestmark = pytest.mark.skipif(
    not all(P.source_for(n, False).is_file() for n in P.PATCHERS),
    reason="needs the GAMMA install and the gen4-all-b overlay",
)


@pytest.fixture(scope="module")
def outdir(tmp_path_factory):
    d = tmp_path_factory.mktemp("i057b")
    P.main([str(d)])
    return d / "gamedata" / "scripts"


def arms(outdir, name, modname):
    return (H.Arm(P.source_for(name, False), modname),
            H.Arm(outdir / name, modname))


# ------------------------------------------------------------------ fluid_aim
# kind/class are ltx data, so a different kind means a different section - the
# scenes keep that true because the patch's memo is keyed on the section.
FLUID_SCENES = [
    dict(section="wpn_ak74", kind="w_rifle", state=0, has_item=True),
    dict(section="wpn_ak74", kind="w_rifle", state=3, has_item=True),
    dict(section="wpn_knife", kind="w_melee", state=5, has_item=True),
    dict(section="wpn_knife", kind="w_melee", state=0, has_item=True, check_ui=True),
    dict(section="wpn_pm", kind="w_pistol", state=3, has_item=True, aim_toggle=True),
    dict(section="wpn_ak74_up", kind="w_rifle", state=1, has_item=True),
    dict(has_item=False),
    dict(section="wpn_ak74", kind="w_rifle", state=0, has_item=True, has_info=True),
    {"section": "wpn_binoc", "kind": "w_binoc", "class": "WP_BINOC",
     "state": 0, "has_item": True},
]


def _fluid_run(arm):
    arm.ns.on_game_start()
    eff, q = [], 0
    for sc in FLUID_SCENES:
        for _ in range(3):          # three frames per scene: caches warm up
            arm.set(**sc)
            arm.clear()
            arm.lua.execute('SendScriptCallback("actor_on_update")')
            eff.append(arm.effects())
            q += len(arm.queries())
    return eff, q


def test_fluid_aim_effects_identical(outdir, capsys):
    a, b = arms(outdir, "fluid_aim.script", "fluid_aim")
    ea, qa = _fluid_run(a)
    eb, qb = _fluid_run(b)
    assert ea == eb
    assert qb < qa
    with capsys.disabled():
        print(f"\nfluid_aim: {qa} -> {qb} engine queries over "
              f"{len(FLUID_SCENES) * 3} frames "
              f"({(qa - qb) / (len(FLUID_SCENES) * 3):.1f}/frame)")


def test_fluid_aim_option_change_invalidates(outdir):
    _, b = arms(outdir, "fluid_aim.script", "fluid_aim")
    b.ns.on_game_start()
    b.set(aim_toggle=False)
    b.lua.execute('SendScriptCallback("actor_on_update")')
    b.set(aim_toggle=True)
    b.lua.execute('SendScriptCallback("on_option_change")')
    b.clear()
    b.lua.execute('SendScriptCallback("actor_on_update")')
    # the re-read happened, so the console was consulted again
    assert any(x.startswith("get_console_cmd") for x in b.queries())


# ------------------------------------------------------------ battery_warning
def _batt_run(arm):
    arm.ns.on_game_start()
    eff, q = [], 0
    for i in range(30):
        arm.set(tg=1000 + i * 2000,
                inv_open=(i % 11 == 5),
                pda_shown=(i % 13 == 7),
                hud_shown=(i % 17 != 3),
                charged=(i % 7 != 0))
        arm.clear()
        arm.lua.execute('SendScriptCallback("actor_on_update")')
        eff.append(arm.effects())
        q += len(arm.queries())
    return eff, q


def test_battery_warning_effects_identical(outdir, capsys):
    a, b = arms(outdir, "battery_warning.script", "battery_warning")
    ea, qa = _batt_run(a)
    eb, qb = _batt_run(b)
    assert ea == eb
    assert qb < qa
    with capsys.disabled():
        print(f"battery_warning: {qa} -> {qb} engine queries over 30 frames "
              f"({(qa - qb) / 30:.1f}/frame)")


# -------------------------------------------------------------- light_gem_mcm
def _gem_run(arm):
    arm.ns.on_game_start()
    shown, colors, q = [], [], 0
    for i in range(40):
        arm.set(lum=0.1 + 0.01 * (i % 20),
                torch_on=(i % 6 < 2),
                icon=(i % 23 == 0),
                flash_sec="device_flashlight" if i % 5 == 0 else "device_torch",
                detector=True if i % 4 == 0 else None)
        arm.clear()
        arm.lua.execute('SendScriptCallback("actor_on_update")')
        eff = arm.effects()
        colors.append([e for e in eff if e.startswith("SetTextureColor")])
        for e in eff:
            if e.startswith("Show "):
                shown.append(e)
        q += len(arm.queries())
    return shown, colors, q


def test_light_gem_colors_identical(outdir, capsys):
    a, b = arms(outdir, "light_gem_mcm.script", "light_gem_mcm")
    sa, ca, qa = _gem_run(a)
    sb, cb, qb = _gem_run(b)
    assert ca == cb, "the gem colour is the whole visible output"
    # Show() is only re-asserted on a change, so the *sequence of distinct
    # values* must match, not the call count.  That is the documented
    # non-identity; assert it is exactly that and nothing more.
    def squash(xs):
        out = []
        for x in xs:
            if not out or out[-1] != x:
                out.append(x)
        return out
    assert squash(sa) == squash(sb)
    assert len(sb) < len(sa)
    assert qb < qa
    with capsys.disabled():
        print(f"light_gem_mcm: {qa} -> {qb} engine queries over 40 frames "
              f"({(qa - qb) / 40:.1f}/frame), Show() {len(sa)} -> {len(sb)}")


# -------------------------------------------------------------- actor_effects
# actor_effects.script is 1900 lines with a heavy load-time footprint, so the
# fog latch is tested on the HUD_fog function lifted out of both arms verbatim.
FOG_ENV = r"""
fogs = nil
fog_val, fog_tg, fog_cycle, fog_last_phase = 0, 0, 0, 0
helm_fog, curr_hud, opt = 0, nil, {}
function math_floor(x) return math.floor(x) end
"""


def _lift_hud_fog(path: Path) -> str:
    src = path.read_text(encoding="utf-8")
    i = src.index("function HUD_fog(")
    # the latch declaration, when present, sits on the lines above
    j = src.rindex("\n", 0, src.rindex("\n", 0, i))
    head = src[max(0, j - 400):i]
    head = "\n".join(l for l in head.splitlines() if l.startswith("local fog_zeroed"))
    end = src.index("\nend\n", src.index("local power = actor.power", i))
    return FOG_ENV + "\n" + head + "\n" + src[i:end + len("\nend\n")]


def test_actor_effects_fog_latch(outdir, capsys):
    orig = _lift_hud_fog(P.source_for("actor_effects.script", False))
    new = _lift_hud_fog(outdir / "actor_effects.script")
    assert "fog_zeroed" in new and "fog_zeroed" not in orig
    res = []
    for src in (orig, new):
        arm = H.Arm.__new__(H.Arm)
        arm.lua = H.lupa.LuaRuntime(unpack_returned_tuples=True)
        arm.lua.execute(H.PRELUDE)
        arm.g = arm.lua.globals()
        arm.state = arm.g.STATE
        arm.lua.execute(src)
        arm.clear()
        for _ in range(50):
            arm.lua.execute("HUD_fog(false)")
        res.append(arm.effects())
    a, b = res
    rects_a = sum(1 for e in a if e == "SetWndRect")
    rects_b = sum(1 for e in b if e == "SetWndRect")
    assert rects_a == 50 * 40, rects_a
    assert rects_b == 40, "the first call zeroes them, the other 49 do not"
    # nothing but SetWndRect differs
    assert [e for e in a if e != "SetWndRect"] == [e for e in b if e != "SetWndRect"]
    with capsys.disabled():
        print(f"actor_effects HUD_fog(false): {rects_a} -> {rects_b} SetWndRect "
              f"over 50 frames ({(rects_a - rects_b) / 50:.1f}/frame)")


def test_patchers_are_byte_asserting():
    for name, fn in P.PATCHERS.items():
        with pytest.raises(AssertionError):
            fn("-- not " + name + "\n")
