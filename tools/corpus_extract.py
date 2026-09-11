#!/usr/bin/env python3
"""
Build a scratch corpus of Anomaly/GAMMA scripts for ALAO regression runs.

The game install is read-only, so we never point ALAO at it directly - we copy
the scripts out into `extracted/<name>/` (gitignored) keeping the MO2 layout
`<Mod>/gamedata/scripts/...` so ALAO's normal discovery works unchanged.

  py -3.12 tools/corpus_extract.py --corpus gamma
  py -3.12 tools/corpus_extract.py --corpus vanilla
  py -3.12 tools/corpus_extract.py --corpus gamma --profile "GAMMA Custom" --out D:\\scratch\\g

`--corpus gamma` walks GAMMA/profiles/<profile>/modlist.txt and only copies mods
that are ENABLED there (lines starting with '+'; '-' is disabled, '*' is a
separator).  `--corpus vanilla` copies Anomaly/gamedata/scripts as a single
pseudo-mod named VANILLA_SCRIPTS.

Writes corpus_manifest.json next to the copied tree: per-file sha256, byte size
and detected encoding, plus mod/file/byte totals.
"""

import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from models import detect_file_encoding  # noqa: E402

DEFAULT_INSTALL = Path(r"D:\GOG_Games\Gamma\S.T.A.L.K.E.R. GAMMA")
DEFAULT_PROFILE = "G.A.M.M.A"
SCRIPT_EXTS = (".script", ".lua")

# modlist.txt line prefixes written by MO2 (same rules as the lab's aalo/mo2.py,
# reimplemented here so this tool has no dependency on the lab checkout).
ENABLED, DISABLED, SEPARATOR = "+", "-", "*"


def parse_modlist(path: Path):
    """Return (enabled, disabled, separators) mod-name lists from modlist.txt."""
    text = path.read_text(encoding="utf-8-sig")
    enabled, disabled, separators = [], [], []
    for raw in text.splitlines():
        line = raw.rstrip("\r")
        if not line.strip() or line.startswith("#"):
            continue
        prefix, name = line[0], line[1:]
        if prefix not in (ENABLED, DISABLED, SEPARATOR):
            prefix, name = ENABLED, line
        # MO2 marks separators with '*' or a _separator suffix
        if prefix == SEPARATOR or name.endswith("_separator"):
            separators.append(name)
        elif prefix == ENABLED:
            enabled.append(name)
        else:
            disabled.append(name)
    return enabled, disabled, separators


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def find_scripts(scripts_dir: Path):
    out = set()
    for ext in SCRIPT_EXTS:
        out.update(scripts_dir.rglob("*" + ext))
    return sorted(p for p in out if p.is_file())


def copy_mod(src_scripts: Path, dest_root: Path, mod_name: str):
    """Copy one mod's scripts tree, preserving <Mod>/gamedata/scripts/<rel>."""
    copied = []
    dest_scripts = dest_root / mod_name / "gamedata" / "scripts"
    for src in find_scripts(src_scripts):
        rel = src.relative_to(src_scripts)
        dst = dest_scripts / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied.append((mod_name, dst, src))
    return copied


def build_gamma(install: Path, profile: str, dest: Path, include_disabled: bool):
    gamma = install / "GAMMA"
    modlist = gamma / "profiles" / profile / "modlist.txt"
    if not modlist.is_file():
        raise SystemExit(f"modlist.txt not found: {modlist}")
    enabled, disabled, separators = parse_modlist(modlist)
    wanted = enabled + (disabled if include_disabled else [])
    mods_dir = gamma / "mods"

    copied, mods_with_scripts, missing = [], [], []
    for name in wanted:
        scripts_dir = mods_dir / name / "gamedata" / "scripts"
        if not scripts_dir.is_dir():
            if not (mods_dir / name).is_dir():
                missing.append(name)
            continue
        files = copy_mod(scripts_dir, dest, name)
        if files:
            copied.extend(files)
            mods_with_scripts.append(name)

    source_info = {
        "install": str(install),
        "profile": profile,
        "modlist": str(modlist),
        "mods_enabled": len(enabled),
        "mods_disabled": len(disabled),
        "separators": len(separators),
        "mods_missing_on_disk": missing,
        "include_disabled": include_disabled,
    }
    return copied, mods_with_scripts, source_info


def build_vanilla(install: Path, dest: Path):
    scripts_dir = install / "Anomaly" / "gamedata" / "scripts"
    if not scripts_dir.is_dir():
        raise SystemExit(f"vanilla scripts not found: {scripts_dir}")
    copied = copy_mod(scripts_dir, dest, "VANILLA_SCRIPTS")
    return copied, ["VANILLA_SCRIPTS"], {"install": str(install), "source": str(scripts_dir)}


def write_manifest(dest: Path, corpus: str, copied, mods, source_info, started):
    files, encodings, total_bytes = [], {}, 0
    for mod_name, dst, src in copied:
        size = dst.stat().st_size
        enc = detect_file_encoding(dst)
        encodings[enc] = encodings.get(enc, 0) + 1
        total_bytes += size
        files.append({
            "mod": mod_name,
            "path": str(dst.relative_to(dest)).replace("\\", "/"),
            "source": str(src),
            "bytes": size,
            "sha256": sha256_of(dst),
            "encoding": enc,
        })
    manifest = {
        "corpus": corpus,
        "generated": started,
        "root": str(dest),
        "mod_count": len(mods),
        "file_count": len(files),
        "total_bytes": total_bytes,
        "encodings": dict(sorted(encodings.items(), key=lambda kv: -kv[1])),
        "source": source_info,
        "files": files,
    }
    out = dest / "corpus_manifest.json"
    out.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest, out


def main():
    ap = argparse.ArgumentParser(description="Extract a scratch script corpus from the game install.")
    ap.add_argument("--corpus", required=True, choices=("gamma", "vanilla"),
                    help="gamma = enabled MO2 mods; vanilla = Anomaly/gamedata/scripts")
    ap.add_argument("--install", type=Path, default=DEFAULT_INSTALL,
                    help=f"game install root (default: {DEFAULT_INSTALL})")
    ap.add_argument("--profile", default=DEFAULT_PROFILE, help="MO2 profile name (gamma only)")
    ap.add_argument("--out", type=Path, default=None,
                    help="destination dir (default: <repo>/extracted/<corpus>)")
    ap.add_argument("--name", default=None, help="folder name under extracted/ (default: the corpus name)")
    ap.add_argument("--include-disabled", action="store_true",
                    help="gamma only: also copy mods disabled in the profile")
    ap.add_argument("--force", action="store_true", help="wipe the destination if it already exists")
    args = ap.parse_args()

    dest = args.out or (REPO_ROOT / "extracted" / (args.name or args.corpus))
    dest = dest.resolve()
    install = args.install.resolve()

    # hard guard: never write anything inside the read-only install
    try:
        dest.relative_to(install)
        raise SystemExit(f"refusing to write inside the game install: {dest}")
    except ValueError:
        pass

    if dest.exists():
        if not args.force:
            raise SystemExit(f"destination exists: {dest}  (use --force to replace)")
        shutil.rmtree(dest)
    dest.mkdir(parents=True)

    started = datetime.now().isoformat(timespec="seconds")
    print(f"[extract] corpus={args.corpus} install={install}")
    print(f"[extract] dest={dest}")

    if args.corpus == "gamma":
        copied, mods, source_info = build_gamma(install, args.profile, dest, args.include_disabled)
    else:
        copied, mods, source_info = build_vanilla(install, dest)

    print(f"[extract] copied {len(copied)} files from {len(mods)} mods; hashing...")
    manifest, manifest_path = write_manifest(dest, args.corpus, copied, mods, source_info, started)

    print(f"[extract] mods with scripts : {manifest['mod_count']}")
    print(f"[extract] files             : {manifest['file_count']}")
    print(f"[extract] bytes             : {manifest['total_bytes']:,}")
    print(f"[extract] encodings         : {manifest['encodings']}")
    print(f"[extract] manifest          : {manifest_path}")


if __name__ == "__main__":
    main()
