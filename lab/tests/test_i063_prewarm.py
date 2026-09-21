"""I-063: the prewarm bundle.

Differential tests against a stub engine: the same observable effects with and
without the mod, plus the properties the prewarm depends on (the GUI-nil
assumption, the slice budget, containment of a bad path, the level-change
rebuild).

No wall-clock number appears anywhere in here on purpose: everything the mod
removes is engine-side, and a stub cannot price an xml parse or a sound load.
What the tests count is how many COLD constructions happen while the player is
in control, which is deterministic.
"""
from __future__ import annotations

import pytest

pytest.importorskip("lupa")

from i063_prewarm_harness import Arm, LIVE_JUMP, LIVE_STEP, MOD  # noqa: E402

pytestmark = pytest.mark.skipif(
    not (LIVE_JUMP.is_file() and LIVE_STEP.is_file()),
    reason="GAMMA install not present",
)

# The loading screen is up for frames ~7..62 in every I-058 capture; anything
# the player can see starts after that.
IN_PLAY_FRAME = 100


def boot(with_mod: bool, frames_behind_screen: int = 55) -> Arm:
    """Load, first update, and let the loading screen run out."""
    a = Arm(with_mod)
    a.first_update()
    a.frame(frames_behind_screen)
    return a


# ---------------------------------------------------------------------------
# the mod compiles and is self-contained
# ---------------------------------------------------------------------------

def test_mod_adds_only_new_scripts_and_replaces_nothing():
    root = MOD.parent.parent.parent
    files = sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())
    assert files == [
        "README.md",
        "gamedata/scripts/modxml_zzz_alao_prewarm_tutorial.script",
        "gamedata/scripts/zzz_alao_prewarm.script",
        "gamedata/scripts/zzz_alao_prewarm_fdda.script",      # v1.4, I-067
        "meta.ini",
    ]


# ---------------------------------------------------------------------------
# what moves
# ---------------------------------------------------------------------------

def test_inventory_singleton_is_built_behind_the_loading_screen():
    a = Arm(True)
    assert int(a.g.UI_BUILDS) == 0
    a.first_update()
    assert int(a.g.UI_BUILDS) == 1, "UIInventory() must be built at actor_on_first_update"
    # ... and the first open in play builds nothing.
    a.frame(IN_PLAY_FRAME)
    a.clear_effects()
    a.g.ui_inventory.start("inventory")
    assert int(a.g.UI_BUILDS) == 1
    assert "UIInventory()" not in a.effects()


def test_without_the_mod_and_without_the_other_mods_the_first_open_pays_for_it():
    """The install this mod's inventory prewarm was written for.

    On GAMMA it is not that install - FDDA Redone and SortingPlus build the GUI
    at first update anyway - which is why the object prewarm reported
    "GUI already built, nothing to do" in every capture.
    """
    a = Arm(False)
    a.frame(IN_PLAY_FRAME)
    a.clear_effects()
    a.g.ui_inventory.start("inventory")
    assert int(a.g.UI_BUILDS) == 1
    assert "UIInventory()" in a.effects()


def test_encyclopedia_singleton_is_built_behind_the_loading_screen():
    a = Arm(True)
    a.first_update()
    assert int(a.g.ENC_BUILDS) == 1
    a.frame(IN_PLAY_FRAME)
    a.clear_effects()
    a.g.ui_pda_encyclopedia_tab.set_article("encyclopedia_factions_stalker")
    assert int(a.g.ENC_BUILDS) == 1
    assert "pda_encyclopedia_tab()" not in a.effects()

    b = boot(False)
    b.clear_effects()
    b.g.ui_pda_encyclopedia_tab.set_article("encyclopedia_factions_stalker")
    assert "pda_encyclopedia_tab()" in b.effects()


def test_no_cold_sound_construction_is_left_in_play():
    """The point of the whole thing, as a count rather than a timing."""
    with_mod = boot(True, frames_behind_screen=200)
    without = boot(False, frames_behind_screen=200)
    for a in (with_mod, without):
        a.g.FRAME = IN_PLAY_FRAME
        for mat in ("materials\\earth", "materials\\metal", "materials\\wood",
                    "materials\\gravel", "materials\\asphalt", "materials\\grass"):
            a.set(material=mat)
            a.jump_now()
            a.land_now()
            for _ in range(4):
                a.footstep(mat)

    assert without.cold_in_play(IN_PLAY_FRAME), "the baseline must have cold calls in play"
    assert with_mod.cold_in_play(IN_PLAY_FRAME) == [], (
        "every sound the player can trigger should already be warm: "
        f"{with_mod.cold_in_play(IN_PLAY_FRAME)[:5]}")


def test_the_queue_covers_what_the_scripts_ask_for():
    """Every path the baseline ever constructs must be in the prewarm queue."""
    a = boot(True, frames_behind_screen=200)
    queued = set(a.mod._alao_build_queue().values())

    b = Arm(False)
    for mat in ("materials\\earth", "materials\\metal", "materials\\wood",
                "materials\\gravel", "materials\\asphalt", "materials\\grass",
                "materials\\bricks", "materials\\bush", "materials\\sand",
                "materials\\metal_plate", "materials\\tin", "materials\\concrete"):
        b.set(material=mat)
        for _ in range(8):          # walk the whole sample queue for the material
            b.jump_now()
            b.land_now()
            b.footstep(mat)
    asked = {c["path"] for c in b.constructions()}
    missing = sorted(asked - queued)
    assert missing == [], f"prewarm queue misses {len(missing)} live paths: {missing[:8]}"


# ---------------------------------------------------------------------------
# differential: nothing else changes
# ---------------------------------------------------------------------------

def test_played_sounds_are_identical_with_and_without():
    a, b = boot(True, 200), boot(False, 200)
    for arm in (a, b):
        arm.clear_effects()
        arm.g.FRAME = IN_PLAY_FRAME
        for mat in ("materials\\earth", "materials\\metal", "materials\\wood"):
            arm.set(material=mat)
            arm.jump_now()
            arm.land_now(7.0)
            for _ in range(6):
                arm.footstep(mat)
    plays_a = [e for e in a.effects() if e.startswith("play ")]
    plays_b = [e for e in b.effects() if e.startswith("play ")]
    assert plays_a == plays_b
    assert plays_a, "the sound scripts must actually have played something"


def test_the_mod_plays_nothing_itself():
    a = boot(True, 300)
    assert [e for e in a.effects() if e.startswith("play ")] == []
    assert [e for e in a.effects() if e.startswith("stop ")] == []


def test_prewarmed_objects_are_never_handed_to_the_scripts():
    """The scripts keep building their own, so overlap behaviour is untouched."""
    a = boot(True, 300)
    before = len(a.constructions())
    a.g.FRAME = IN_PLAY_FRAME
    a.footstep("materials\\earth")
    after = a.constructions()
    new = after[before:]
    assert new, "footstep must still construct its own sound objects"
    assert all(not c["cold"] for c in new), "but every one of them is warm"


TWELVE = ["actor_item_to_ruck", "actor_item_to_slot", "actor_item_to_belt",
          "actor_on_item_drop", "actor_on_item_use", "actor_on_item_put_in_box",
          "actor_on_item_take_from_box", "npc_on_item_take", "npc_on_item_drop",
          "npc_on_use", "physic_object_on_use_callback", "actor_on_net_destroy"]


def test_the_twelve_listeners_are_not_a_non_identity_on_this_install():
    """`UIInventory:__init` registers twelve listeners, three of which do work
    outside their `IsShown()` guard.  Building GUI early was therefore listed as
    this mod's one behavioural non-identity - but on GAMMA it is not one, because
    FDDA Redone and SortingPlus already build the GUI at first update, so the
    listeners exist at exactly the same moment with or without this mod."""
    a, b = boot(True, 55), boot(False, 55)
    for n in TWELVE:
        assert int(a.g.listener_count(n)) == int(b.g.listener_count(n)) == 1, n


def test_the_twelve_listeners_still_move_on_an_install_without_those_mods():
    """Where it IS a non-identity, pin it so it stays known."""
    a, b = Arm(True), Arm(False)
    a.first_update(others_build_gui=False)
    b.g.SendScriptCallback("actor_on_first_update")
    for n in TWELVE:
        assert int(a.g.listener_count(n)) == 1, n
        assert int(b.g.listener_count(n)) == 0, n
    # and after the first open the two agree again
    b.g.ui_inventory.start("inventory")
    for n in TWELVE:
        assert int(a.g.listener_count(n)) == int(b.g.listener_count(n)) == 1, n


# ---------------------------------------------------------------------------
# the slice budget
# ---------------------------------------------------------------------------

def test_the_whole_queue_is_built_inside_first_update():
    """The v1.1 change: no slicing, nothing left for the player's frames.

    v1.0 sliced onto actor_on_update with a 0.5 ms budget, and the attended run
    20260920-165259-I-063-953526 showed why that cannot work: one cold
    sound_object costs 2-18 ms, so a budget only decides whether to START
    another item, never how long it runs.  109 slices over 0.1 ms, 3 of them
    over 6.4 ms, worst 17.7 ms, half of them after the loading screen dropped.
    """
    a = Arm(True)
    assert a.mod._alao_state()["slice_sounds"] is False, "the slicer must default off"
    assert len(a.constructions()) == 0
    a.first_update()
    st = a.mod._alao_state()
    expected = len(a.mod._alao_build_queue())
    assert expected > 100, "the queue should be the whole sound set"
    assert len(a.constructions()) == expected
    assert st["sounds_done"] is True
    assert st["slicing"] is False


def test_no_actor_on_update_listener_is_ever_registered():
    a = Arm(True)
    a.first_update()
    assert int(a.g.listener_count("actor_on_update")) == 0, (
        "the prewarm must not sit on the per-frame callback at all")
    a.frame(200)
    assert int(a.g.listener_count("actor_on_update")) == 0
    # and no construction happens on any frame after first update
    n = len(a.constructions())
    a.frame(200)
    assert len(a.constructions()) == n


def test_all_the_prewarm_cost_lands_on_the_first_update_frame():
    a = Arm(True)
    a.g.FRAME = 7
    a.first_update()
    frames = {c["frame"] for c in a.constructions()}
    assert frames == {7}, f"constructions leaked onto frames {sorted(frames)}"


def test_a_missing_sound_path_is_contained():
    a = Arm(True)
    a.lua.execute(r"""BAD_PATHS["jump\\jump_water_1"] = true""")
    a.first_update()
    st = a.mod._alao_state()
    assert st["sounds_done"] is True
    assert int(st["failed"]) >= 1
    assert int(st["qi"]) > len(a.mod._alao_build_queue()), "one bad path must not stop the queue"
    assert int(a.g.listener_count("actor_on_update")) == 0


def test_the_slicer_still_works_when_it_is_asked_for():
    """The escape hatch is off by default but must not have rotted."""
    a = Arm(True, slice_sounds=True)
    a.first_update()
    st = a.mod._alao_state()
    assert st["slicing"] is True
    assert int(a.g.listener_count("actor_on_update")) == 1
    a.frame(600)
    st = a.mod._alao_state()
    assert st["slicing"] is False
    assert int(st["qi"]) > len(a.mod._alao_build_queue())
    assert int(a.g.listener_count("actor_on_update")) == 0


# ---------------------------------------------------------------------------
# level change
# ---------------------------------------------------------------------------

def test_the_inventory_is_rebuilt_after_a_level_change():
    a = boot(True, 300)
    assert int(a.g.UI_BUILDS) == 1
    a.g.ui_inventory.net_destroy()            # UIInventory:actor_on_net_destroy
    assert a.g.ui_inventory.GUI is None
    a.first_update()                          # the next level's first update
    assert int(a.g.UI_BUILDS) == 2


def test_the_sound_queue_is_built_once_per_session():
    a = boot(True, 300)
    n = len(a.constructions())
    a.first_update()
    a.frame(100)
    assert len(a.constructions()) == n, "sounds are engine-global; do not redo them"


def test_prewarm_is_a_noop_when_the_gui_already_exists():
    a = Arm(True)
    a.g.ui_inventory.start("inventory")       # something opened it first
    assert int(a.g.UI_BUILDS) == 1
    a.first_update()
    assert int(a.g.UI_BUILDS) == 1


# ---------------------------------------------------------------------------
# v1.2 TARGET 1: the actor_bag cell pool
#
# The walk-out run 20260920-185607-I-062-a1c78b traced every inventory open:
#   open 1   cells 0 -> 19, grid 0 -> 7    17.4 / 9.2 / 7.9 / 12.0 ms
#   later    cells 19 -> 19, grid 7 -> 7    3.5 - 4.9 ms
# so the pool, not the GUI object, is what costs.  These tests count cell
# CONSTRUCTIONS, which is the thing that moves; no timing is claimed.
# ---------------------------------------------------------------------------

def test_the_first_open_builds_the_pool_without_the_prewarm():
    a = Arm(False)
    a.g.OTHER_MOD_BUILDS_GUI()          # FDDA / SortingPlus: GUI, but empty pool
    assert a.pool() == {"cells": 0, "grid": 0, "idxer": 0}
    before = int(a.g.CELL_BUILDS)
    a.open_inventory()
    assert a.pool()["cells"] == int(a.g.RUCK_N)
    assert int(a.g.CELL_BUILDS) - before == int(a.g.RUCK_N), (
        "this is the 17 ms: one UICellItem, four InitStatic, per stack")


def test_the_prewarm_grows_the_pool_at_first_update():
    a = Arm(True)
    a.first_update()
    p = a.pool()
    assert p["cells"] >= int(a.g.RUCK_N), p
    assert p["grid"] >= 1, p


def test_the_first_open_then_constructs_nothing():
    """The pass condition of the in-game run, as a construction count."""
    a = Arm(True)
    a.first_update()
    pre = a.pool()
    before = int(a.g.CELL_BUILDS)
    a.open_inventory()
    post = a.pool()
    assert int(a.g.CELL_BUILDS) == before, "the first open must build no cells"
    assert pre["cells"] == post["cells"], f"{pre} -> {post}"


# ---------------------------------------------------------------------------
# v1.3: the grid is the other high-water mark
#
# Run 20260920-194429-I-063-d9e519 traced the v1.2 variant's first open as
# `cells 31 -> 31, grid 3 -> 7`: the cells were pre-built, the grid was not.
# The cause is ordering - `zzz_alao_prewarm` sorts before
# `zzz_rax_sortingplus_mcm`, so the pool is laid out under the default
# sizekind sort and SortingPlus then switches to `kind`, which packs one kind
# group per row and needs more of them.
# ---------------------------------------------------------------------------

def test_v12_would_have_left_the_grid_short():
    """The bug, reproduced: lay the pool out under one sort, open under another."""
    # A kind-sorted open needs strictly more rows than a dense one for the same
    # ruck, which is why laying the pool out under the wrong sort leaves the
    # grid short.
    kind = Arm(False)
    kind.g.OTHER_MOD_BUILDS_GUI()
    kind.g.SORT_METHOD = "kind"
    kind.open_inventory()
    dense = Arm(False)
    dense.g.OTHER_MOD_BUILDS_GUI()
    dense.open_inventory()
    assert kind.pool()["grid"] > dense.pool()["grid"], (
        f"kind {kind.pool()['grid']} vs sizekind {dense.pool()['grid']}")
    # and v1.2's prewarm laid it out densely, because it runs before SortingPlus
    v12 = Arm(True)
    v12.first_update(sortingplus=None)
    assert v12.g.SORT_METHOD is None


def test_the_grid_survives_the_prewarm_and_the_first_open_never_grows():
    a = Arm(True)
    a.first_update()                        # SortingPlus switches to "kind"
    pre = a.pool()
    grows = int(a.g.GROWS)
    a.open_inventory()
    post = a.pool()
    assert int(a.g.GROWS) == grows, "the first open must not call Grow()"
    assert pre["grid"] == post["grid"], f"grid {pre['grid']} -> {post['grid']}"


def test_the_grid_is_provisioned_for_one_row_per_stack():
    """Grow() is a Lua table and `cols` booleans - no engine call - so buy plenty."""
    a = Arm(True)
    a.first_update()
    p = a.pool()
    assert p["grid"] >= p["cells"], f"{p}"


def test_the_grid_provisioning_holds_for_a_bigger_ruck():
    a = Arm(True)
    a.g.RUCK_N = 40
    a.first_update()
    pre = a.pool()
    grows = int(a.g.GROWS)
    a.open_inventory()
    assert int(a.g.GROWS) == grows, f"grew at open time from {pre}"


def test_headroom_covers_looting_a_few_more_stacks():
    a = Arm(True)
    a.first_update()
    before = int(a.g.CELL_BUILDS)
    a.g.RUCK_N = int(a.g.RUCK_N) + 8     # looted eight new stacks
    a.open_inventory()
    assert int(a.g.CELL_BUILDS) == before, "headroom should absorb this"


def test_the_prewarm_leaves_no_content_behind():
    """Pool retained, contents cleared: cc:Reset() is the last thing we do."""
    a = Arm(True)
    a.first_update()
    cc = a.g.ui_inventory.GUI.CC["actor_bag"]
    assert int(cc.idxer) == 0, "idxer must be back to 0"
    assert sum(1 for _ in cc.indx_id.items()) == 0, "no item may still be indexed"
    assert a.pool()["cells"] > 0, "but the cells stay"


def test_no_on_cc_add_callback_escapes_the_prewarm():
    """The non-identity, resolved twice over.

    `UICellContainer:Callback` dispatches only to `self.owner[func]`, so the one
    subscriber is `UIInventory:On_CC_Add`, whose whole body is
    `self.update_info = true`.  Nothing on the live stack overrides it (checked:
    29 `UIInventory.<x> =` assignments across the 1346 live winners, none of them
    `On_CC_Add`).  We suppress it anyway with `disable_callback` - the mod's own
    mechanism, already used for actor_equ / belt / quick / picker - and put the
    flags back afterwards.
    """
    a = Arm(True)
    a.first_update()
    assert int(a.g.CC_ADD_FIRED) == 0
    cc = a.g.ui_inventory.GUI.CC["actor_bag"]
    assert cc.disable_callback["On_CC_Add"] is None, "the flag must be restored"
    assert cc.disable_callback["On_CC_Remove"] is None
    # and a real open still fires it normally
    a.open_inventory()
    assert int(a.g.CC_ADD_FIRED) > 0


def test_the_pool_prewarm_is_a_noop_when_the_pool_already_exists():
    a = Arm(True)
    a.g.OTHER_MOD_BUILDS_GUI()
    a.open_inventory()                   # something opened it first
    before = int(a.g.CELL_BUILDS)
    a.first_update()
    assert int(a.g.CELL_BUILDS) == before


def test_the_prewarm_survives_nobody_else_building_the_gui():
    a = Arm(True)
    a.first_update(others_build_gui=False)   # nobody built it; ours does
    assert a.g.ui_inventory.GUI is not None
    assert a.pool()["cells"] >= int(a.g.RUCK_N)


# ---------------------------------------------------------------------------
# v1.2 TARGET 2: the tutorial sequencer
#
# `bind_campfire.script:176` had exactly ONE call of 705 / 716 ms per capture
# and every other call under 0.8 ms - the signature of a one-time engine cost,
# paid by whichever tutorial starts first.
# ---------------------------------------------------------------------------

def _campfire_walkup(arm):
    """What bind_campfire.script:176 does when the actor nears a campfire."""
    if not arm.g.game.has_active_tutorial():
        arm.g.game.start_tutorial("tutorial_campfire_ignite")


def test_without_the_prewarm_the_campfire_pays_the_cold_load():
    a = Arm(False)
    a.lua.execute("TUT_COLD_COST = 705000")     # 705 ms in harness microseconds
    a.g.OTHER_MOD_BUILDS_GUI()
    a.g.SendScriptCallback("actor_on_first_update")
    start = int(a.g.SIM_US)
    _campfire_walkup(a)
    assert int(a.g.SIM_US) - start == 705000, "this is the 786 ms frame"


def test_with_the_prewarm_the_campfire_is_free():
    a = Arm(True)
    a.lua.execute("TUT_COLD_COST = 705000")
    a.first_update()
    assert "start_tutorial alao_prewarm_noop" in a.effects()
    start = int(a.g.SIM_US)
    _campfire_walkup(a)
    assert int(a.g.SIM_US) - start == 0, "the sequencer was already warm"


def test_the_prewarm_stops_the_tutorial_it_started():
    a = Arm(True)
    a.first_update()
    assert a.g.game.has_active_tutorial() is False
    eff = a.effects()
    i = eff.index("start_tutorial alao_prewarm_noop")
    assert eff[i + 1] == "sequencer cold load"
    assert eff[i + 2] == "stop_tutorial alao_prewarm_noop", (
        "stop must follow start immediately, with nothing in between")


def test_an_unknown_tutorial_name_is_survivable():
    """If the DXML injection did not take, nothing breaks and the log says so."""
    a = Arm(True)
    a.lua.execute("TUTORIALS = {}")
    a.first_update()
    assert "start_tutorial UNKNOWN alao_prewarm_noop" in a.effects()
    assert a.g.game.has_active_tutorial() is False
    rep = " ".join(a.report())
    assert "started=false" in rep, rep


def test_the_prewarm_yields_to_a_tutorial_already_running():
    a = Arm(True)
    a.lua.execute('TUT_ACTIVE = "something_else"')
    a.first_update()
    assert a.g.TUT_ACTIVE == "something_else", "must not stop someone else's"
    assert "already active" in " ".join(a.report())


def test_the_dxml_script_replaces_no_file_and_is_side_effect_free():
    dxml = MOD.parent / "modxml_zzz_alao_prewarm_tutorial.script"
    assert dxml.is_file()
    src = dxml.read_text(encoding="utf-8")
    # the modpack-author protocol modxml_tutorial_hooks.script documents
    assert "modxml_tutorial_hooks.exceptions" in src
    node = src.split("local NODE = [[")[1].split("]]")[0]
    for forbidden in ("guard_key", "function_on_start", "function_on_stop",
                      "sound", "pause"):
        assert forbidden not in node, f"the no-op node must not carry {forbidden}"
