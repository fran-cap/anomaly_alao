"""Build the I-063 variant overlay: agent-I057-b plus the prewarm mod's script.

The baseline arm of the I-063 comparison is `agent-I057-b` itself (full ALAO +
the four gen-4 patches + I-057), so the variant is that tree with exactly one
file added:

    gamedata/scripts/zzz_alao_prewarm.script    lab/mods/alao-prewarm

Nothing is replaced.  The name is new - no mod in the enabled modlist ships a
file called that, and the builder checks - so the overlay cannot shadow anybody.
The file is LuaJIT-compiled before it lands, and so is every other script in the
copied tree, because an overlay that does not compile wastes an attended run.

    py -3.12 lab/coord/i063_build_overlay.py [--out <dir>]
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

from build_overlay import DEFAULT_MODLIST, DEFAULT_MODS_DIR, read_modlist  # noqa: E402

REPO = HERE.parent.parent
BASE = Path(r"C:\code\GIT\anomaly_alao\lab\coord\overlays\agent-I057-b")
OUT = Path(r"C:\code\GIT\anomaly_alao\lab\coord\overlays\agent-I063-b")
MOD = REPO / "lab" / "mods" / "alao-prewarm"
ADDED = "zzz_alao_prewarm.script"


def compiles(path: Path) -> bool:
    """Syntax check only.

    Many GAMMA scripts are CP1251 with Russian comments, so the text handed to
    LuaJIT may not be byte-identical to the file (lupa re-encodes as UTF-8).
    That changes bytes inside comments and string literals and nothing else, so
    it cannot turn a valid chunk into an invalid one or the other way round.
    """
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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--base", type=Path, default=BASE)
    a = ap.parse_args(argv)

    src = MOD / "gamedata" / "scripts" / ADDED
    if not src.is_file():
        print(f"missing {src}", file=sys.stderr)
        return 2
    if not a.base.is_dir():
        print(f"missing baseline overlay {a.base}", file=sys.stderr)
        return 2

    order = read_modlist(DEFAULT_MODLIST)
    clashes = shipped_by_any_enabled_mod(ADDED, order)
    if clashes:
        print(f"REFUSING: {ADDED} is already shipped by {clashes}", file=sys.stderr)
        return 3

    if a.out.exists():
        shutil.rmtree(a.out)
    shutil.copytree(a.base, a.out)
    scripts = a.out / "gamedata" / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    dst = scripts / ADDED
    assert not dst.exists(), f"{ADDED} already in the baseline overlay"
    shutil.copy2(src, dst)

    bad = [p.name for p in sorted(scripts.glob("*.script")) if not compiles(p)]
    if bad:
        print(f"LuaJIT compile failures: {bad}", file=sys.stderr)
        return 4

    n = sum(1 for _ in scripts.glob("*.script"))
    report = {
        "base": str(a.base),
        "added": [{"file": ADDED, "source": str(src),
                   "sha256": hashlib.sha256(dst.read_bytes()).hexdigest(),
                   "shipped_by_enabled_mods": clashes}],
        "replaced": [],
        "scripts_total": n,
        "all_compile": True,
    }
    (a.out / "i063_manifest.json").write_text(json.dumps(report, indent=2))
    (a.out / "meta.ini").write_text(
        "[General]\ncategory=\ncomments=I-063 arm: agent-I057-b plus "
        "zzz_alao_prewarm.script (lab/mods/alao-prewarm). Safe to delete.\n",
        encoding="utf-8")
    print(f"{a.out}: {n} scripts, 1 added, 0 replaced, all compile under LuaJIT 2.0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
