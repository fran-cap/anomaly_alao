"""Snapshot and restore the mutable configuration: user.ltx and MO2 profiles.

A snapshot is a directory under ``data/snapshots/<timestamp>[-label]/`` holding
copies of the files plus a ``manifest.json`` describing where each came from and
its SHA-256.  Restore copies the stored copies back over the originals.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from . import config as _config
from . import ltx as _ltx

__all__ = ["take", "restore", "list_snapshots", "load_manifest", "diff_user_ltx", "timestamp"]

MANIFEST_NAME = "manifest.json"


def timestamp(now: datetime | None = None) -> str:
    """Local ``YYYYMMDD-HHMMSS`` stamp, matching the contract's run_id prefix."""
    return (now or datetime.now()).strftime("%Y%m%d-%H%M%S")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _iso(dt: datetime | None = None) -> str:
    return (dt or datetime.now(timezone.utc)).isoformat(timespec="seconds")


def take(label: str | None = None, profile: str | None = None, cfg=None, include_profile: bool = True) -> Path:
    """Snapshot user.ltx (and optionally the MO2 profile) into data/snapshots.

    Returns the snapshot directory.
    """
    cfg = cfg or _config.get()
    cfg.ensure_data_dirs()
    prof = profile or cfg.profile
    name = timestamp() + (f"-{label}" if label else "")
    dest = cfg.snapshots_dir / name
    dest.mkdir(parents=True, exist_ok=True)

    files: list[dict] = []

    user_ltx = cfg.effective_user_ltx(prof)
    if user_ltx.is_file():
        stored = dest / "user.ltx"
        shutil.copy2(user_ltx, stored)
        files.append(
            {
                "kind": "user_ltx",
                "source": str(user_ltx),
                "stored": stored.name,
                "sha256": _sha256(stored),
                "size": stored.stat().st_size,
            }
        )

    if include_profile:
        src_profile = cfg.profile_dir(prof)
        if src_profile.is_dir():
            stored_dir = dest / "profile"
            shutil.copytree(src_profile, stored_dir, dirs_exist_ok=True)
            for f in sorted(stored_dir.rglob("*")):
                if f.is_file():
                    rel = f.relative_to(dest).as_posix()
                    files.append(
                        {
                            "kind": "profile_file",
                            "source": str(src_profile / f.relative_to(stored_dir)),
                            "stored": rel,
                            "sha256": _sha256(f),
                            "size": f.stat().st_size,
                        }
                    )

    manifest = {
        "snapshot_id": name,
        "created": _iso(),
        "label": label,
        "profile": prof,
        "game_root": str(cfg.game_root),
        "files": files,
    }
    (dest / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return dest


def list_snapshots(cfg=None) -> list[dict]:
    """Manifests of every snapshot, newest first."""
    cfg = cfg or _config.get()
    if not cfg.snapshots_dir.is_dir():
        return []
    out = []
    for d in sorted(cfg.snapshots_dir.iterdir(), reverse=True):
        m = d / MANIFEST_NAME
        if m.is_file():
            try:
                out.append(json.loads(m.read_text(encoding="utf-8")))
            except json.JSONDecodeError:
                continue
    return out


def load_manifest(snapshot: str | Path, cfg=None) -> dict:
    cfg = cfg or _config.get()
    d = Path(snapshot)
    if not d.is_dir():
        d = cfg.snapshots_dir / str(snapshot)
    return json.loads((d / MANIFEST_NAME).read_text(encoding="utf-8"))


def restore(snapshot: str | Path, cfg=None, dry_run: bool = False, kinds=None) -> list[str]:
    """Copy a snapshot's files back over their sources.

    *kinds* filters by manifest kind (``user_ltx``, ``profile_file``).  Returns
    the list of restored destination paths.
    """
    cfg = cfg or _config.get()
    d = Path(snapshot)
    if not d.is_dir():
        d = cfg.snapshots_dir / str(snapshot)
    manifest = json.loads((d / MANIFEST_NAME).read_text(encoding="utf-8"))
    restored: list[str] = []
    for f in manifest["files"]:
        if kinds and f["kind"] not in kinds:
            continue
        src = d / f["stored"]
        dst = Path(f["source"])
        if not src.is_file():
            continue
        restored.append(str(dst))
        if dry_run:
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    return restored


def diff_user_ltx(before, after) -> dict:
    """``{key: [old, new]}`` between two user.ltx paths, snapshots or objects.

    Accepts a snapshot id/directory on either side and resolves its user.ltx.
    """
    return _ltx.diff_user_ltx(_resolve_user_ltx(before), _resolve_user_ltx(after))


def _resolve_user_ltx(target):
    if isinstance(target, _ltx.UserLtx):
        return target
    p = Path(target)
    if p.is_dir():
        candidate = p / "user.ltx"
        if candidate.is_file():
            return candidate
        raise FileNotFoundError(f"no user.ltx inside {p}")
    if p.is_file():
        return p
    cfg = _config.get()
    candidate = cfg.snapshots_dir / str(target) / "user.ltx"
    if candidate.is_file():
        return candidate
    raise FileNotFoundError(f"cannot resolve user.ltx from {target!r}")
