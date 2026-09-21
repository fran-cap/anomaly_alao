"""I-049: the patched drx_da_main dispatch must behave exactly like the original.

Both arms are built by `lab/tools/i049_model.py` out of the LIVE game files, so
these tests skip when the install is not present. What they assert:

* the patcher lifts the anomaly body out of the closure byte-for-byte;
* over thousands of frames with binders spawning and dying, both arms produce
  the identical sequence of (frame clock, binder, tg) calls;
* a body that spawns or destroys binders mid-pass is handled the way hspairs
  handled it (a new binder waits for the next frame, a destroyed one is
  skipped);
* `unregister_callbacks()` and a same-key re-register still work;
* the patched arm really does collapse N listeners into 1.
"""
from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest

LAB = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LAB / "tools"))
sys.path.insert(0, str(LAB / "coord"))

lupa = pytest.importorskip("lupa.luajit20")

try:
    import i049_model as model
    import i049_drx_da_patch as patcher
except ImportError as exc:  # pragma: no cover
    pytest.skip(f"i049 tooling unavailable: {exc}", allow_module_level=True)


def _sources():
    for p in (model.G_PATCHES, model.AXR_MAIN, model.DRX):
        if not p.is_file():
            pytest.skip(f"live source not present: {p}")
    original = model._read(model.DRX)
    return original, patcher.patch(original)


@pytest.fixture(scope="module")
def arms():
    original, patched = _sources()
    return model.arm_lua(None), model.arm_lua(patched), original, patched


def _rt(src):
    rt = lupa.LuaRuntime()
    rt.execute(src)
    return rt


def _trace(rt):
    t = rt.eval("TRACE")
    n = rt.eval("TRACE_N")
    return [t[i] for i in range(1, int(n) + 1)]


# --------------------------------------------------------------------------- #
# the patch itself
# --------------------------------------------------------------------------- #

def test_patcher_is_idempotent_on_anchors(arms):
    _, _, original, patched = arms
    # patching twice must fail loudly rather than emit a double patch
    with pytest.raises(AssertionError):
        patcher.patch(patched)
    assert "\r\r" not in patched
    assert patched.count("\r\n") == patched.count("\n")


def test_body_lifted_verbatim(arms):
    _, _, original, patched = arms
    assert model.lifted_body_is_verbatim(original, patched)


def test_patched_file_compiles_under_luajit(arms):
    _, _, _, patched = arms
    rt = lupa.LuaRuntime()
    assert rt.eval("loadstring")(patched, "@drx_da_main.script") is not None


def test_patched_still_calls_the_body_through_one_listener(arms):
    a_src, b_src, _, _ = arms
    a, b = _rt(a_src), _rt(b_src)
    for rt in (a, b):
        rt.execute('for i = 1, 40 do spawn_binder("z" .. i, i, 0) end')
    assert int(a.eval('listener_count("actor_on_update")')) == 40
    assert int(b.eval('listener_count("actor_on_update")')) == 1
    assert int(b.eval("registered_count()")) == 40


# --------------------------------------------------------------------------- #
# behavioural equivalence
# --------------------------------------------------------------------------- #

def _run_script(rt, events, frames, step):
    """events: {frame: [("spawn", key, seed, start) | ("destroy", key)]}"""
    for f in range(1, frames + 1):
        clock = f * step
        rt.execute("CLOCK = %d" % clock)
        for ev in events.get(f, ()):
            if ev[0] == "spawn":
                rt.execute('spawn_binder("%s", %d, %d)' % (ev[1], ev[2], ev[3]))
            else:
                rt.execute('destroy_binder("%s")' % ev[1])
        rt.execute('make_callback("actor_on_update")')
    return _trace(rt)


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_identical_trace_under_spawn_and_destroy_churn(arms, seed):
    a_src, b_src, _, _ = arms
    rnd = random.Random(seed)
    frames, step = 1200, 16          # ~19 s of game time at 60 fps
    live, nxt = [], 0
    events = {}
    for f in range(1, frames + 1):
        bucket = []
        if f == 1:
            for _ in range(60):
                nxt += 1
                key = "anom_%d" % nxt
                bucket.append(("spawn", key, rnd.randrange(1, 97),
                               rnd.randrange(0, 900)))
                live.append(key)
        else:
            if rnd.random() < 0.05:
                nxt += 1
                key = "anom_%d" % nxt
                bucket.append(("spawn", key, rnd.randrange(1, 97),
                               f * step + rnd.randrange(0, 900)))
                live.append(key)
            if live and rnd.random() < 0.04:
                key = live.pop(rnd.randrange(len(live)))
                bucket.append(("destroy", key))
        if bucket:
            events[f] = bucket

    ta = _run_script(_rt(a_src), events, frames, step)
    tb = _run_script(_rt(b_src), events, frames, step)
    assert len(ta) > 2000, "the scenario must actually exercise the bodies"
    assert ta == tb


def test_identical_when_the_same_key_is_respawned(arms):
    a_src, b_src, _, _ = arms
    events = {
        1: [("spawn", "anom_1", 3, 0), ("spawn", "anom_2", 5, 0)],
        4: [("spawn", "anom_1", 11, 0)],          # re-register, no destroy
        9: [("destroy", "anom_1")],
        12: [("spawn", "anom_1", 7, 0)],
    }
    ta = _run_script(_rt(a_src), events, 40, 20)
    tb = _run_script(_rt(b_src), events, 40, 20)
    assert ta == tb


def test_unregister_callbacks_clears_both_arms(arms):
    a_src, b_src, _, _ = arms
    a, b = _rt(a_src), _rt(b_src)
    for rt in (a, b):
        rt.execute('for i = 1, 12 do spawn_binder("z" .. i, i, 0) end')
        rt.execute("unregister_callbacks()")
        rt.execute("CLOCK = 10000")
        rt.execute('make_callback("actor_on_update")')
    assert _trace(a) == []
    assert _trace(b) == []
    assert int(b.eval("registered_count()")) == 0


def test_mid_pass_destroy_and_spawn_match_hspairs(arms):
    """A body that kills a later binder and spawns a new one, mid-dispatch."""
    a_src, b_src, _, _ = arms
    hook = """
    local fired = false
    local orig = anomaly_body
    function install_hook()
      local outer = record
      _G.record = function(key, tg)
        outer(key, tg)
        if key == "anom_2" and not fired then
          fired = true
          destroy_binder("anom_5")          -- a later entry: must be skipped
          spawn_binder("anom_new", 4, 0)    -- must NOT fire this frame
        end
      end
    end
    """
    out = []
    for src in (a_src, b_src):
        rt = _rt(src)
        rt.execute(hook)
        rt.execute('for i = 1, 8 do spawn_binder("anom_" .. i, i, 0) end')
        rt.execute("install_hook()")
        for f in range(1, 6):
            rt.execute("CLOCK = %d" % (f * 40))
            rt.execute('make_callback("actor_on_update")')
        out.append(_trace(rt))
    # WHO fires on each frame must match; the ORDER within a frame must not be
    # compared. After a mid-pass register/unregister the shipped hspairs heap is
    # reseeded from pairs() over function keys, so arm A's order is pointer order
    # (I-051: 14 distinct orders in 30 replays). Asserting out[0] == out[1] here
    # failed about 1 run in 8 for exactly that reason. Trace entries lead with the
    # frame clock, so sorting compares the per-frame multisets.
    assert sorted(out[0]) == sorted(out[1])
    # arm B (the shared dispatcher) IS deterministic: registration order, every run
    b_first = [x.split("|")[1] for x in out[1] if x.startswith("40|")]
    assert b_first == ["anom_1", "anom_2", "anom_3", "anom_4", "anom_6", "anom_7", "anom_8"]
    first_frame = [x for x in out[0] if x.startswith("40|")]
    assert not any("anom_5" in x for x in first_frame), "destroyed mid-pass must be skipped"
    assert not any("anom_new" in x for x in first_frame), "spawned mid-pass waits a frame"
    assert any("anom_new" in x for x in out[0]), "and then does fire"


def test_compaction_keeps_registration_order(arms):
    """Holes are punched and compacted; order and identity must survive."""
    _, b_src, _, _ = arms
    rt = _rt(b_src)
    rt.execute('for i = 1, 30 do spawn_binder("anom_" .. i, i, 0) end')
    rt.execute('for i = 2, 30, 2 do destroy_binder("anom_" .. i) end')
    rt.execute("CLOCK = 5000")
    rt.execute('make_callback("actor_on_update")')
    keys = [x.split("|")[1] for x in _trace(rt)]
    assert keys == ["anom_%d" % i for i in range(1, 31, 2)]
    n, holes = rt.eval("anomaly_update_count()")
    assert (int(n), int(holes)) == (15, 0), "compaction runs after the walk"
    rt.execute("CLOCK = 6000")
    rt.execute('make_callback("actor_on_update")')
    keys2 = [x.split("|")[1] for x in _trace(rt)][15:]
    assert keys2 == keys
