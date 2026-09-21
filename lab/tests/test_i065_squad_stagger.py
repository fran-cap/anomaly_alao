"""I-065: squad first-update bursts after a load.

The captures say every squad's first `update()` after a load is the expensive
one (target search), that the engine either sweeps all 523 of them in the
first-update frame behind the loading screen (mode A) or dribbles them out at
~20 per ALife tick in play (mode B, 25 frames of 12-40 ms), and that which of
the two you get is a coin flip per load.  The mod makes every load a mode-A
load and keeps a per-frame-budgeted drain as the fallback.

These tests are structural: where the work lands, that every squad gets exactly
one first update, that nobody's target changes, that the fallback is bounded.
No wall-clock number is asserted; the clock in here is simulated.
"""
from __future__ import annotations

import re

import pytest

pytest.importorskip("lupa")

from lupa import luajit20 as lupa  # noqa: E402

from i065_stagger_harness import LIVE_SQUAD, MOD, MOD_ROOT, World  # noqa: E402

HITCH_BAR_MS = 5.0


# ---------------------------------------------------------------------------
# the mod is self-contained and compiles
# ---------------------------------------------------------------------------

def test_mod_adds_only_new_scripts_and_replaces_nothing():
    files = sorted(p.relative_to(MOD_ROOT).as_posix() for p in MOD_ROOT.rglob("*") if p.is_file())
    assert files == ["README.md", "gamedata/scripts/zzz_alao_squad_stagger.script", "meta.ini"]


def test_mod_compiles_under_luajit20():
    rt = lupa.LuaRuntime()
    chk = rt.eval("function(s) local f, e = loadstring(s) return f ~= nil, e end")
    ok, err = chk(MOD.read_text(encoding="utf-8"))
    assert ok, err


def test_mod_patches_nothing_and_has_a_kill_switch():
    src = MOD.read_text(encoding="utf-8")
    code = "\n".join(line.split("--", 1)[0] for line in src.splitlines())
    # no assignment into another script's namespace, no class method replaced
    assert not re.search(r"\bsim_squad_scripted\s*\.\s*\w+\s*=", code)
    assert not re.search(r"\bsim_board\s*\.\s*\w+\s*=", code)
    assert not re.search(r"\b_G\s*\.", code)
    assert re.search(r"^local ENABLED\s*=\s*true", src, re.M)


def test_kill_switch_registers_nothing_but_still_logs():
    w = World(mode="B", config={"enabled": False})   # config lands before on_game_start
    assert w.g.listeners("actor_on_first_update") == 0
    assert w.g.listeners("actor_on_update") == 0
    assert any(l.startswith("[alao_stagger 1.0] installed: enabled=false") for l in w.log())
    w.load().frames(1200)
    assert not [u for u in w.updates() if u["by"] == "script"]
    assert max(w.in_play_ms().values()) >= 6 * HITCH_BAR_MS   # the bursts are back


def test_install_line():
    w = World()
    line = w.log()[0]
    assert line.startswith("[alao_stagger 1.0] installed: enabled=true sweep_at_load=true")
    assert "clock=os.clock" in line
    # nothing per-frame is registered until there is a backlog
    assert w.g.listeners("actor_on_update") == 0


# ---------------------------------------------------------------------------
# the defect, reproduced without the mod
# ---------------------------------------------------------------------------

def test_baseline_mode_b_puts_the_bursts_in_play():
    w = World(with_mod=False, mode="B").load().frames(1200)
    play = w.in_play_ms()
    assert max(play.values()) >= 6 * HITCH_BAR_MS          # 10 searching squads x 3 ms
    assert sum(1 for ms in play.values() if ms >= 12) == 3  # 60 squads / 20 per tick
    assert w.first_cb() == {i: 1 for i in w.squad_ids()}


def test_baseline_mode_a_hides_them_behind_the_loading_screen():
    w = World(with_mod=False, mode="A").load().frames(1200)
    assert max(w.in_play_ms().values()) < 1.5               # 20 cheap updates a tick
    assert w.frame_ms()[World.FIRST_UPDATE_FRAME] >= 90     # 30 searches


# ---------------------------------------------------------------------------
# with the mod: every load is a mode-A load
# ---------------------------------------------------------------------------

def test_mode_b_with_the_mod_has_no_burst_in_play():
    w = World(mode="B").load().frames(1200)
    assert max(w.in_play_ms().values()) < 1.5
    assert w.frame_ms()[World.FIRST_UPDATE_FRAME] >= 90
    # every squad got its first-update block exactly once, all of them from us
    assert w.first_cb() == {i: 1 for i in w.squad_ids()}
    firsts = [u for u in w.updates() if u["first"]]
    assert {u["by"] for u in firsts} == {"script"}
    assert {u["frame"] for u in firsts} == {World.FIRST_UPDATE_FRAME}
    # and the engine's later visits found nothing expensive left
    assert not [u for u in w.updates() if u["by"] == "engine" and u["searched"]]
    s = w.state()
    assert (s["known"], s["pending"], s["done"], s["skipped"], s["errors"]) == (60, 60, 60, 0, 0)
    assert s["draining"] is False and w.g.listeners("actor_on_update") == 0


def test_the_sweep_runs_in_id_order_like_the_engines():
    w = World(mode="B").load()
    ids = [u["id"] for u in w.updates()]
    assert ids == sorted(ids) == w.squad_ids()


def test_nobody_gets_a_different_target():
    """The search is order-sensitive in the stub.  Same order, same board, same
    answer: mode A without the mod, mode B without it, and both with it."""
    ref = World(with_mod=False, mode="A").load().frames(1200).targets()
    assert World(with_mod=False, mode="B").load().frames(1200).targets() == ref
    assert World(mode="B").load().frames(1200).targets() == ref
    assert World(mode="A").load().frames(1200).targets() == ref
    assert World(mode="A", sweep_before_listener=False).load().frames(1200).targets() == ref


def test_mode_a_engine_first_leaves_the_mod_nothing_to_do():
    w = World(mode="A", sweep_before_listener=True).load().frames(300)
    s = w.state()
    assert (s["known"], s["pending"], s["done"]) == (60, 0, 0)
    assert any("0 pending - the engine got there first" in l for l in w.log())
    assert all(u["by"] == "engine" for u in w.updates())


def test_mode_a_listener_first_costs_the_load_frame_once_not_twice():
    w = World(mode="A", sweep_before_listener=False).load()
    searched = [u for u in w.updates() if u["searched"]]
    assert len(searched) == 30 and all(u["by"] == "script" for u in searched)
    assert w.first_cb() == {i: 1 for i in w.squad_ids()}


def test_log_lines_have_the_documented_shape():
    w = World(mode="B").load()
    log = w.log()
    assert re.match(r"\[alao_stagger\] load sweep: 60 known, 60 pending, 60 updated in \d+ ms "
                    r"\(max one squad 3\.0 ms\), 0 errors, 0 left for the in-play drain", log[1])
    assert re.match(r"\[alao_stagger\] 60 squads, 1 frames, max per frame \d+\.\d ms "
                    r"\(in play 0\.0 ms\), max one squad 3\.0 ms, load sweep 60 in \d+ ms, "
                    r"drain frames 0, skipped 0, errors 0, drained at load", log[2])


def test_foreign_objects_on_the_board_are_left_alone():
    w = World(mode="B")
    w.g.add_foreign(5)
    w.g.SIMBOARD.squads[99999] = True      # on the board, no server object
    w.load()
    s = w.state()
    assert (s["known"], s["pending"], s["done"], s["errors"]) == (62, 60, 60, 0)


def test_an_erroring_update_is_contained_and_logged_once():
    w = World(mode="B")
    w.g.OBJECTS[103].explode = True
    w.g.OBJECTS[107].explode = True
    w.load()
    s = w.state()
    assert (s["done"], s["errors"]) == (60, 2)
    assert sum("failed, left to the engine" in l for l in w.log()) == 1
    assert w.first_cb() == {i: 1 for i in w.squad_ids()}


def test_level_change_second_first_update_is_a_no_op():
    w = World(mode="B").load().frames(50)
    n = len(w.updates())
    w.g.fire("actor_on_first_update")
    assert len([u for u in w.updates() if u["by"] == "script"]) == 60
    assert len(w.updates()) >= n
    assert any("0 pending" in l for l in w.log())


# ---------------------------------------------------------------------------
# the fallback: the budgeted in-play drain
# ---------------------------------------------------------------------------

def test_load_budget_overflow_is_drained_in_play_under_the_bar():
    w = World(mode="B", config={"load_budget_ms": 30}).load()
    s = w.state()
    assert 0 < s["load_n"] < 60 and s["draining"] is True
    assert w.g.listeners("actor_on_update") == 1
    w.frames(300)
    s = w.state()
    assert s["draining"] is False and w.g.listeners("actor_on_update") == 0
    assert s["done"] + s["skipped"] == 60
    # bounded: the budget plus at most the one indivisible update that crossed it
    # (the drain starts on frame 9, so its first ~55 frames are still behind the screen)
    after_load = {f: ms for f, ms in w.frame_ms().items() if World.FIRST_UPDATE_FRAME < f < 400}
    assert after_load and max(after_load.values()) <= 3 + 3.0 + 1e-6
    assert w.first_cb() == {i: 1 for i in w.squad_ids()}
    assert any(l.endswith(", drained") for l in w.log())


def test_pure_stagger_is_bounded_per_frame_and_nearest_first():
    w = World(mode="B", config={"sweep_at_load": False}).load()
    assert w.state()["draining"] is True
    assert w.frame_ms().get(World.FIRST_UPDATE_FRAME, 0) == 0
    w.frames(200)
    ups = [u for u in w.updates() if u["by"] == "script"]
    assert [u["id"] for u in ups] == w.squad_ids()          # distance grows with id in the stub
    per_frame = {}
    for u in ups:
        per_frame.setdefault(u["frame"], []).append(u)
    assert all(sum(1 for u in us if u["searched"]) <= 1 for us in per_frame.values())
    assert all(len(us) <= 8 for us in per_frame.values())
    assert max(w.frame_ms().values()) <= 3 + 3.0 + 1e-6
    s = w.state()
    assert s["done"] == 60 and s["max_play"] <= 6.0 and s["frames"] == len(per_frame)
    # all of it done long before the engine's own sweep would have started
    assert max(per_frame) < w.engine_starts_at
    assert re.search(r"\[alao_stagger\] 60 squads, \d+ frames, max per frame \d\.\d ms "
                     r"\(in play \d\.\d ms\)", w.log()[-1])


def test_pure_stagger_racing_the_engine_never_doubles_a_first_update():
    w = World(mode="B", squads=200, engine_starts_at=20, tick_every=5, per_tick=20,
              config={"sweep_at_load": False}).load().frames(400)
    s = w.state()
    assert s["done"] + s["skipped"] == 200 and s["skipped"] > 0
    assert w.first_cb() == {i: 1 for i in w.squad_ids()}
    assert s["draining"] is False


def test_drain_gives_up_loudly():
    w = World(mode="B", engine_starts_at=10 ** 9,
              config={"sweep_at_load": False, "max_drain_frames": 5}).load().frames(20)
    assert w.state()["draining"] is False
    assert any("GAVE UP with" in l for l in w.log())
    assert w.g.listeners("actor_on_update") == 0


def test_without_a_clock_the_drain_is_one_squad_a_frame():
    w = World(mode="B", clockless=True, engine_starts_at=10 ** 9,
              config={"sweep_at_load": False}).load().frames(100)
    assert "clock=NONE" in w.log()[0]
    ups = [u for u in w.updates() if u["by"] == "script"]
    frames = [u["frame"] for u in ups]
    assert len(ups) == 60 and len(set(frames)) == 60


def test_without_a_clock_the_load_sweep_still_does_everything():
    w = World(mode="B", clockless=True).load()
    assert w.state()["done"] == 60 and w.state()["draining"] is False


# ---------------------------------------------------------------------------
# what the mod assumes about the live sim_squad_scripted.script
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not LIVE_SQUAD.is_file(), reason="I-063 overlay not present")
def test_live_squad_script_still_has_the_shape_the_mod_relies_on():
    src = LIVE_SQUAD.read_bytes().decode("cp1251", errors="replace")
    # __init leaves the flag false, update() flips it before doing anything else
    assert re.search(r"self\.first_update\s*=\s*false", src)
    m = re.search(r"function sim_squad_scripted:update\(\)(.*?)\nend", src, re.S)
    assert m
    body = m.group(1)
    assert re.search(r"if not \(self\.first_update\) then\s*\n\s*self\.first_update = true", body)
    assert "cse_alife_online_offline_group.update(self)" in body
    # the reason the first update is the expensive one: the target is not saved
    w = re.search(r"function sim_squad_scripted:STATE_Write\(packet\)(.*?)\nend", src, re.S).group(1)
    assert "current_target_id" in w and "assigned_target_id" not in w
    g = re.search(r"function sim_squad_scripted:generic_update\(\)(.*?)\nend", src, re.S).group(1)
    assert "SIMBOARD:get_squad_target(self)" in g
