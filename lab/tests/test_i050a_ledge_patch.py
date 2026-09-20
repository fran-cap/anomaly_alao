"""I-050a: the ledge-grabbing patch must be invisible except in what it skips.

Every test drives the ORIGINAL script and the PATCHED one through the same
scripted sequence of camera / actor / MCM states under the same stub engine and
compares what a later frame can actually observe: savedClimbPos, savedInterPos,
savedCollisionPos, savedClimbNormal - the whole contract between
checkLedgeGrabbing and tryToClimb / onScreenCheck / checkJump.

Where the camera moves, the arms must also ask the engine for exactly the same
rays in the same order (that is what pins the shared scan ray and the db.actor
CSE). Where the camera is still, the patched arm is allowed - required - to ask
for nothing, and the saved state must still match.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

lupa = pytest.importorskip("lupa.luajit20")

from i050a_harness import Arm, MODDIR  # noqa: E402

REPO = Path(r"C:\code\GIT\anomaly_alao")
ALAO_COPY = REPO / "lab/coord/overlays/ref3-alao-b/gamedata/scripts/demonized_ledge_grabbing.script"
LIVE_COPY = MODDIR / "demonized_ledge_grabbing.script"

PATCHER = Path(__file__).resolve().parents[1] / "coord" / "i050a_ledge_patch.py"


@pytest.fixture(autouse=True)
def _quiet_faulthandler():
    """Silence pytest's faulthandler for these tests.

    Calling a Lua function from Python through lupa unwinds a C++ exception
    (0xe24c4a02) on the way back; faulthandler prints a full traceback for it
    on every single call even though nothing is wrong. Everything else in the
    lab suite keeps its faulthandler.
    """
    import faulthandler
    was = faulthandler.is_enabled()
    faulthandler.disable()
    try:
        yield
    finally:
        if was:
            faulthandler.enable()


def _orig_path() -> Path:
    if ALAO_COPY.is_file():
        return ALAO_COPY
    if LIVE_COPY.is_file():
        return LIVE_COPY
    pytest.skip("no copy of demonized_ledge_grabbing.script available")


@pytest.fixture(scope="module")
def scripts(tmp_path_factory):
    src = _orig_path()
    sys.path.insert(0, str(PATCHER.parent))
    import importlib.util
    spec = importlib.util.spec_from_file_location("i050a_ledge_patch", PATCHER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    out = tmp_path_factory.mktemp("i050a") / "patched.script"
    out.write_bytes(mod.patch(src.read_bytes().decode("utf-8")).encode("utf-8"))
    return src, out


def both(scripts, mcm=None):
    src, patched = scripts
    arms = []
    for p in (src, patched):
        a = Arm(p)
        a.set_mcm(**(mcm or {}))
        a.first_update()
        arms.append(a)
    return arms


def queries(log):
    """The engine work with an observable result: ray casts, normals, world2ui.

    Ray CONSTRUCTIONS are deliberately not in here - building one scan ray
    instead of fifteen is the point of part of the patch, and the counts are
    asserted on their own in test_scan_ray_built_once_per_scan.
    """
    return [e for e in log if not e.startswith("geometry_ray{")]


def builds(log):
    return [e for e in log if e.startswith("geometry_ray{")]


def step(arm, **kw):
    arm.place(**kw)
    arm.clear_log()
    arm.frame()
    return arm.saved(), arm.log()


# --------------------------------------------------------------------------
# 1. the patched file is still Lua 5.1 / LuaJIT 2.0
def test_patched_compiles(scripts):
    _, patched = scripts
    L = lupa.LuaRuntime()
    ok = L.eval("function(s) return loadstring(s) end")(patched.read_text(encoding="utf-8"))
    assert ok is not None


# --------------------------------------------------------------------------
# 2. a moving camera: identical saved state AND identical engine work
MOVE = [
    dict(cam=(0, 1.70, 0.0), dirv=(0, 0, 1), actor=(0, 0, 0.0), tg=1000),
    dict(cam=(0, 1.70, 0.5), actor=(0, 0, 0.5), tg=1016),
    dict(cam=(0, 1.71, 1.0), actor=(0, 0, 1.0), tg=1032),
    dict(cam=(0.1, 1.71, 1.4), dirv=(0.2, -0.1, 0.97), actor=(0.1, 0, 1.4), tg=1048),
    dict(cam=(0.2, 1.69, 1.9), dirv=(0.2, 0.3, 0.93), actor=(0.2, 0, 1.9), tg=1064),
]


@pytest.mark.parametrize("ledge_z", [None, 0.4, 0.9])
def test_moving_camera_is_identical(scripts, ledge_z):
    a, b = both(scripts)
    for arm in (a, b):
        arm.state.ledge_z = ledge_z
    for st in MOVE:
        sa, la = step(a, **dict(st))
        sb, lb = step(b, **dict(st))
        assert sa == sb, f"saved state diverged at {st}"
        assert queries(la) == queries(lb), f"engine work diverged at {st}"
        assert set(builds(lb)) <= set(builds(la)), f"new ray kinds at {st}"


# --------------------------------------------------------------------------
# 3. a frozen camera: same saved state, and the patched arm stops working
def test_frozen_camera_same_state_no_work(scripts):
    a, b = both(scripts)
    first = dict(cam=(0, 1.70, 0.0), dirv=(0, 0, 1), actor=(0, 0, 0.0), tg=1000)
    sa, la = step(a, **dict(first))
    sb, lb = step(b, **dict(first))
    assert sa == sb and queries(la) == queries(lb)

    work_a, work_b = [], []
    for i in range(1, 8):
        sa, la = step(a, tg=1000 + 16 * i)
        sb, lb = step(b, tg=1000 + 16 * i)
        assert sa == sb, "saved state diverged while standing still"
        work_a.append(len(la))
        work_b.append(len(lb))
    assert all(n > 0 for n in work_a), "original should keep scanning"
    assert work_b == [0] * 7, f"patched should do nothing: {work_b}"


def test_frozen_camera_near_a_ledge(scripts):
    """Standing still in front of a climbable edge: the answer must persist."""
    a, b = both(scripts)
    for arm in (a, b):
        arm.state.ledge_z = 0.4
    first = dict(cam=(0, 1.70, 0.0), dirv=(0, 0, 1), actor=(0, 0, 0.0), tg=1000)
    step(a, **dict(first))
    step(b, **dict(first))
    assert a.saved() == b.saved()
    assert a.saved()[0] != "nil", "the scenario must actually find a ledge"
    for i in range(1, 5):
        sa, la = step(a, tg=1000 + 16 * i)
        sb, lb = step(b, tg=1000 + 16 * i)
        assert sa == sb
        assert lb == []


# --------------------------------------------------------------------------
# 4. the guard must let go again
def test_camera_moves_after_being_frozen(scripts):
    a, b = both(scripts)
    for arm in (a, b):
        arm.state.ledge_z = 0.4
    seq = [dict(cam=(0, 1.70, 0.0), dirv=(0, 0, 1), actor=(0, 0, 0), tg=1000),
           dict(tg=1016), dict(tg=1032),
           dict(cam=(0, 1.70, 0.2), actor=(0, 0, 0.2), tg=1048),
           dict(tg=1064),
           dict(dirv=(0.5, 0, 0.87), tg=1080),
           dict(tg=1096),
           dict(cam=(0, 1.75, 0.2), tg=1112)]
    scanned = []
    for st in seq:
        sa, la = step(a, **dict(st))
        sb, lb = step(b, **dict(st))
        assert sa == sb, f"diverged at {st}"
        assert queries(la) == queries(lb) or lb == [], f"diverged at {st}"
        scanned.append(lb != [])
    # frames 0/3/5/7 moved something, 1/2/4/6 did not
    assert scanned == [True, False, False, True, False, True, False, True], scanned


def _vec(s):
    assert s != "nil"
    return [float(x) for x in s.strip("()").split(",")]


def test_sub_epsilon_jitter_is_bounded(scripts):
    """The guard's tolerance is a bound on staleness, and this measures it.

    CC_EPS_POS is 1e-4 m. That is not bit-exact, so this pins what it buys and
    what it costs: the patched arm stops working while the camera wanders
    inside the tolerance, and its answer then lags the original's by at most
    that much - against a scan that samples geometry every 87 mm.
    """
    import i050a_ledge_patch as P
    eps = float(P.EPS_POS)
    a, b = both(scripts)
    for arm in (a, b):
        arm.state.ledge_z = 0.4
    base = dict(cam=(0, 1.70, 0.0), dirv=(0, 0, 1), actor=(0, 0, 0), tg=1000)
    step(a, **dict(base)); step(b, **dict(base))
    worst, skipped = 0.0, 0
    for i in range(1, 10):
        jitter = eps * 0.4 * (1 if i % 2 else -1)
        st = dict(cam=(jitter, 1.70 + jitter, jitter), tg=1000 + 16 * i)
        sa, _ = step(a, **dict(st))
        sb, lb = step(b, **dict(st))
        if lb == []:
            skipped += 1
        worst = max(worst, max(abs(x - y) for x, y in zip(_vec(sa[0]), _vec(sb[0]))))
    assert skipped == 9, "the guard should absorb jitter inside its tolerance"
    assert worst <= eps, f"staleness {worst} exceeds the tolerance {eps}"


@pytest.mark.parametrize("field,val", [
    ("cam", (0.01, 1.70, 0.0)),
    ("cam", (0, 1.70, 0.01)),
    ("cam", (0, 1.71, 0.0)),
    ("dirv", (0.01, 0, 0.99995)),
    ("actor", (0, 0.01, 0)),
])
def test_any_input_change_rescans(scripts, field, val):
    a, b = both(scripts)
    base = dict(cam=(0, 1.70, 0.0), dirv=(0, 0, 1), actor=(0, 0, 0), tg=1000)
    step(a, **dict(base)); step(b, **dict(base))
    step(a, tg=1016); sb, lb = step(b, tg=1016)
    assert lb == [], "precondition: the guard is holding"
    sa, la = step(a, tg=1032, **{field: val})
    sb, lb = step(b, tg=1032, **{field: val})
    assert sa == sb
    assert queries(lb) == queries(la), "a changed input must redo the original's work"


# --------------------------------------------------------------------------
# 5. MCM changes invalidate the guard
@pytest.mark.parametrize("mcm", [
    {"alternativeClimbDetection": False},
    {"playerWidthCheck": False},
    {"raySteps": 8},
    {"climbTriggerDistance": 2.0},
    {"throttleCheck": 50},
    {"hardcoreMode": True},
])
def test_mcm_variants_identical(scripts, mcm):
    a, b = both(scripts, mcm)
    for arm in (a, b):
        arm.state.ledge_z = 0.4
    for i, st in enumerate(MOVE):
        sa, la = step(a, **dict(st))
        sb, lb = step(b, **dict(st))
        assert sa == sb, f"saved state diverged at frame {i} with {mcm}"
        assert queries(la) == queries(lb), f"engine work diverged at frame {i} with {mcm}"


def test_alt_detection_off_keeps_the_original_guard(scripts):
    """With alternativeClimbDetection off the mod's OWN guard does the skipping.

    The new guard sits in front of it, not instead of it, so a creep that is
    inside the original's 2 cm tolerance but outside the new guard's 0.1 mm one
    must still be skipped - by the original guard, in both arms.
    """
    a, b = both(scripts, {"alternativeClimbDetection": False})
    for arm in (a, b):
        arm.state.ledge_z = 0.4
    base = dict(cam=(0, 1.70, 0.0), dirv=(0, 0, 1), actor=(0, 0, 0), tg=1000)
    sa, la = step(a, **dict(base)); sb, lb = step(b, **dict(base))
    assert sa == sb and queries(la) == queries(lb)
    for i in range(1, 4):
        z = 0.005 * i                      # 5 mm a frame: under 2 cm, over 0.1 mm
        st = dict(cam=(0, 1.70, z), actor=(0, 0, z), tg=1000 + 16 * i)
        sa, la = step(a, **dict(st))
        sb, lb = step(b, **dict(st))
        assert sa == sb, f"diverged at creep {z}"
        assert la == [] and lb == [], "both arms should skip on the original guard"
    # and once the creep passes the original's own 2 cm tolerance, both scan
    st = dict(cam=(0, 1.70, 0.05), actor=(0, 0, 0.05), tg=1100)
    sa, la = step(a, **dict(st))
    sb, lb = step(b, **dict(st))
    assert sa == sb and queries(la) == queries(lb) and lb != []


def test_mcm_change_midway_rescans(scripts):
    a, b = both(scripts)
    for arm in (a, b):
        arm.state.ledge_z = 0.4
    base = dict(cam=(0, 1.70, 0.0), dirv=(0, 0, 1), actor=(0, 0, 0), tg=1000)
    step(a, **dict(base)); step(b, **dict(base))
    step(a, tg=1016); _, lb = step(b, tg=1016)
    assert lb == []
    for arm in (a, b):
        arm.set_mcm(raySteps=6)
    sa, la = step(a, tg=1032)
    sb, lb = step(b, tg=1032)
    assert sa == sb
    assert queries(lb) == queries(la) and lb != []


# --------------------------------------------------------------------------
# 6. the gates above checkLedgeGrabbing
def test_disabled_and_reenabled(scripts):
    a, b = both(scripts)
    for arm in (a, b):
        arm.state.ledge_z = 0.4
    base = dict(cam=(0, 1.70, 0.0), dirv=(0, 0, 1), actor=(0, 0, 0), tg=1000)
    step(a, **dict(base)); step(b, **dict(base))
    assert a.saved() == b.saved() and a.saved()[0] != "nil"
    for arm in (a, b):
        arm.set_mcm(enable=False)
    sa, _ = step(a, tg=1016); sb, _ = step(b, tg=1016)
    assert sa == sb and sa[0] == "nil", "disabling must clear savedClimbPos"
    for arm in (a, b):
        arm.set_mcm(enable=True)
    sa, la = step(a, tg=1032); sb, lb = step(b, tg=1032)
    assert sa == sb and sa[0] != "nil", "re-enabling must find the ledge again"
    assert queries(la) == queries(lb)


def test_precondition_flip(scripts):
    """mcClimb (on a ladder) blocks the scan; leaving the ladder must rescan."""
    a, b = both(scripts)
    for arm in (a, b):
        arm.state.ledge_z = 0.4
    base = dict(cam=(0, 1.70, 0.0), dirv=(0, 0, 1), actor=(0, 0, 0), tg=1000)
    step(a, **dict(base)); step(b, **dict(base))
    assert a.saved()[0] != "nil"
    for arm in (a, b):
        arm.state.move_state["mcClimb"] = True
    sa, _ = step(a, tg=1016); sb, _ = step(b, tg=1016)
    assert sa == sb and sa[0] == "nil"
    for arm in (a, b):
        arm.state.move_state["mcClimb"] = False
    sa, la = step(a, tg=1032); sb, lb = step(b, tg=1032)
    assert sa == sb and sa[0] != "nil"
    assert queries(la) == queries(lb)


def test_climb_active_then_reset(scripts):
    """A climb runs (climbActive), ends with reset(); the next frame rescans."""
    a, b = both(scripts)
    for arm in (a, b):
        arm.state.ledge_z = 0.4
    base = dict(cam=(0, 1.70, 0.0), dirv=(0, 0, 1), actor=(0, 0, 0), tg=1000)
    step(a, **dict(base)); step(b, **dict(base))
    assert a.saved()[0] != "nil"
    # tryToClimb sets climbActive; the end of the climb calls reset()
    for arm in (a, b):
        arm.lua.execute("")
        arm.ns.reset()
    sa, la = step(a, tg=1016); sb, lb = step(b, tg=1016)
    assert sa == sb
    assert queries(la) == queries(lb), "an external reset() must force a rescan"
    assert lb != [], "an external reset() must force a rescan"


# --------------------------------------------------------------------------
# 7. the shared scan ray is built once, and only once
def test_scan_ray_built_once_per_scan(scripts):
    src, patched = scripts
    a, b = both(scripts)
    st = dict(cam=(0, 1.70, 0.0), dirv=(0, 0, 1), actor=(0, 0, 0), tg=1000)
    _, la = step(a, **dict(st))
    _, lb = step(b, **dict(st))
    n_a = sum(1 for e in la if e.startswith("geometry_ray{"))
    n_b = sum(1 for e in lb if e.startswith("geometry_ray{"))
    assert n_a == 15, f"default raySteps is 15, got {n_a} constructions"
    assert n_b == 1, f"patched should build one scan ray, got {n_b}"
    # ...while asking for exactly the same queries
    assert [e for e in la if e.startswith("ray:get")] == \
           [e for e in lb if e.startswith("ray:get")]
