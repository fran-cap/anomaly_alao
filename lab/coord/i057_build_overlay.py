"""Build the I-057 variant overlay: gen4-all-b plus the I-057 patched files.

gen4-all-b is the baseline arm (full ALAO + the four gen-4 patches, already
filtered to the copies the live profile actually loads), so the variant is just
that tree with five files replaced:

    demonized_ledge_grabbing.script   track A, on-demand scan
    fluid_aim.script                  track B
    actor_effects.script              track B (fog latch)
    battery_warning.script            track B
    light_gem_mcm.script              track B

Two of the five (battery_warning, light_gem_mcm) are not in gen4-all-b because
ALAO rewrote nothing in them; those are patched from the live winning mod copy,
which this script re-checks against the modlist before it writes them.  Every
file is LuaJIT-compiled before it lands.

    py -3.12 lab/coord/i057_build_overlay.py [--out <dir>]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import i057_bundle_patch as B  # noqa: E402
import i057_ledge_ondemand_patch as L  # noqa: E402
from build_overlay import DEFAULT_MODLIST, DEFAULT_MODS_DIR, read_modlist  # noqa: E402

BASE = Path(r"C:\code\GIT\anomaly_alao\lab\coord\overlays\gen4-all-b")
OUT = Path(r"C:\code\GIT\anomaly_alao\lab\coord\overlays\agent-I057-b")
LEDGE = "demonized_ledge_grabbing.script"


def live_winner(name: str, order: list[str]) -> Path | None:
    for mod in order:
        p = DEFAULT_MODS_DIR / mod / "gamedata" / "scripts" / name
        if p.is_file():
            return p
    return None


def compiles(path: Path) -> bool:
    from lupa import luajit20 as lupa
    rt = lupa.LuaRuntime()
    chk = rt.eval('function(s) local f = loadstring(s) return f ~= nil end')
    return bool(chk(path.read_text(encoding="utf-8")))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=OUT)
    a = ap.parse_args(argv)
    order = read_modlist(DEFAULT_MODLIST)

    if a.out.exists():
        shutil.rmtree(a.out)
    shutil.copytree(BASE, a.out)
    scripts = a.out / "gamedata" / "scripts"

    L.main([str(scripts / LEDGE)])
    B.main([str(a.out)])

    report = {"base": str(BASE), "patched": []}
    for name in [LEDGE] + list(B.PATCHERS):
        p = scripts / name
        assert p.is_file(), name
        assert compiles(p), f"{name} does not compile under LuaJIT 2.0"
        src = L.GEN4 if name == LEDGE else B.source_for(name, False)
        w = live_winner(name, order)
        report["patched"].append({
            "file": name,
            "source": str(src),
            "live_winner_mod": w.parent.parent.parent.name if w else "(db)",
            "in_gen4_base": (BASE / "gamedata" / "scripts" / name).is_file(),
            "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
        })
        print(f"{name:38s} <- {src.parent.parent.parent.name:45s} "
              f"live winner: {report['patched'][-1]['live_winner_mod']}")

    (a.out / "i057_manifest.json").write_text(json.dumps(report, indent=2))
    n = sum(1 for _ in scripts.glob("*.script"))
    print(f"\n{a.out}: {n} scripts, 5 patched, all compile")
    return 0


if __name__ == "__main__":
    sys.exit(main())
