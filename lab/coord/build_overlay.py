#!/usr/bin/env python3
"""Turn an ALAO --fix'ed corpus tree into ONE overlay mod the game can load.

Why this exists: an in-game A/B of ALAO's rewrites needs the rewritten scripts
to actually be the ones the engine loads.  Many GAMMA mods ship the same
script name (there are a dozen ui_inventory.script's); MO2 priority decides
which one wins.  So we can't just dump every rewritten file into a folder - we
have to walk the modlist in priority order, find the copy that wins in the live
profile, and take THAT one only if ALAO rewrote it.  Anything else would put a
rewrite of a losing mod's file on top of the winner's original: wrong code.

    py -3.12 lab/coord/build_overlay.py --work <fixed tree in MO2 layout> --out <overlay dir>
        [--modlist <modlist.txt>]   default: the live G.A.M.M.A profile's (read-only)
        [--manifest-only]           just print what would be taken

<work> is what `tools/corpus_run.py --keep-work` leaves in extracted/_work/<run>/work,
or any copy of extracted/gamma you ran `stalker_lua_lint.py --fix` on yourself.
Rewritten files are recognised by their sibling `.alao-bak`.

<out> ends up as  <out>/gamedata/scripts/<...>.script  plus overlay_manifest.json,
ready to be copied to GAMMA/mods/aalo-rewrite-<x>/ by fps_runner.py (which is the
only thing that writes into the install, and only under that prefix).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

DEFAULT_MODLIST = Path(r"D:\GOG_Games\Gamma\S.T.A.L.K.E.R. GAMMA\GAMMA\profiles\G.A.M.M.A\modlist.txt")
DEFAULT_MODS_DIR = Path(r"D:\GOG_Games\Gamma\S.T.A.L.K.E.R. GAMMA\GAMMA\mods")
BAK = ".alao-bak"


def read_modlist(path: Path) -> list[str]:
    """Enabled mod names, highest priority first (that's the file order)."""
    out = []
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.rstrip("\r")
        if not line or line.startswith("#"):
            continue
        prefix, name = line[0], line[1:]
        if prefix == "+" and not name.endswith("_separator"):
            out.append(name)
    return out


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def build(work: Path, out: Path, modlist: Path, manifest_only: bool = False) -> dict:
    order = read_modlist(modlist)
    rank = {name: i for i, name in enumerate(order)}
    present = {p.name for p in work.iterdir() if p.is_dir()}
    missing = [m for m in present if m not in rank]

    # every gamedata-relative path that any mod in the tree ships
    shipped: dict[str, list[str]] = {}   # rel -> [mod names in tree]
    for mod in present:
        gd = work / mod / "gamedata"
        if not gd.is_dir():
            continue
        for f in gd.rglob("*"):
            if not f.is_file() or f.name.endswith(BAK):
                continue
            rel = f.relative_to(gd).as_posix()
            shipped.setdefault(rel, []).append(mod)

    taken, skipped_losers, unrewritten_winner = [], [], 0
    for rel, mods in sorted(shipped.items()):
        ranked = sorted((m for m in mods if m in rank), key=lambda m: rank[m])
        if not ranked:
            continue
        winner = ranked[0]
        src = work / winner / "gamedata" / rel
        if (src.parent / (src.name + BAK)).is_file():
            taken.append({"rel": rel, "mod": winner, "sha256": sha256(src)})
        else:
            unrewritten_winner += 1
        for m in ranked[1:]:
            lf = work / m / "gamedata" / rel
            if (lf.parent / (lf.name + BAK)).is_file():
                skipped_losers.append({"rel": rel, "mod": m, "winner": winner})

    manifest = {
        "work": str(work), "modlist": str(modlist), "enabled_mods_in_modlist": len(order),
        "mods_in_tree": len(present), "mods_not_in_modlist": sorted(missing),
        "files_taken": len(taken), "rewritten_but_shadowed": len(skipped_losers),
        "winners_not_rewritten": unrewritten_winner,
        "taken": taken, "shadowed": skipped_losers,
    }
    if manifest_only:
        return manifest

    if out.exists():
        shutil.rmtree(out)
    for t in taken:
        dst = out / "gamedata" / t["rel"]
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(work / t["mod"] / "gamedata" / t["rel"], dst)
    (out / "overlay_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    # MO2 shows this in the mod's Notes tab; harmless otherwise
    (out / "meta.ini").write_text(
        "[General]\ncategory=\ncomments=ALAO rewrite overlay (lab/coord/build_overlay.py). "
        "Safe to delete.\n", encoding="utf-8")
    return manifest


def build_bottom(work: Path, out: Path, modlist: Path, mods_dir: Path, manifest_only: bool = False) -> dict:
    """Overlay meant for the BOTTOM of the load order: rewritten vanilla scripts.

    The tree is a single pseudo-mod (VANILLA_DB or VANILLA_SCRIPTS) that is not
    in the modlist. At lowest priority a file is live only if NO enabled mod
    ships the same gamedata-relative path, so scan the real mods dir (read-only)
    for what the enabled mods ship and take rewritten files outside that set.
    """
    order = read_modlist(modlist)
    shipped: set[str] = set()
    for mod in order:
        gd = mods_dir / mod / "gamedata"
        if gd.is_dir():
            for f in gd.rglob("*.script"):
                shipped.add(f.relative_to(gd).as_posix().lower())
    # GAMMA also patches the game in place: loose files in Anomaly/gamedata beat the .db archives,
    # so a db script shadowed by one of those is NOT live either (bind_monster.script bit us, gen-3)
    loose = mods_dir.parent.parent / "Anomaly" / "gamedata"
    if loose.is_dir():
        for f in loose.rglob("*.script"):
            shipped.add(f.relative_to(loose).as_posix().lower())
    trees = [p for p in work.iterdir() if p.is_dir() and (p / "gamedata").is_dir()]
    taken, shadowed, untouched = [], [], 0
    for tree in trees:
        gd = tree / "gamedata"
        for f in gd.rglob("*"):
            if not f.is_file() or f.name.endswith(BAK):
                continue
            rel = f.relative_to(gd).as_posix()
            rewritten = (f.parent / (f.name + BAK)).is_file()
            if not rewritten:
                untouched += 1
                continue
            if rel.lower() in shipped:
                shadowed.append({"rel": rel, "mod": tree.name})
            else:
                taken.append({"rel": rel, "mod": tree.name, "sha256": sha256(f)})
    manifest = {
        "work": str(work), "modlist": str(modlist), "mods_dir": str(mods_dir), "position": "bottom",
        "enabled_mods_in_modlist": len(order), "files_shipped_by_enabled_mods": len(shipped),
        "files_taken": len(taken), "rewritten_but_shadowed": len(shadowed), "winners_not_rewritten": untouched,
        "taken": taken, "shadowed": shadowed, "mods_not_in_modlist": [],
    }
    if manifest_only:
        return manifest
    if out.exists():
        shutil.rmtree(out)
    for t in taken:
        dst = out / "gamedata" / t["rel"]
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(work / t["mod"] / "gamedata" / t["rel"], dst)
    (out / "overlay_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (out / "meta.ini").write_text(
        "[General]\ncategory=\ncomments=ALAO rewritten vanilla scripts overlay (lab/coord/build_overlay.py --bottom). "
        "Load it LOWEST. Safe to delete.\n", encoding="utf-8")
    return manifest


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--work", type=Path, required=True)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--modlist", type=Path, default=DEFAULT_MODLIST)
    ap.add_argument("--manifest-only", action="store_true")
    ap.add_argument("--bottom", action="store_true",
                    help="overlay for the bottom of the load order (rewritten vanilla scripts): take rewritten files no enabled mod ships")
    ap.add_argument("--mods-dir", type=Path, default=DEFAULT_MODS_DIR)
    a = ap.parse_args(argv)
    if not a.work.is_dir():
        print(f"no such work tree: {a.work}", file=sys.stderr)
        return 2
    if not a.manifest_only and a.out is None:
        ap.error("--out is required unless --manifest-only")
    if a.bottom:
        m = build_bottom(a.work.resolve(), a.out.resolve() if a.out else Path("."), a.modlist, a.mods_dir, a.manifest_only)
    else:
        m = build(a.work.resolve(), a.out.resolve() if a.out else Path("."), a.modlist, a.manifest_only)
    print(f"overlay ({m.get('position', 'top')}): {m['files_taken']} rewritten winners taken, "
          f"{m['rewritten_but_shadowed']} rewrites shadowed by a higher-priority mod, "
          f"{m['winners_not_rewritten']} winners untouched, "
          f"{len(m['mods_not_in_modlist'])} tree mods absent from modlist")
    if m["mods_not_in_modlist"]:
        print("  not in modlist: " + ", ".join(m["mods_not_in_modlist"][:10]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
