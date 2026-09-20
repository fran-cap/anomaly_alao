"""I-057 track B: what the bundle patch removes, per frame, per engine call.

This is the bench, and it is a CROSSING COUNT, not a timing.  Reason: every
engine call in the offline harness is a Lua table lookup, so a wall-clock number
here would measure the stub and not the game (the gen-4 lesson from I-050b:
crossings are not equally priced - `get_hud`/`time_global`-class getters are
~0.25 us and the ltx / console / UI calls carry the rest).  What IS meaningful
offline is exactly which calls disappear and how many per frame; the pricing is
then the pessimistic read, stated per row.

Crossing counts are deterministic, so unlike a timing they do not depend on the
box being quiet.  (`coord lock status` was still checked before and after.)

    py -3.12 lab/tools/i057_bundle_bench.py
"""
from __future__ import annotations

import collections
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "tests"))
sys.path.insert(0, str(HERE.parent / "coord"))

import i057_bundle_harness as H  # noqa: E402
import i057_bundle_patch as P  # noqa: E402
sys.path.insert(0, str(HERE.parent / "tests"))
import test_i057_bundle as T  # noqa: E402

# how a removed call should be priced, pessimistically
PRICE = {
    "r_value": "ini_file_ex read (2 crossings, no cache on this install)",
    "get_console_cmd": "console query",
    "SYS_GetParam": "system.ltx read",
    "section": "trivial getter ~0.25 us",
    "item_in_slot": "inventory getter",
    "get_hud": "trivial getter ~0.25 us",
    "GetCustomStatic": "UI lookup by name",
    "main_hud_shown": "trivial getter",
    "get_pda_menu": "UI object",
    "IsShown": "UI query",
    "inventory_opened": "UI query",
    "Frect": "allocation + crossing",
    "SetWndRect": "UI call",
}


def price(name):
    for k, v in PRICE.items():
        if name.startswith(k):
            return v
    return ""


def histogram(run, arm_a, arm_b, frames, label):
    ha = collections.Counter()
    hb = collections.Counter()
    for arm, h in ((arm_a, ha), (arm_b, hb)):
        arm.ns.on_game_start()
        for q in run(arm):
            h[q] += 1
    print(f"\n### {label} ({frames} frames)")
    print("| engine call | base/frame | patched/frame | pessimistic price |")
    print("|---|---:|---:|---|")
    total_a = total_b = 0
    for k in sorted(set(ha) | set(hb)):
        a, b = ha[k] / frames, hb[k] / frames
        total_a += ha[k]
        total_b += hb[k]
        if abs(a - b) < 1e-9:
            continue
        print(f"| `{k}` | {a:.2f} | {b:.2f} | {price(k)} |")
    print(f"| **total** | **{total_a / frames:.2f}** | **{total_b / frames:.2f}** | "
          f"**-{(total_a - total_b) / frames:.2f}/frame** |")
    return (total_a - total_b) / frames


def collect(fn):
    def run(arm):
        out = []
        for _ in range(1):
            pass
        return out
    return run


def main():
    outdir = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    if outdir is None:
        import tempfile
        outdir = Path(tempfile.mkdtemp(prefix="i057b"))
        P.main([str(outdir)])
        outdir = outdir / "gamedata" / "scripts"

    print("# I-057 track B: engine calls removed per frame")
    print("\nCounts from lab/tests/i057_bundle_harness.py, the same scenes the "
          "differential tests use.  Pricing is the reader's job; the gen-4 rule "
          "is that getters are ~0.25 us and ltx / console / UI calls are more.")

    def fluid(arm):
        out = []
        for sc in T.FLUID_SCENES:
            for _ in range(3):
                arm.set(**sc)
                arm.clear()
                arm.lua.execute('SendScriptCallback("actor_on_update")')
                out += arm.queries()
        return out

    def batt(arm):
        out = []
        for i in range(30):
            arm.set(tg=1000 + i * 2000, inv_open=(i % 11 == 5),
                    pda_shown=(i % 13 == 7), hud_shown=(i % 17 != 3),
                    charged=(i % 7 != 0))
            arm.clear()
            arm.lua.execute('SendScriptCallback("actor_on_update")')
            out += arm.queries()
        return out

    def gem(arm):
        out = []
        for i in range(40):
            arm.set(lum=0.1 + 0.01 * (i % 20), torch_on=(i % 6 < 2),
                    icon=(i % 23 == 0),
                    flash_sec="device_flashlight" if i % 5 == 0 else "device_torch",
                    detector=True if i % 4 == 0 else None)
            arm.clear()
            arm.lua.execute('SendScriptCallback("actor_on_update")')
            out += arm.queries()
        return out

    total = 0
    for name, mod, run, frames in (
        ("fluid_aim.script", "fluid_aim", fluid, len(T.FLUID_SCENES) * 3),
        ("battery_warning.script", "battery_warning", batt, 30),
        ("light_gem_mcm.script", "light_gem_mcm", gem, 40),
    ):
        a = H.Arm(P.source_for(name, False), mod)
        b = H.Arm(outdir / name, mod)
        total += histogram(run, a, b, frames, name)

    print("\n### actor_effects.script HUD_fog(false)")
    print("40 `Frect()` allocations and 40 `SetWndRect` UI calls per frame, "
          "every frame the breathing fog is not being drawn, re-zeroing "
          "rectangles that are already zero.  The latch leaves 40 on the first "
          "such frame and 0 after.  Worth 0 when the player's helmet does have "
          "a blur value and the fog is actually drawn - that branch is "
          "untouched; only the profiler can say which branch this save is in.")
    print(f"\n**Bundle total excluding the fog latch: -{total:.1f} engine calls "
          f"per frame.**")


if __name__ == "__main__":
    main()
