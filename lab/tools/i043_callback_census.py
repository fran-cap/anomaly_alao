"""I-043: who listens to which callback, in the copies the live GAMMA profile loads.

The point of the "live winner" resolution is that a static grep over
`extracted/gamma` answers a different question than the game does. Three layers,
highest priority first:

  1. enabled MO2 mods, in modlist order
  2. loose `Anomaly/gamedata/scripts` (GAMMA patches the base install in place)
  3. the Anomaly db archives, extracted to `extracted/vanilla_db`

That resolution is load-bearing here: `axr_main.script` exists in all three, and
the winner is the LOOSE copy (the one mod that ships it, "420- Tactical Compass",
is disabled), which is the only one whose `make_callback` uses `spairs`. The db
copy still has the old plain-`pairs` dispatcher.

    py -3.12 lab/tools/i043_callback_census.py           # listener counts
    py -3.12 lab/tools/i043_callback_census.py --json out.json

Sanity check before trusting a zero out of this: `--self-check` asserts the two
known positives (axr_main.script is a live winner and holds exactly one
`spairs(` site; actor_on_update has > 100 RegisterScriptCallback sites).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "coord"))
from build_overlay import read_modlist, DEFAULT_MODLIST, DEFAULT_MODS_DIR  # noqa: E402

LOOSE = Path(r"D:\GOG_Games\Gamma\S.T.A.L.K.E.R. GAMMA\Anomaly\gamedata\scripts")
DB = Path(r"C:\code\GIT\anomaly_alao\extracted\vanilla_db\VANILLA_DB\gamedata\scripts")

PER_FRAME = ("actor_on_update", "npc_on_update", "monster_on_update")

FUNC = re.compile(r'^\s*(?:local\s+)?function\s+([A-Za-z_][\w.:]*)\s*\(', re.M)
REG = re.compile(r'RegisterScriptCallback\s*\(\s*"([^"]+)"')
UNREG = re.compile(r'UnregisterScriptCallback\s*\(\s*"([^"]+)"')
SEND = re.compile(r'SendScriptCallback\s*\(\s*"([^"]+)"')
SPAIRS = re.compile(r'\bspairs\s*\(')


def read(p: Path) -> str:
    for enc in ("utf-8-sig", "cp1251"):
        try:
            return p.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
    return p.read_bytes().decode("latin-1")


def live_scripts(modlist=DEFAULT_MODLIST, mods_dir=DEFAULT_MODS_DIR):
    """{lowercase filename: (Path, source tag)} for the copy the game loads."""
    winners: dict[str, tuple[Path, str]] = {}
    for mod in read_modlist(modlist):            # highest priority first
        d = mods_dir / mod / "gamedata" / "scripts"
        if d.is_dir():
            for f in d.rglob("*.script"):
                winners.setdefault(f.name.lower(), (f, "mod:" + mod))
    for f in LOOSE.glob("*.script"):
        winners.setdefault(f.name.lower(), (f, "loose"))
    if DB.is_dir():
        for f in DB.rglob("*.script"):
            winners.setdefault(f.name.lower(), (f, "db"))
    return winners


def enclosing(text: str, pos: int) -> str:
    last = None
    for m in FUNC.finditer(text):
        if m.start() > pos:
            break
        last = m.group(1)
    return last or "<toplevel>"


def census(winners):
    reg, unreg, send = Counter(), Counter(), Counter()
    permanent, churn = Counter(), Counter()
    spairs_sites = {}
    reg_where = defaultdict(Counter)
    for k, (p, src) in sorted(winners.items()):
        t = read(p)
        unreg_names = {m.group(1) for m in UNREG.finditer(t)}
        for m in REG.finditer(t):
            name = m.group(1)
            reg[name] += 1
            fn = enclosing(t, m.start())
            reg_where[name][fn] += 1
            # "permanent" = registered from the module's on_game_start (axr_main
            # auto-runs it for every script) and never unregistered in that file
            if fn in ("on_game_start", "<toplevel>") and name not in unreg_names:
                permanent[name] += 1
            else:
                churn[name] += 1
        for m in UNREG.finditer(t):
            unreg[m.group(1)] += 1
        for m in SEND.finditer(t):
            send[m.group(1)] += 1
        n = len(SPAIRS.findall(t))
        if n:
            spairs_sites[k] = {"sites": n, "source": src}
    return {
        "live_scripts": len(winners),
        "register": dict(reg), "unregister": dict(unreg), "send": dict(send),
        "permanent": dict(permanent), "churn": dict(churn),
        "register_where": {k: dict(v) for k, v in reg_where.items()},
        "spairs_sites": spairs_sites,
        "spairs_total": sum(v["sites"] for v in spairs_sites.values()),
    }


def self_check(winners, c):
    problems = []
    if winners.get("axr_main.script", (None, None))[1] != "loose":
        problems.append("axr_main.script is not resolving to the loose GAMMA copy")
    if c["spairs_sites"].get("axr_main.script", {}).get("sites") != 1:
        problems.append("axr_main.script should hold exactly 1 spairs( site")
    if c["register"].get("actor_on_update", 0) < 100:
        problems.append("actor_on_update should have > 100 RegisterScriptCallback sites")
    return problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", type=Path)
    ap.add_argument("--top", type=int, default=20)
    a = ap.parse_args(argv)

    winners = live_scripts()
    c = census(winners)
    problems = self_check(winners, c)

    print(f"live winner scripts: {c['live_scripts']}")
    print(f"RegisterScriptCallback sites: {sum(c['register'].values())}")
    print(f"spairs( sites: {c['spairs_total']} across {len(c['spairs_sites'])} files\n")
    print(f"{'callback':34}{'reg':>5}{'perm':>6}{'churn':>7}{'unreg':>7}{'send':>6}")
    for name, n in Counter(c["register"]).most_common(a.top):
        print(f"{name:34}{n:5d}{c['permanent'].get(name,0):6d}"
              f"{c['churn'].get(name,0):7d}{c['unregister'].get(name,0):7d}{c['send'].get(name,0):6d}")
    print("\nper-frame dispatch (one SendScriptCallback site each):")
    for name in PER_FRAME:
        print(f"  {name:22} K = {c['permanent'].get(name,0)} permanent .. "
              f"{c['register'].get(name,0)} static sites")

    if problems:
        print("\nSELF-CHECK FAILED - do not quote these numbers:")
        for p in problems:
            print("  ! " + p)
    else:
        print("\nself-check ok (axr_main.script live + 1 spairs site, actor_on_update > 100)")

    if a.json:
        a.json.write_text(json.dumps(c, indent=1), encoding="utf-8")
        print(f"wrote {a.json}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
