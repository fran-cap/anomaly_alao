"""I-057 track A: differential tests for the on-demand ledge scan.

Arms: `gen4-all-b`'s demonized_ledge_grabbing.script (ALAO + I-050a) and the
same file through `lab/coord/i057_ledge_ondemand_patch.py`.  Both run on the
I-050a stub engine (`i050a_harness`), which logs every geometry_ray
construction and query, so "how much engine work did this frame ask for" is
directly observable.

The contract the patch has to keep: whenever something READS the scan's output
(tryToClimb / onScreenCheck, i.e. only from the climb keybind), it must see the
same value it would have seen before.  So these tests drive frames, fire key
events, and compare the saved* tuple captured inside a stubbed tryToClimb.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "coord"))

import i050a_harness as H  # noqa: E402
import i057_ledge_ondemand_patch as P  # noqa: E402

pytestmark = pytest.mark.skipif(
    not (P.GEN4.is_file() and H.MCM.is_file()),
    reason="needs the gen4-all-b overlay and the extracted GAMMA corpus",
)

# on_key_press/on_key_hold reach two ui_mcm helpers the I-050a harness does not
# stub (it only ever called checkLedgeGrabbing).  Both are driven from Python.
KEY_STUBS = r"""
KEY = {mod = true, double = false, hold = false}
ui_mcm.get_mod_key = function() return KEY.mod end
ui_mcm.double_tap = function() return KEY.double end
ui_mcm.key_hold = function() return KEY.hold end
USES = {}
USEN = 0
"""

# tryToClimb is 400 lines of animation and camera driving; what matters here is
# only what it would have read.  Same replacement in both arms.
PROBE_USE = r"""
function(ns)
    ns.tryToClimb = function(a)
        USEN = USEN + 1
        USES[USEN] = table.concat({tostring(a), ns.__probe()}, "|")
        return false
    end
end
"""


@pytest.fixture(scope="module")
def patched(tmp_path_factory):
    out = tmp_path_factory.mktemp("i057") / "demonized_ledge_grabbing.script"
    P.main([str(out)])
    return out


class Rig:
    def __init__(self, script: Path):
        self.arm = H.Arm(script)
        self.arm.lua.execute(KEY_STUBS)
        self.arm.lua.eval(PROBE_USE)(self.arm.ns)
        self.g = self.arm.lua.globals()

    def start(self, **mcm):
        self.arm.set_mcm(**mcm)
        self.arm.first_update()

    def frame(self, cam=None, dirv=None, actor=None, tg=None):
        self.arm.place(cam=cam, dirv=dirv, actor=actor, tg=tg)
        self.arm.frame()

    def press(self, dik=57):
        self.arm.ns.on_key_press(dik)

    def uses(self):
        n = int(self.g.USEN)
        return [self.g.USES[i] for i in range(1, n + 1)]

    def rays(self):
        return sum(1 for e in self.arm.log() if e.startswith("ray:get"))

    def clear_log(self):
        self.arm.clear_log()


def walk(n=40, z0=0.0):
    """A moving scene: the camera and actor advance 1 cm per frame."""
    for i in range(1, n + 1):
        z = z0 + i * 0.01
        yield dict(cam=(0.0, 1.70 + 0.002 * (i % 3), z),
                   dirv=(0.01 * (i % 5), 0.0, 1.0),
                   actor=(0.0, 0.0, z),
                   tg=1000 + i * 16)


def _both(patched, mcm=None, ledge=None):
    rigs = []
    for script in (P.GEN4, patched):
        r = Rig(script)
        if ledge is not None:
            r.arm.state.ledge_z, r.arm.state.ledge_y = ledge
        r.start(**(mcm or {}))
        rigs.append(r)
    return rigs


@pytest.mark.parametrize("ledge", [(None, 1.5), (0.25, 1.5), (0.18, 1.9)])
def test_same_answer_at_every_use(patched, ledge):
    """Key presses at the same engine state as the frame just run: what the
    reader sees must be identical in both arms, open ground or ledge."""
    a, b = _both(patched, ledge=ledge)
    seen = {id(a): [], id(b): []}
    for i, st in enumerate(walk(40)):
        for r in (a, b):
            r.frame(**st)
            if i % 7 == 3:          # press on some frames, not others
                r.press()
                # the whole contract between the scan and its readers
                seen[id(r)].append(r.arm.saved())
    assert seen[id(a)], "no press was observed"
    assert seen[id(a)] == seen[id(b)]
    assert a.uses() == b.uses()


def test_ledge_scene_actually_reaches_tryToClimb(patched):
    """Guard against the above passing because nothing ever climbs."""
    a, b = _both(patched, ledge=(0.25, 1.5))
    for i, st in enumerate(walk(40)):
        for r in (a, b):
            r.frame(**st)
            if i % 7 == 3:
                r.press()
    assert a.uses(), "the ledge scene never reached tryToClimb"
    assert a.uses() == b.uses()


def test_key_hold_and_double_tap_paths(patched):
    a, b = _both(patched, mcm={"inputMethod": "both", "modifier": 0})
    for st in walk(20):
        for r in (a, b):
            r.frame(**st)
    for r in (a, b):
        r.press()
        r.g.KEY.hold = True
        r.arm.ns.on_key_hold(57)
    assert a.uses(), "no use recorded"
    assert a.uses() == b.uses()


def test_no_rays_without_demand(patched):
    """The point of the patch: an idle moving frame asks the engine for nothing."""
    a, b = _both(patched)
    for st in walk(30):
        for r in (a, b):
            r.clear_log()
            r.frame(**st)
    assert a.rays() == 15, "baseline should cast settings.raySteps rays per frame"
    assert b.rays() == 0


def test_rays_on_demand(patched):
    """...and a key press pays for exactly one scan."""
    a, b = _both(patched)
    for st in walk(10):
        for r in (a, b):
            r.frame(**st)
    for r in (a, b):
        r.clear_log()
        r.press()
    assert b.rays() == 15
    assert a.rays() == 0     # the unpatched arm scans from the listener only


def test_debug_mode_still_scans_every_frame(patched):
    """debugMode redraws gizmos from the listener, so it keeps the old path."""
    b = Rig(patched)
    b.start()
    b.arm.ns.toggleDebugMode()
    for st in walk(5):
        b.clear_log()
        b.frame(**st)
    assert b.rays() == 15


def test_scan_before_first_update_is_refused(patched):
    """ledgeScanNow bails out until actor_on_first_update has set savedCamY;
    the unpatched listener is never registered before that point either."""
    b = Rig(patched)
    b.arm.set_mcm()              # settings only, no first_update
    b.clear_log()
    b.press()
    assert b.rays() == 0


def test_patch_is_byte_asserting():
    """Every anchor is asserted; a changed upstream file must fail loudly."""
    with pytest.raises(AssertionError):
        P.patch("-- not the ledge grabbing script\n")
