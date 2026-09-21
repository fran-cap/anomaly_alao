"""Build the I-066 variant overlay: agent-I063-b plus the stealth MCM cache.

The baseline arm of the I-066 comparison is `agent-I063-b` itself (full ALAO +
the four gen-4 patches + I-057 + alao-prewarm), so the variant is that tree with
exactly one file added:

    gamedata/scripts/zzz_alao_vmm_cache.script    lab/mods/alao-vmm-cache

Nothing is replaced.  The name is new - no mod in the enabled modlist ships a
file called that, and the builder checks - so the overlay cannot shadow anybody.
It also checks the two things the patch leans on: that the base overlay (or the
modlist under it) still has a `stealth_mcm.script` whose get_config is the shape
we cache, and that nobody in the tree binds `stealth_mcm.get_config` to a local
(a cached reference would walk straight past a monkey patch).  Every script in
the copied tree is LuaJIT-compiled, because an overlay that does not compile
wastes a run.

    py -3.12 lab/coord/i066_build_overlay.py [--out <dir>]
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
OUT = Path(r"C:\code\GIT\anomaly_alao\lab\coord\overlays\agent-I066-b")
MOD = REPO / "lab" / "mods" / "alao-vmm-cache"
ADDED = ("zzz_alao_vmm_cache.script",)

# `local x = stealth_mcm.get_config` anywhere = a reference the patch cannot reach
LOCAL_BIND = re.compile(r"=\s*stealth_mcm\.get_config\s*(?:$|[^(\s])", re.M)


def read_text(path: Path) -> str:
    raw = path.read_bytes()
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1251", errors="replace")


def compiles(path: Path) -> bool:
    """Syntax check only (see i063_build_overlay.compiles for the encoding note)."""
    from lupa import luajit20 as lupa
    rt = lupa.LuaRuntime()
    chk = rt.eval("function(s) local f = loadstring(s) return f ~= nil end")
    return bool(chk(read_text(path)))


def shipped_by_any_enabled_mod(name: str, order: list[str]) -> list[str]:
    return [m for m in order
            if (DEFAULT_MODS_DIR / m / "gamedata" / "scripts" / name).is_file()]


def live_copy(name: str, overlay_scripts: Path, order: list[str]) -> Path | None:
    """The overlay sits on top of the modlist, so it wins when it ships the file."""
    p = overlay_scripts / name
    if p.is_file():
        return p
    for m in order:
        p = DEFAULT_MODS_DIR / m / "gamedata" / "scripts" / name
        if p.is_file():
            return p
    return None


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

    base_scripts = a.base / "gamedata" / "scripts"
    targets = {}
    for name in ("stealth_mcm.script", "visual_memory_manager.script"):
        p = live_copy(name, base_scripts, order)
        if p is None:
            print(f"REFUSING: no live {name}; the patch would have nothing to patch", file=sys.stderr)
            return 5
        targets[name] = p
    sm = read_text(targets["stealth_mcm.script"])
    if 'ui_mcm.get("stealth/"..key)' not in re.sub(r"\s+", "", sm):
        print("REFUSING: live stealth_mcm.get_config is not the shape this patch caches", file=sys.stderr)
        return 5
    vm = read_text(targets["visual_memory_manager.script"])
    reads = len(re.findall(r"stealth_mcm\.get_config\(", vm))

    binders = []
    seen = set()
    for root in [base_scripts] + [DEFAULT_MODS_DIR / m / "gamedata" / "scripts" for m in order]:
        if not root.is_dir():
            continue
        for p in root.glob("*.script"):
            if p.name in seen:
                continue            # shadowed by something above it
            seen.add(p.name)
            try:
                txt = read_text(p)
            except OSError:
                continue
            if "stealth_mcm" in txt and LOCAL_BIND.search(txt):
                binders.append(str(p))
    if binders:
        print(f"REFUSING: these live scripts hold stealth_mcm.get_config in a variable: {binders}",
              file=sys.stderr)
        return 6

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

    n = sum(1 for _ in scripts.glob("*.script"))
    report = {
        "base": str(a.base),
        "added": added,
        "replaced": [],
        "scripts_total": n,
        "all_compile": True,
        "patch_targets": {k: str(v) for k, v in targets.items()},
        "get_config_sites_in_live_vmm": reads,
        "scripts_holding_get_config_in_a_variable": [],
    }
    (a.out / "i066_manifest.json").write_text(json.dumps(report, indent=2))
    (a.out / "meta.ini").write_text(
        "[General]\ncategory=\ncomments=I-066 arm: agent-I063-b plus "
        + " + ".join(ADDED) + " (lab/mods/alao-vmm-cache). Safe to delete.\n",
        encoding="utf-8")
    print(f"{a.out}: {n} scripts, {len(added)} added, 0 replaced, all compile under LuaJIT 2.0")
    print(f"  live stealth_mcm:  {targets['stealth_mcm.script']}")
    print(f"  live vmm:          {targets['visual_memory_manager.script']}  ({reads} get_config sites)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
