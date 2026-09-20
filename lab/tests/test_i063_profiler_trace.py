"""I-063: the per-call trace flag on the hitch profiler.

`max / first / log2 histogram` answers "is this listener a hitch" and cannot
answer "why is this call 19 ms and that one 4".  `TRACE_LISTENERS` names a
listener (or a label prefix) and emits one `ALAOPROF|1|trace|` line per call.

Two things must hold: with the flag at its default the build is exactly what the
locked overlays are, and with it on the lines appear, carry the same duration
the hitch half recorded, and stop at the cap.

Reuses `test_profiler_hitch`'s stub engine and driver, so no new fake engine.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

LAB = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LAB / "framework"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

lupa = pytest.importorskip("lupa.luajit20", reason="lupa (LuaJIT 2.0) not installed")

import test_profiler_hitch as H  # noqa: E402

TRACE_OFF = "local TRACE_LISTENERS   = nil"
TRACE_LINE = re.compile(r"ALAOPROF\|1\|trace\|n=(\d+)\|name=([^|]+)\|units=([\d.]+)"
                        r"\|frame=(\d+)\|t=(\d+)\|pre=([^|]*)\|post=([^|]*)")


def _traced_src(target="menu_open#", cap=None):
    s = H._src(listeners=True)
    assert s.count(TRACE_OFF) == 1, "the TRACE_LISTENERS switch moved"
    s = s.replace(TRACE_OFF, 'local TRACE_LISTENERS   = {"%s"}' % target, 1)
    if cap is not None:
        old = "local TRACE_MAX_LINES   = 400"
        assert s.count(old) == 1, "the TRACE_MAX_LINES switch moved"
        s = s.replace(old, "local TRACE_MAX_LINES   = %d" % cap, 1)
    return s


def _log_lines(lua):
    n = int(lua.eval("#__log"))
    return [lua.eval("__log")[i] for i in range(1, n + 1)]


def _traces(lua):
    out = []
    for ln in _log_lines(lua):
        m = TRACE_LINE.match(ln or "")
        if m:
            out.append({"n": int(m.group(1)), "name": m.group(2),
                        "units": float(m.group(3)), "frame": int(m.group(4)),
                        "t": int(m.group(5)), "pre": m.group(6), "post": m.group(7)})
    return out


requires_axr = pytest.mark.skipif(not H.REAL_AXR.is_file(),
                                  reason=f"no axr_main.script to load: {H.REAL_AXR}")


def test_the_flag_defaults_to_off_in_the_source():
    src = H.HITCH_LUA.read_text(encoding="utf-8")
    assert TRACE_OFF in src, "the shipped hitch profiler must trace nothing"
    assert "ALAOPROF|1|trace|" in src, "but the emitter must be there to switch on"


def test_default_build_emits_no_trace_lines(driven_default):
    assert _traces(driven_default) == []


@pytest.fixture(scope="module")
def driven_default():
    lua, _ = H._run(H._src())
    return lua


@pytest.fixture(scope="module")
def driven_traced():
    if not H.REAL_AXR.is_file():
        pytest.skip(f"no axr_main.script to load: {H.REAL_AXR}")
    lua, log = H._run(_traced_src(), real_axr=True)
    return {"lua": lua, "log": log}


@requires_axr
def test_one_line_per_call_of_the_named_listener(driven_traced):
    tr = _traces(driven_traced["lua"])
    assert len(tr) == int(driven_traced["lua"].eval("__menu_calls")) == 16, (
        "every call of the traced listener gets a line, including the ones in "
        "the window that never reached the log")
    assert all(t["name"].startswith("menu_open#") for t in tr)
    assert [t["n"] for t in tr] == list(range(1, 17))


@requires_axr
def test_the_traced_duration_matches_what_the_hitch_half_recorded(driven_traced):
    tr = _traces(driven_traced["lua"])
    rows = {r["name"]: r for r in driven_traced["log"].hitch_ranking(scope="lst")}
    row = next(r for n, r in rows.items() if n.startswith("menu_open#"))
    # the stub timer counts fake microseconds; the first call is 8 ms
    assert tr[0]["units"] == pytest.approx(8000, abs=50)
    assert tr[0]["frame"] == 700 == row["first_frame"]
    assert max(t["units"] for t in tr) / 1000.0 == pytest.approx(row["max_ms"], abs=0.3)
    # every later call is 5 ms
    assert all(t["units"] == pytest.approx(5000, abs=50) for t in tr[1:])


@requires_axr
def test_the_probe_runs_outside_the_timer(driven_traced):
    """pre/post are sampled around the call, and never inflate `units`.

    The stub has no ui_inventory, so the probe short-circuits to "na" - which is
    the point: a probe that cannot find its subject must cost nothing and say so
    rather than error inside a wrapped listener.
    """
    tr = _traces(driven_traced["lua"])
    assert all(t["pre"] == "na" and t["post"] == "na" for t in tr)
    assert all(t["units"] == pytest.approx(5000, abs=50) for t in tr[1:]), (
        "the probe leaked into the timed region")


@requires_axr
def test_untraced_listeners_get_no_lines(driven_traced):
    tr = _traces(driven_traced["lua"])
    assert not [t for t in tr if t["name"].startswith("cheap_thing#")]
    assert not [t for t in tr if t["name"].startswith("actor_on_update#")]


@requires_axr
def test_the_cap_stops_the_log_from_flooding():
    lua, _ = H._run(_traced_src(cap=4), real_axr=True)
    tr = _traces(lua)
    assert len(tr) == 4
    assert int(lua.eval("__menu_calls")) == 16, "capping the log must not skip calls"


@requires_axr
def test_tracing_does_not_change_the_per_frame_numbers():
    """The trace is an extra log line, not a different measurement."""
    _, plain = H._run(H._src(listeners=True), real_axr=True)
    _, traced = H._run(_traced_src(), real_axr=True)
    a = {r["name"]: r["ms_per_frame"] for r in plain.ranking(top=None, drop_first=0)}
    b = {r["name"]: r["ms_per_frame"] for r in traced.ranking(top=None, drop_first=0)}
    assert set(a) == set(b)
    for name in a:
        assert b[name] == pytest.approx(a[name], abs=0.005), name


def test_the_parser_ignores_trace_lines():
    """aalo/profiler.py must not choke on a kind it does not know."""
    from aalo import profiler as P
    text = ("ALAOPROF|1|hdr|ts=1|timer=profile_timer|units_per_ms=1000|calib_ms=250|"
            "calib_units=250000|calib_spin=9|overhead_ns=200|make_callback=true|"
            "binders=off|listeners=3:ok|dump_ms=30000|hitch=on|hitch_floor_ms=0.1|"
            "hitch_buckets=14\n"
            "ALAOPROF|1|trace|n=1|name=x#y.script:1|units=8000|frame=700|t=103500|"
            "pre=cells=0,grid=8,idxer=0|post=cells=41,grid=16,idxer=41\n"
            "ALAOPROF|1|win|seq=1|t0=0|t1=30000|span_ms=30000|frames=6000|"
            "total_units=100|calls=10|nested=0|names=1\n"
            "ALAOPROF|1|eow|seq=1\n")
    log = P.parse(text)
    assert log.errors == []
    assert log.header is not None and log.header.usable
    assert len(log.windows) == 1 and log.windows[0].complete
