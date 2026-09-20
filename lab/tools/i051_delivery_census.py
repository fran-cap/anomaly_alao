"""I-051: the two census questions that decide how to deliver the I-043 patch.

1. **Can a monkey-patch work?** A `zzz_*.script` that reassigns
   `axr_main.make_callback` / `callback_set` / `callback_unset` / `callback_add`
   at `on_game_start` only catches callers that look the function up through the
   module table *at call time*. Anything that did `local mc = axr_main.make_callback`
   at load time keeps the old one. So: find every reference to those four names
   in the copies the game actually loads, and flag the ones that bind them to a
   local / a field.

2. **How bad is a full-file replacement?** `axr_main.script` exists in the db, in
   the loose `Anomaly/gamedata/scripts` tree and possibly in mods. A mod that
   ships its own copy would be overwritten by (or would overwrite) a replacement
   mod. Count them in the live modlist AND in `extracted/gamma` (every mod, not
   only the enabled ones), because a user's modlist is not this machine's.

    py -3.12 lab/tools/i051_delivery_census.py
    py -3.12 lab/tools/i051_delivery_census.py --json out.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[0]))
from i043_callback_census import live_scripts, read, LOOSE, DB  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "coord"))
from build_overlay import read_modlist, DEFAULT_MODLIST, DEFAULT_MODS_DIR  # noqa: E402

EXTRACTED_GAMMA = Path(r"C:\code\GIT\anomaly_alao\extracted\gamma")

PATCHED = ("make_callback", "callback_set", "callback_unset", "callback_add")

# `axr_main.make_callback` used as a value rather than called: assigned to a
# local/global/field, passed as an argument, or stored in a table.
REF = re.compile(r"\baxr_main\s*\.\s*(" + "|".join(PATCHED) + r")\b")
CALL_AFTER = re.compile(r"^\s*\(")


def classify(line: str, m: re.Match) -> str:
    """called | bound (the dangerous one) | other"""
    tail = line[m.end():]
    if CALL_AFTER.match(tail):
        return "called"
    head = line[: m.start()]
    if re.search(r"(=|,|\(|\[)\s*$", head):
        return "bound"
    return "other"


def census_refs(winners) -> dict:
    hits = []
    for name, (path, tag) in sorted(winners.items()):
        if name == "axr_main.script":
            continue
        try:
            src = read(path)
        except OSError:
            continue
        for i, line in enumerate(src.splitlines(), 1):
            for m in REF.finditer(line):
                hits.append({"file": name, "tag": tag, "line": i,
                             "func": m.group(1), "kind": classify(line, m),
                             "text": line.strip()[:160]})
    return {"hits": hits,
            "by_kind": {k: sum(1 for h in hits if h["kind"] == k)
                        for k in ("called", "bound", "other")}}


def census_axr_main_copies() -> dict:
    live_mods, all_mods = [], []
    for mod in read_modlist(DEFAULT_MODLIST):
        if (DEFAULT_MODS_DIR / mod / "gamedata" / "scripts" / "axr_main.script").is_file():
            live_mods.append(mod)
    if EXTRACTED_GAMMA.is_dir():
        for mod in sorted(p.name for p in EXTRACTED_GAMMA.iterdir() if p.is_dir()):
            if list((EXTRACTED_GAMMA / mod).rglob("axr_main.script")):
                all_mods.append(mod)
    return {
        "enabled_mods_shipping_axr_main": live_mods,
        "extracted_gamma_mods_shipping_axr_main": all_mods,
        "loose_copy": str(LOOSE / "axr_main.script"),
        "loose_exists": (LOOSE / "axr_main.script").is_file(),
        "db_copy_exists": (DB / "axr_main.script").is_file(),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json")
    a = ap.parse_args()

    winners = live_scripts()
    refs = census_refs(winners)
    copies = census_axr_main_copies()

    print(f"live winners scanned: {len(winners)}")
    print(f"references to axr_main.<patched fn>: {refs['by_kind']}")
    for h in refs["hits"]:
        print(f"  [{h['kind']:7}] {h['file']}:{h['line']} ({h['tag']})  {h['text']}")
    print()
    print("axr_main.script copies")
    print(f"  loose            : {copies['loose_exists']}")
    print(f"  db               : {copies['db_copy_exists']}")
    print(f"  ENABLED mods     : {len(copies['enabled_mods_shipping_axr_main'])} "
          f"{copies['enabled_mods_shipping_axr_main']}")
    print(f"  extracted/gamma  : {len(copies['extracted_gamma_mods_shipping_axr_main'])} "
          f"{copies['extracted_gamma_mods_shipping_axr_main']}")

    if a.json:
        Path(a.json).write_text(json.dumps({"refs": refs, "copies": copies}, indent=2),
                                encoding="utf-8")
        print(f"\nwrote {a.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
