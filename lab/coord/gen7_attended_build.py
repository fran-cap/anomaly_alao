"""Gen-7 attended arm: agent-I067-b (prewarm v1.4) plus the I-064 spawn prewarm file.

One attended session instead of two.  Both mods only ADD files, so the combined
variant is a copy plus one script; the builder checks that, compiles everything
and writes the request next to the overlays.

    py -3.12 lab/coord/gen7_attended_build.py
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

OVERLAYS = Path(r"C:\code\GIT\anomaly_alao\lab\coord\overlays")
BASE = OVERLAYS / "agent-I067-b"
SPAWN = OVERLAYS / "agent-I064-b" / "gamedata" / "scripts" / "zzz_alao_spawn_prewarm.script"
OUT = OVERLAYS / "gen7-attended-b"
REQUEST = OVERLAYS / "gen7-attended-request.json"

NOTES = (
    "ATTENDED, 3 repeats x 2 ARMS = 6 captures of 210 s. Baseline agent-I063-b (prewarm v1.3). Variant gen7-attended-b = "
    "agent-I067-b (prewarm v1.4, FDDA first-use prewarm) + zzz_alao_spawn_prewarm.script (I-064, throwaway class-profile "
    "stalker created and released at actor_on_first_update so the ~5251 cold specific_character loads land behind the loading "
    "screen). Only files added, nothing replaced. ROUTINE, identical in all six: sit out the 30 s warm-up doing nothing. At "
    "~0:40 open the inventory, hold it ~4 s so the backpack animation plays, double-click ONE consumable (the first name on the "
    "variant's '[alao_prewarm]   fdda items:' log line, the same one every capture), close, open the inventory once more, close. "
    "Then walk STRAIGHT to the hamlet that triggers the fast-travel discovery by the usual route, stand ~10 s, walk back, stop "
    "moving ~30 s before the end. No PDA. READ THE LOG FIRST: '[alao_spawn] prewarm: created and released ... N cold character "
    "loads absorbed' (N under 500 = do NOT credit it), '[alao_spawn] squad ... K cold character loads' at the hamlet (K over 500 "
    "in a variant capture = the prewarm did not cover it), '[alao_prewarm 1.4] fdda' block with its models / sounds ms. TARGETS: "
    "I-064 the 430-456 ms frame at hamlet arrival (baseline 8 of 8) gone in the variant, and what the actor_on_first_update frame "
    "pays for it; I-067 A1 lam2.script:271 ~10 ms about 1 s after the first inventory open -> 1-3 ms, A2 first consumable "
    "13.5-30 ms -> judge on repeats 2-3 (cold-launch outlier lands on the first baseline capture). READ BACK WITH: "
    "py -3.12 lab/tools/profile_report.py --queue <id> --frames --axes --trace --hitch --listeners"
)


def main() -> int:
    from lupa import luajit20 as lupa

    if not SPAWN.is_file():
        print(f"missing {SPAWN}")
        return 1
    if OUT.exists():
        shutil.rmtree(OUT)
    shutil.copytree(BASE, OUT)
    dest = OUT / "gamedata" / "scripts" / SPAWN.name
    if dest.exists():
        print(f"{dest.name} already in the base, refusing")
        return 1
    shutil.copy2(SPAWN, dest)

    lua = lupa.LuaRuntime()
    check = lua.eval("function(src, name) local f, e = loadstring(src, name) return f ~= nil, e end")
    bad = 0
    scripts = sorted((OUT / "gamedata" / "scripts").glob("*.script"))
    for p in scripts:
        ok, err = check(p.read_bytes(), "@" + p.name)
        if not ok:
            bad += 1
            print(f"COMPILE FAIL {p.name}: {err}")
    print(f"{len(scripts)} scripts, {bad} compile failures -> {OUT}")
    if bad:
        return 1

    req = {
        "label": "gen7-attended-I064-I067",
        "baseline_overlay": (OVERLAYS / "agent-I063-b").as_posix(),
        "variant_overlay": OUT.as_posix(),
        "baseline_overlay_bottom": (OVERLAYS / "ref3-vanilla-bottom").as_posix(),
        "variant_overlay_bottom": (OVERLAYS / "ref3-vanilla-bottom").as_posix(),
        "profiler_overlay": (OVERLAYS / "alao-profiler-walkout-listeners-inv").as_posix(),
        "repeats": 3,
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
