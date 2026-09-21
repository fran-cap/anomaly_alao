"""Build the I-064 variant overlay: agent-I063-b plus the spawn prewarm script.

The baseline arm of the I-064 comparison is `agent-I063-b` itself (full ALAO +
the four gen-4 patches + I-057 + alao-prewarm v1.3), so the variant is that
tree with exactly one file added:

    gamedata/scripts/zzz_alao_spawn_prewarm.script    lab/mods/alao-spawn-prewarm

Nothing is replaced.  The name is new - no mod in the enabled modlist ships a
file called that, and the builder checks - so the overlay cannot shadow anybody.
Every script in the resulting tree is LuaJIT-compiled before the builder says
yes, because an overlay that does not compile wastes an attended run.

The mod reads its throwaway candidate from live configs at run time; the builder
also checks, read-only, that the section it is expected to pick exists in the
enabled modlist's configs with a class character_profile, and says so, so a
"prewarm skipped" line in the game log is not the first time anyone hears of it.

    py -3.12 lab/coord/i064_build_overlay.py [--out <dir>] [--base <dir>]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from build_overlay import DEFAULT_MODLIST, DEFAULT_MODS_DIR, read_modlist  # noqa: E402

REPO = HERE.parent.parent
BASE = Path(r"C:\code\GIT\anomaly_alao\lab\coord\overlays\agent-I063-b")
OUT = Path(r"C:\code\GIT\anomaly_alao\lab\coord\overlays\agent-I064-b")
MOD = REPO / "lab" / "mods" / "alao-spawn-prewarm"
ADDED = ("zzz_alao_spawn_prewarm.script",)

# what the mod is expected to pick on this modlist (first usable name of the
# first SQUAD_SOURCES entry)
EXPECT_SQUAD = "stalker_sim_squad_novice"
EXPECT_NPC = "sim_default_stalker_0"


def compiles(path: Path) -> bool:
    """Syntax check only (see i063_build_overlay.compiles for the encoding note)."""
    from lupa import luajit20 as lupa
    raw = path.read_bytes()
    try:
        src = raw.decode("utf-8")
    except UnicodeDecodeError:
        src = raw.decode("cp1251", errors="replace")
    rt = lupa.LuaRuntime()
    chk = rt.eval("function(s) local f = loadstring(s) return f ~= nil end")
    return bool(chk(src))


def shipped_by_any_enabled_mod(name: str, order: list[str]) -> list[str]:
    return [m for m in order
            if (DEFAULT_MODS_DIR / m / "gamedata" / "scripts" / name).is_file()]


def live_config(rel: str, order: list[str]) -> Path | None:
    for m in order:
        p = DEFAULT_MODS_DIR / m / "gamedata" / "configs" / rel
        if p.is_file():
            return p
    return None


def check_candidate(order: list[str]) -> dict:
    """Read-only look at the live configs.  Advisory: never fails the build."""
    out = {"squad": EXPECT_SQUAD, "npc": EXPECT_NPC, "ok": False, "why": ""}
    squads = live_config(r"misc\squad_descr\squad_descr_default_stalkers.ltx", order)
    spawns = live_config(r"creatures\spawn_sections_general.ltx", order)
    profiles = live_config(r"gameplay\npc_profile.xml", order)
    if not (squads and spawns and profiles):
        out["why"] = "configs not found as loose files in the enabled mods (db-packed?)"
        return out
    out["files"] = [str(squads), str(spawns), str(profiles)]
    sq = squads.read_text(encoding="cp1251", errors="replace")
    m = re.search(rf"(?ms)^\[{re.escape(EXPECT_SQUAD)}\][^\n]*\n(.*?)(?=^\[|\Z)", sq)
    if not (m and re.search(rf"(?m)^\s*npc_random\s*=\s*{re.escape(EXPECT_NPC)}\b", m.group(1))):
        out["why"] = f"[{EXPECT_SQUAD}] npc_random does not start with {EXPECT_NPC}"
        return out
    sp = spawns.read_text(encoding="cp1251", errors="replace")
    m = re.search(rf"(?ms)^\[{re.escape(EXPECT_NPC)}\][^\n]*\n(.*?)(?=^\[|\Z)", sp)
    if not (m and re.search(rf"(?m)^\s*character_profile\s*=\s*{re.escape(EXPECT_NPC)}\s*$", m.group(1))):
        out["why"] = f"[{EXPECT_NPC}] character_profile is not {EXPECT_NPC}"
        return out
    pr = profiles.read_text(encoding="cp1251", errors="replace")
    if not re.search(rf'<character id="{re.escape(EXPECT_NPC)}">\s*<class>', pr):
        out["why"] = f"npc_profile {EXPECT_NPC} is not a <class> profile: no random pick, no walk"
        return out
    n_chars = 0
    for p in profiles.parent.glob("character_desc_*.xml"):
        n_chars += p.read_text(encoding="cp1251", errors="replace").count("<specific_character ")
    out.update(ok=True, why="class profile, engine picks at random",
               specific_characters_next_to_profile=n_chars)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--base", type=Path, default=BASE)
    a = ap.parse_args(argv)

    if not a.base.is_dir():
        print(f"missing baseline overlay {a.base}", file=sys.stderr)
        return 2
    order = read_modlist(DEFAULT_MODLIST)
    for name in ADDED:
        if not (MOD / "gamedata" / "scripts" / name).is_file():
            print(f"missing {MOD / 'gamedata' / 'scripts' / name}", file=sys.stderr)
            return 2
        clashes = shipped_by_any_enabled_mod(name, order)
        if clashes:
            print(f"REFUSING: {name} is already shipped by {clashes}", file=sys.stderr)
            return 3

    if a.out.exists():
        shutil.rmtree(a.out)
    shutil.copytree(a.base, a.out)
    scripts = a.out / "gamedata" / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)

    added = []
    for name in ADDED:
        src = MOD / "gamedata" / "scripts" / name
        dst = scripts / name
        assert not dst.exists(), f"{name} already in the baseline overlay"
        shutil.copy2(src, dst)
        added.append({"file": name, "source": str(src),
                      "sha256": hashlib.sha256(dst.read_bytes()).hexdigest(),
                      "shipped_by_enabled_mods": []})

    bad = [p.name for p in sorted(scripts.glob("*.script")) if not compiles(p)]
    if bad:
        print(f"LuaJIT compile failures: {bad}", file=sys.stderr)
        return 4

    cand = check_candidate(order)
    n = sum(1 for _ in scripts.glob("*.script"))
    report = {
        "base": str(a.base),
        "added": added,
        "replaced": [],
        "scripts_total": n,
        "all_compile": True,
        "prewarm_candidate": cand,
    }
    # drop the base overlay's manifest name collision: keep theirs, add ours
    (a.out / "i064_manifest.json").write_text(json.dumps(report, indent=2))
    (a.out / "meta.ini").write_text(
        "[General]\ncategory=\ncomments=I-064 arm: agent-I063-b plus "
        + " + ".join(ADDED) + " (lab/mods/alao-spawn-prewarm). Safe to delete.\n",
        encoding="utf-8")
    print(f"{a.out}: {n} scripts, {len(added)} added, 0 replaced, "
          "all compile under LuaJIT 2.0")
    print(f"prewarm candidate {cand['npc']}: {'ok' if cand['ok'] else 'NOT CONFIRMED'} - {cand['why']}"
          + (f" ({cand['specific_characters_next_to_profile']} specific characters in that mod)"
             if cand.get("ok") else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
