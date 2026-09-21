"""Gen-7 stacked arm: agent-I067-b (prewarm v1.4) plus the other three gen-7 mods' files.

    zzz_alao_spawn_prewarm.script   I-064 v1.1   from agent-I064-b
    zzz_alao_squad_stagger.script   I-065 v1.1   from agent-I065-b
    zzz_alao_vmm_cache.script       I-066 v1.0   from agent-I066-b

All four mods only ADD files, so the stack is a copy plus three scripts.  Compiles
everything and writes the request next to the overlays.

    py -3.12 lab/coord/gen7_stacked_build.py
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

OVERLAYS = Path(r"C:\code\GIT\anomaly_alao\lab\coord\overlays")
BASE = OVERLAYS / "agent-I067-b"
ADDED = (
    ("agent-I064-b", "zzz_alao_spawn_prewarm.script"),
    ("agent-I065-b", "zzz_alao_squad_stagger.script"),
    ("agent-I066-b", "zzz_alao_vmm_cache.script"),
)
OUT = OVERLAYS / "gen7-all-b"
REQUEST = OVERLAYS / "gen7-all-request.json"

NOTES = (
    "ATTENDED, SHORT: 2 repeats x 2 ARMS = 4 captures of 210 s. Baseline agent-I063-b (prewarm v1.3). Variant gen7-all-b = all four "
    "gen-7 mods stacked for the first time: prewarm v1.4 (I-067), spawn-prewarm 1.1 (I-064), squad-stagger 1.1 (I-065), vmm-cache 1.0 "
    "(I-066). Only files added. ROUTINE, same as 20260920-212446-I-064-6fab64: sit out the 30 s warm-up, at ~0:40 open the inventory, "
    "hold ~4 s, double-click the same consumable every capture, close, open once more, close; walk straight to the hamlet, stand ~10 s, "
    "walk back, stop ~30 s before the end. No PDA. This is a does-it-hold-together run, not a new measurement: READ THE LOG FIRST - "
    "'[alao_spawn 1.1]' prewarm line with ms and ~5278 cold loads now in the right slots, '[alao_stagger 1.1]' load sweep 523 updated / "
    "0 errors, '[alao_vmm_cache 1.0] patched' + cached keys, '[alao_prewarm 1.4] fdda' block. Then: no 4xx ms frame, no 455 ms burst "
    "train, lam2.script:271 under 1 ms, eng get_visible_value ~13 us/call, and what actor_on_first_update costs with all four in it "
    "(expect ~3.3 s vs ~2.0 s). Also note ui_inventory.script:93 first open per capture (open question from the last run) and ask the "
    "user how it felt. READ BACK WITH: py -3.12 lab/tools/profile_report.py --queue <id> --frames --axes --hitch --listeners"
)


def main() -> int:
    from lupa import luajit20 as lupa

    if OUT.exists():
        shutil.rmtree(OUT)
    shutil.copytree(BASE, OUT)
    scripts_dir = OUT / "gamedata" / "scripts"
    for overlay, name in ADDED:
        src = OVERLAYS / overlay / "gamedata" / "scripts" / name
        if not src.is_file():
            print(f"missing {src}")
            return 1
        if (scripts_dir / name).exists():
            print(f"{name} already in the base, refusing")
            return 1
        shutil.copy2(src, scripts_dir / name)

    lua = lupa.LuaRuntime()
    check = lua.eval("function(src, name) local f, e = loadstring(src, name) return f ~= nil, e end")
    bad = 0
    scripts = sorted(scripts_dir.glob("*.script"))
    for p in scripts:
        ok, err = check(p.read_bytes(), "@" + p.name)
        if not ok:
            bad += 1
            print(f"COMPILE FAIL {p.name}: {err}")
    print(f"{len(scripts)} scripts, {bad} compile failures -> {OUT}")
    if bad:
        return 1

    req = {
        "label": "gen7-all-stacked",
        "baseline_overlay": (OVERLAYS / "agent-I063-b").as_posix(),
        "variant_overlay": OUT.as_posix(),
        "baseline_overlay_bottom": (OVERLAYS / "ref3-vanilla-bottom").as_posix(),
        "variant_overlay_bottom": (OVERLAYS / "ref3-vanilla-bottom").as_posix(),
        "profiler_overlay": (OVERLAYS / "alao-profiler-walkout-listeners-inv").as_posix(),
        "repeats": 2,
        "duration_s": 210,
        "warmup_s": 30,
        "save": "gammabaseline",
        "notes": NOTES,
    }
    REQUEST.write_text(json.dumps(req, indent=2), encoding="utf-8")
    print(f"request -> {REQUEST}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
