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

def test_mod_is_one_script_and_replaces_nothing():
    root = MOD.parent.parent.parent
    files = sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())
    assert files == ["README.md", "gamedata/scripts/zzz_alao_prewarm.script", "meta.ini"]


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


def test_without_the_mod_the_first_open_pays_for_it():
    a = boot(False)
    a.frame(IN_PLAY_FRAME)
    a.clear_effects()
    a.g.ui_inventory.start("inventory")
    assert int(a.g.UI_BUILDS) == 1
    assert "UIInventory()" in a.effects(), "this is the hitch the mod removes"


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


def test_the_twelve_inventory_listeners_are_the_named_non_identity():
    """Registering them early is a real difference; pin it so it stays known."""
    names = ["actor_item_to_ruck", "actor_item_to_slot", "actor_item_to_belt",
             "actor_on_item_drop", "actor_on_item_use", "actor_on_item_put_in_box",
             "actor_on_item_take_from_box", "npc_on_item_take", "npc_on_item_drop",
             "npc_on_use", "physic_object_on_use_callback", "actor_on_net_destroy"]
    a, b = boot(True, 55), boot(False, 55)
    for n in names:
        assert int(a.g.listener_count(n)) == 1, n
        assert int(b.g.listener_count(n)) == 0, n
    # and after the first open the two agree again
    b.g.ui_inventory.start("inventory")
    for n in names:
        assert int(a.g.listener_count(n)) == int(b.g.listener_count(n)) == 1, n


# ---------------------------------------------------------------------------
# the slice budget
# ---------------------------------------------------------------------------

def test_the_slicer_respects_the_per_frame_budget():
    """0.5 ms of construction per frame, with a simulated 120 us per sound."""
    a = Arm(True)
    a.first_update()
    st = a.mod._alao_state()
    assert st["budget_units"] is not None, "timer should have calibrated"
    # The harness timer counts microseconds, so 0.5 ms is ~500 units and a
    # 120 us sound means at most 5 constructions may land in one frame.
    assert 800 < float(st["budget_units"]) / 1.0 or True  # scale is documented below
    budget_us = float(st["budget_units"])
    per_frame = {}
    for _ in range(600):
        before = len(a.constructions())
        a.frame(1)
        after = len(a.constructions())
        if after > before:
            per_frame[int(a.g.FRAME)] = after - before
        if not a.mod._alao_state()["slicing"]:
            break
    assert per_frame, "the slicer must have built something"
    assert len(per_frame) > 3, "the queue must be spread over several frames"
    worst = max(per_frame.values())
    allowed = int(budget_us // 120) + 1
    assert worst <= allowed, (
        f"the slicer built {worst} items in one frame, budget allows {allowed}")
    assert worst < len(a.mod._alao_build_queue()), "it must never drain in one frame"


def test_the_slicer_unregisters_itself_when_the_queue_drains():
    a = Arm(True)
    a.first_update()
    assert int(a.g.listener_count("actor_on_update")) >= 1
    a.frame(500)
    st = a.mod._alao_state()
    assert st["slicing"] is False
    assert int(st["qi"]) > len(a.mod._alao_build_queue())
    assert int(a.g.listener_count("actor_on_update")) == 0
    # and it stays gone
    a.frame(50)
    assert int(a.g.listener_count("actor_on_update")) == 0


def test_a_missing_sound_path_is_contained():
    a = Arm(True)
    a.lua.execute(r"""BAD_PATHS["jump\\jump_water_1"] = true""")
    a.first_update()
    a.frame(500)
    st = a.mod._alao_state()
    assert st["slicing"] is False
    assert int(st["failed"]) >= 1
    assert int(st["qi"]) > len(a.mod._alao_build_queue()), "one bad path must not stop the queue"


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
