"""Build the I-067 variant overlay: agent-I057-b plus alao-prewarm v1.4.

Same recipe as `i063_build_overlay.py` (whose helpers this imports), with three
added files instead of two and a different default output, so the I-063 overlay
- this round's shared BASELINE arm - is never touched:

    gamedata/scripts/zzz_alao_prewarm.script                   v1.3 body, version string 1.4
    gamedata/scripts/modxml_zzz_alao_prewarm_tutorial.script   unchanged
    gamedata/scripts/zzz_alao_prewarm_fdda.script              NEW in v1.4 (I-067)

Nothing is replaced; every name is checked against the enabled modlist; every
script in the result is LuaJIT-compiled before the builder says yes.

    py -3.12 lab/coord/i067_build_overlay.py [--out <dir>] [--mod <alao-prewarm dir>] [--base <dir>]
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

from build_overlay import DEFAULT_MODLIST, read_modlist  # noqa: E402
from i063_build_overlay import BASE, compiles, shipped_by_any_enabled_mod  # noqa: E402

REPO = HERE.parent.parent
OUT = Path(r"C:\code\GIT\anomaly_alao\lab\coord\overlays\agent-I067-b")
PROTECTED = Path(r"C:\code\GIT\anomaly_alao\lab\coord\overlays\agent-I063-b")
MOD = REPO / "lab" / "mods" / "alao-prewarm"
ADDED = ("zzz_alao_prewarm.script",
         "modxml_zzz_alao_prewarm_tutorial.script",
         "zzz_alao_prewarm_fdda.script")
WANT_VERSION = 'local VERSION = "1.4"'


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--base", type=Path, default=BASE)
    ap.add_argument("--mod", type=Path, default=MOD)
    a = ap.parse_args(argv)

    if a.out.resolve() in (PROTECTED.resolve(), a.base.resolve()):
        print(f"REFUSING to write over {a.out}: it is an arm of somebody's comparison", file=sys.stderr)
        return 5
    if not a.base.is_dir():
        print(f"missing baseline overlay {a.base}", file=sys.stderr)
        return 2
    src_dir = a.mod / "gamedata" / "scripts"
    order = read_modlist(DEFAULT_MODLIST)
    for name in ADDED:
        if not (src_dir / name).is_file():
            print(f"missing {src_dir / name}", file=sys.stderr)
            return 2
        clashes = shipped_by_any_enabled_mod(name, order)
        if clashes:
            print(f"REFUSING: {name} is already shipped by {clashes}", file=sys.stderr)
            return 3
    for name in ("zzz_alao_prewarm.script", "zzz_alao_prewarm_fdda.script"):
        if WANT_VERSION not in (src_dir / name).read_text(encoding="utf-8"):
            print(f"{name} is not v1.4", file=sys.stderr)
            return 6

    if a.out.exists():
        shutil.rmtree(a.out)
    shutil.copytree(a.base, a.out)
    scripts = a.out / "gamedata" / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)

    added = []
    for name in ADDED:
        dst = scripts / name
        assert not dst.exists(), f"{name} already in the baseline overlay"
        shutil.copy2(src_dir / name, dst)
        added.append({"file": name, "source": str(src_dir / name),
                      "sha256": hashlib.sha256(dst.read_bytes()).hexdigest(),
                      "shipped_by_enabled_mods": []})

    bad = [p.name for p in sorted(scripts.glob("*.script")) if not compiles(p)]
    if bad:
        print(f"LuaJIT compile failures: {bad}", file=sys.stderr)
        return 4

    n = sum(1 for _ in scripts.glob("*.script"))
    (a.out / "i067_manifest.json").write_text(json.dumps({
        "base": str(a.base), "added": added, "replaced": [],
        "scripts_total": n, "all_compile": True, "prewarm_version": "1.4"}, indent=2))
    (a.out / "meta.ini").write_text(
        "[General]\ncategory=\ncomments=I-067 arm: agent-I057-b plus "
        + " + ".join(ADDED) + " (lab/mods/alao-prewarm v1.4). Safe to delete.\n",
        encoding="utf-8")
    print(f"{a.out}: {n} scripts, {len(added)} added, 0 replaced, "
          "all compile under LuaJIT 2.0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
