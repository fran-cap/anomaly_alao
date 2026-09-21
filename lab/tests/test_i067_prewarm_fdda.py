"""I-067: alao-prewarm v1.4, the FDDA first-use prewarm.

What these pin down, in the order the lessons were paid for:

  * nothing the player can see or hear comes out of a prewarm - no hud motion
    started, no cam effector, no sound played, no item created;
  * the cold resource touches really do move out of play (counted against the
    REAL lam2 / consumables / backpack scripts when the GAMMA install is there);
  * after the loading screen the work is capped by COUNT - one step per frame -
    and the per-frame listener is gone again once the queue is dry;
  * every step says what it did in the log, and says so when it did nothing;
  * each feature has its own kill switch.
"""
from __future__ import annotations

import re

import pytest

from i067_fdda_harness import MOD, MOD_DIR, Arm, live_available

needs_live = pytest.mark.skipif(not live_available(), reason="GAMMA FDDA Redone scripts not on this box")

VISIBLE = ("play_hud_motion", "stop_hud_motion", "add_cam_effector", "play ", "stop ",
           "alife_create_item", "eat")


def visible(effects):
    return [e for e in effects if e.startswith(VISIBLE)]


def switch(name, value="false"):
    def edit(src):
        out, n = re.subn(rf"(?m)^(local {name}\s*=\s*)\S+", rf"\g<1>{value}", src)
        assert n == 1, name
        return out
    return edit


# ---------------------------------------------------------------------------
# files
# ---------------------------------------------------------------------------

def test_every_mod_script_compiles_and_is_crlf():
    from lupa import luajit20 as lupa
    rt = lupa.LuaRuntime()
    chk = rt.eval("function(s) local f, e = loadstring(s) return f ~= nil, e end")
    scripts = sorted(MOD_DIR.glob("*.script"))
    assert len(scripts) == 3
    for p in scripts:
        raw = p.read_bytes()
        ok, err = chk(raw.decode("utf-8"))
        assert ok, f"{p.name}: {err}"
        assert raw.count(b"\n") == raw.count(b"\r\n"), f"{p.name} is not CRLF"


def test_version_is_bumped_everywhere():
    for name in ("zzz_alao_prewarm.script", "zzz_alao_prewarm_fdda.script"):
        assert 'local VERSION = "1.4"' in (MOD_DIR / name).read_text(encoding="utf-8")
    assert "version=1.4" in (MOD_DIR.parent.parent / "meta.ini").read_text(encoding="utf-8")


def test_under_the_upvalue_limit():
    # LuaJIT refuses a function with more than 60 upvalues at load time, so the
    # compile test covers it; this keeps the file-level local count honest too.
    src = MOD.read_text(encoding="utf-8")
    assert len(re.findall(r"(?m)^local ", src)) < 60


def test_new_file_name_only():
    assert MOD.name == "zzz_alao_prewarm_fdda.script"
    src = MOD.read_text(encoding="utf-8")
    # never replaces or wraps anybody's function
    assert not re.search(r"(?m)^\s*(lam2|liz_fdda_\w+|game|level|actor_effects)\.\w+\s*=", src)
    for banned in ("play_hud_motion(", "stop_hud_motion(", "add_cam_effector(", ":play("):
        code = "\n".join(l.split("--")[0] for l in src.splitlines())
        assert banned not in code, banned


# ---------------------------------------------------------------------------
# the load-time pass
# ---------------------------------------------------------------------------

def test_load_pass_warms_backpack_and_ruck_and_shows_nothing():
    a = Arm()
    a.load_game()
    keys = {t["key"] for t in a.touches()}
    bp = r"dynamics\weapons\wpn_eat\backpack\wpn_backpack_act_hud"
    assert {"motions:item_ea_backpack_open_stalker_hud",
            "motions:item_ea_backpack_close_stalker_hud",
            "model:" + bp + "_stalker.ogf", "model:" + bp + ".ogf",
            r"sound:interface\item_usage\backpack_open",
            r"sound:interface\item_usage\backpack_close",
            r"camfile:itemuse_anm_effects\backpack_open.anm",
            "motions:item_ea_cmuphob_vodka_hud", "motions:item_ea_bread_hud",
            r"model:dynamics\weapons\wpn_eat\vodka_hud",
            r"sound:interface\item_usage\vodka_use",
            r"camfile:itemuse_anm_effects\vodka_use.anm"} <= keys
    # not in the ruck -> not warmed at load
    assert "motions:item_ea_medkit_hud" not in keys
    assert all(t["frame"] == 7 for t in a.touches())
    assert a.effects() == []
    assert a.listeners("actor_on_update") == 0


def test_each_resource_is_touched_once():
    a = Arm(ruck=["bread", "sausage", "bread", "vodka", "vodka"])
    a.load_game()
    keys = [t["key"] for t in a.touches()]
    assert len(keys) == len(set(keys))
    # bread and sausage share a sound and a cam file
    assert keys.count(r"sound:interface\item_usage\food_use") == 1


def test_faction_follows_the_actor_with_fddas_own_fallback():
    a = Arm(community="actor_dolg")
    a.load_game()
    keys = {t["key"] for t in a.touches()}
    assert "motions:item_ea_backpack_open_dolg_hud" in keys
    assert "motions:item_ea_backpack_open_stalker_hud" not in keys
    b = Arm(community="actor_greh")           # no greh sections in the stub config
    b.load_game()
    assert "motions:item_ea_backpack_open_stalker_hud" in {t["key"] for t in b.touches()}


def test_log_says_what_it_did():
    a = Arm()
    a.load_game()
    log = a.log()
    assert log[0] == "[alao_prewarm 1.4] fdda"
    text = "\n".join(log)
    assert "fdda: 3 backpack sections (faction stalker) + 2 animated item sections out of 4 items in the ruck, 15 steps" in text
    assert re.search(r"fdda motions: 4 hud sections warmed, 0 missing, 0 failed, \d+ ms", text)
    assert re.search(r"fdda models: 4 loaded, 0 missing, 0 failed, \d+ ms", text)
    assert re.search(r"fdda sounds: 4 built, 0 failed, \d+ ms \(held, never played\)", text)
    assert re.search(r"fdda cam files: 3 read, 0 not loose, 0 failed, \d+ ms", text)
    assert re.search(r"fdda: \d+ ms total, synchronous at first update", text)
    assert "fdda on-take: armed, 1 step(s) per frame" in text
    assert "[alao_prewarm]   fdda items: vodka, bread" in log


def test_log_says_so_when_there_is_nothing_to_do():
    a = Arm(ruck=["ammo_9x18"], edit=switch("FDDA_BACKPACK"))
    a.load_game()
    text = "\n".join(a.log())
    assert "0 backpack sections" in text and "fdda: nothing to warm" in text
    assert a.touches() == []

    b = Arm()
    b.rt.execute("lam2 = nil liz_fdda_redone_consumables = nil")
    b.load_game()
    assert "not installed, nothing to do" in "\n".join(b.log())
    assert b.touches() == []

    c = Arm(edit=switch("PREWARM_FDDA"))
    c.load_game()
    assert "fdda: switched off, nothing done" in "\n".join(c.log())
    assert c.touches() == [] and c.listeners("actor_on_item_take") == 0


def test_mcm_toggles_are_honoured():
    a = Arm()
    a.rt.execute("MCM['backpack/enable'] = false")
    a.load_game()
    keys = {t["key"] for t in a.touches()}
    assert not any("backpack" in k for k in keys)
    assert "backpack animation is off in MCM, skipped" in "\n".join(a.log())
    assert "motions:item_ea_bread_hud" in keys


@pytest.mark.parametrize("name,prefix", [
    ("FDDA_MOTIONS", "motions:"), ("FDDA_MODELS", "model:"),
    ("FDDA_SOUNDS", "sound:"), ("FDDA_CAM_FILES", "camfile:")])
def test_step_kill_switches(name, prefix):
    a = Arm(edit=switch(name))
    a.load_game()
    keys = [t["key"] for t in a.touches()]
    assert keys and not any(k.startswith(prefix) for k in keys)
    assert "switched off" in "\n".join(a.log())


def test_a_missing_mesh_is_never_handed_to_model_create():
    a = Arm()
    a.rt.execute(r"MISSING_MESH['dynamics\\weapons\\wpn_eat\\vodka_hud.ogf'] = true")
    a.load_game()          # the stub's motion_exists raises on a missing file
    text = "\n".join(a.log())
    assert "fdda models: 3 loaded, 1 missing, 0 failed" in text


def test_failures_are_counted_not_raised():
    a = Arm()
    a.rt.execute(r"BAD_SOUND['interface\\item_usage\\vodka_use'] = true "
                 r"PACKED_ANM['itemuse_anm_effects\\vodka_use.anm'] = true")
    a.load_game()
    text = "\n".join(a.log())
    assert "fdda sounds: 3 built, 1 failed" in text
    assert "fdda cam files: 2 read, 1 not loose, 0 failed" in text


def test_engine_without_the_bindings_degrades_to_a_log_line():
    a = Arm()
    a.rt.execute("game.motion_exists = nil io = nil")
    a.load_game()
    text = "\n".join(a.log())
    assert "fdda models: 0 loaded, 4 missing" in text
    assert "fdda cam files: 0 read, 3 not loose" in text
    assert "fdda motions: 4 hud sections warmed" in text


# ---------------------------------------------------------------------------
# the on-take drain
# ---------------------------------------------------------------------------

def test_on_take_drains_one_step_per_frame_then_unregisters():
    a = Arm()
    a.load_game()
    n0 = len(a.touches())
    a.take("medkit")
    assert len(a.touches()) == n0                 # nothing on the take frame itself
    assert a.listeners("actor_on_update") == 1
    per_frame = []
    for _ in range(8):
        before = len(a.touches())
        a.frame()
        per_frame.append(len(a.touches()) - before)
    assert per_frame == [1, 1, 1, 1, 0, 0, 0, 0]
    assert a.listeners("actor_on_update") == 0
    assert a.effects() == []
    text = "\n".join(a.log())
    assert re.search(r"\[alao_prewarm 1\.4\] fdda on-take: 4 steps over 4 frames, worst step \d+ ms", text)


def test_on_take_ignores_known_plain_and_preload_items():
    a = Arm()
    a.take("medkit")                              # before first update: the ruck scan's job
    assert a.listeners("actor_on_update") == 0
    a.load_game()
    n0 = len(a.touches())
    for sec in ("vodka", "bread", "ammo_9x18", "fdda_error_snd"):
        a.take(sec)
    a.frame(5)
    assert len(a.touches()) == n0
    assert a.listeners("actor_on_update") == 0


def test_bulk_take_stays_at_one_step_per_frame():
    a = Arm(ruck=[])
    a.load_game()
    n0 = len(a.touches())
    for sec in ("medkit", "vodka", "bread", "sausage"):
        a.take(sec)
    worst = 0
    for _ in range(40):
        before = len(a.touches())
        a.frame()
        worst = max(worst, len(a.touches()) - before)
    assert worst == 1
    # 4 motions + 4 models + 3 sounds (food_use shared) + 3 cam files
    assert len(a.touches()) - n0 == 14
    assert a.listeners("actor_on_update") == 0


def test_on_take_kill_switch():
    a = Arm(edit=switch("FDDA_ON_TAKE"))
    a.load_game()
    assert a.listeners("actor_on_item_take") == 0
    assert "fdda on-take: switched off" in "\n".join(a.log())


def test_level_change_redoes_motions_and_models_not_sounds():
    a = Arm()
    a.load_game()
    a.take("medkit")
    a.frame(1)
    a.rt.execute("fire('actor_on_net_destroy')")
    assert a.listeners("actor_on_update") == 0
    n0 = len(a.touches())
    a.load_game()
    again = [t["key"] for t in a.touches()[n0:]]
    assert any(k.startswith("motions:") for k in again)
    assert any(k.startswith("model:") for k in again)
    assert not any(k.startswith("sound:") for k in again)


# ---------------------------------------------------------------------------
# against the real FDDA Redone scripts
# ---------------------------------------------------------------------------

@needs_live
def test_live_without_the_mod_the_cold_touches_land_in_play():
    a = Arm(with_mod=False, live=True)
    a.load_game()
    a.open_inventory(frames=10)
    cold = a.cold_after(7)
    assert "motions:item_ea_backpack_open_stalker_hud" in cold
    assert any(k.startswith("model:") for k in cold)
    assert r"sound:interface\item_usage\backpack_open" in cold
    assert r"camfile:itemuse_anm_effects\backpack_open.anm" in cold


@needs_live
def test_live_with_the_mod_nothing_cold_is_left_for_play():
    base = Arm(with_mod=False, live=True)
    var = Arm(with_mod=True, live=True)
    for a in (base, var):
        a.load_game()
        a.open_inventory(frames=10)
        a.use_item("vodka", frames=600)       # open -> idle -> close -> vodka
        a.rt.execute("liz_fdda_redone_backpack.is_ui_inventory_open = false")
        a.frame(600)
    assert len(base.cold_after(7)) >= 8
    assert var.cold_after(7) == []
    # and the player sees and hears exactly the same things, in the same order
    assert visible(base.effects()) == visible(var.effects())
    assert any(e.startswith("play_hud_motion item_ea_cmuphob_vodka_hud") for e in var.effects())


@needs_live
def test_live_item_picked_up_later_is_warm_by_the_time_it_is_used():
    a = Arm(with_mod=True, live=True)
    a.load_game()
    a.take("medkit")
    a.frame(6)
    mark = a.rt.globals().FRAME
    a.use_item("medkit", frames=400)
    assert a.cold_after(mark) == []
