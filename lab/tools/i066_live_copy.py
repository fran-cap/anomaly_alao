"""I-066: who ships a script, in priority order, and which copy the game loads.

    py -3.12 lab/tools/i066_live_copy.py visual_memory_manager.script [more.script ...]

Live copy = top enabled mod in the GAMMA modlist, else loose Anomaly gamedata/scripts,
else the db (extracted/vanilla_db). Read-only, it only stats files.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "coord"))

from build_overlay import DEFAULT_MODLIST, DEFAULT_MODS_DIR, read_modlist  # noqa: E402

LOOSE = Path(r"D:\GOG_Games\Gamma\S.T.A.L.K.E.R. GAMMA\Anomaly\gamedata\scripts")
DB = Path(r"C:\code\GIT\anomaly_alao\extracted\vanilla_db\VANILLA_DB\gamedata\scripts")


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:12]


def main(argv) -> int:
    order = read_modlist(DEFAULT_MODLIST)  # winner first
    for name in argv:
        print(f"== {name}")
        hits = [(i, m) for i, m in enumerate(order)
                if (DEFAULT_MODS_DIR / m / "gamedata" / "scripts" / name).is_file()]
        for n, (i, m) in enumerate(hits):
            p = DEFAULT_MODS_DIR / m / "gamedata" / "scripts" / name
            tag = "LIVE" if n == 0 else "shadowed"
            print(f"  {tag:8s} prio#{i:4d} {sha(p)} {p.stat().st_size:7d}  {m}")
        for label, root in (("loose", LOOSE), ("db", DB)):
            p = root / name
            if p.is_file():
                tag = "LIVE" if not hits and label == "loose" else label
                print(f"  {tag:8s}           {sha(p)} {p.stat().st_size:7d}  {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
