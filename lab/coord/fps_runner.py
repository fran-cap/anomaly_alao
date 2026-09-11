#!/usr/bin/env python3
"""Drain the FPS queue: run each queued in-game A/B under the `game` lock.

MUST run from an ELEVATED terminal (MO2 is RUNASADMIN and PresentMon needs an
ETW session; see lab/framework/README.md "Elevation caveat").  Close RTSS /
Afterburner first.  Agents never call this; they `coord queue submit` a request
and the organizer (or whoever holds the elevated shell) runs:

    py -3.12 C:\\code\\GIT\\anomaly_alao\\lab\\coord\\fps_runner.py            # drain until empty
    py -3.12 ...\\fps_runner.py --once                                           # one item
    py -3.12 ...\\fps_runner.py --dry-run                                        # harness dry-run, no game
    py -3.12 ...\\fps_runner.py --watch 60                                       # poll every 60 s forever

Request JSON an agent submits (all paths absolute):

    {
      "label": "counter-append",                 # short slug, [a-z0-9-]
      "variant_overlay":  "C:/.../overlay-b",    # dir with gamedata/scripts/... (build_overlay.py output)
      "baseline_overlay": null,                  # null = stock game; or another overlay dir
      "repeats": 3, "duration_s": 300, "warmup_s": 30, "save": "gammabaseline",
      "notes": "ALAO --fix with I-001 counter append, 1503-file corpus"
    }

What one item does, in order:
  1. take the `game` lock (ttl = generous, heartbeat while running)
  2. copy each overlay to  GAMMA/mods/aalo-rewrite-<qid>-a|b/   (the ONLY writes into the
     install, always under the `aalo-rewrite-` prefix, always removed afterwards)
  3. copy the live profile to  profiles/aalo-src-<qid>  and insert both mod entries at the
     top (highest priority), disabled.  The live profile is never touched.
  4. write  lab/framework/experiments/aalo-rewrite-<qid>.toml  with baseline enabling -a
     (if any) and variant enabling -b, profile = aalo-src-<qid>
  5. `py -3.12 -m aalo run --experiment <toml> --save <save>`  (the harness copies
     aalo-src-<qid> again per run, toggles, snapshots/restores user.ltx, samples)
  6. collect the run dirs, compute per-arm means and the delta, write it into the queue item
  7. remove the overlay mods, the aalo-src profile and the TOML (kept with --keep)
"""
from __future__ import annotations

import argparse
import json
import shutil
import statistics
import subprocess
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

COORD = Path(__file__).resolve().parent
sys.path.insert(0, str(COORD))
sys.path.insert(0, str(COORD.parent / "framework"))
import coord  # noqa: E402
from aalo import config as _config, mo2 as _mo2  # noqa: E402

REWRITE_PREFIX = "aalo-rewrite-"
SRC_PREFIX = "aalo-src-"
RUNNER_OWNER = "fps-runner"


def is_admin() -> bool:
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _slug(s: str) -> str:
    return "".join(c if c.isalnum() or c == "-" else "-" for c in s.lower())[:40].strip("-") or "run"


def install_overlay(cfg, name: str, overlay: Path) -> Path:
    if not name.startswith(REWRITE_PREFIX):
        raise ValueError(f"refusing to write mod {name!r}: must start with {REWRITE_PREFIX}")
    if not (overlay / "gamedata").is_dir():
        raise FileNotFoundError(f"overlay has no gamedata/: {overlay}")
    dst = cfg.mods_dir / name
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(overlay, dst)
    return dst


def remove_overlay(cfg, name: str) -> None:
    if not name.startswith(REWRITE_PREFIX):
        raise ValueError(f"refusing to delete mod {name!r}")
    dst = cfg.mods_dir / name
    if dst.is_dir():
        shutil.rmtree(dst)


def make_source_profile(m: _mo2.MO2, qid: str, mod_names: list[str]) -> str:
    """Clone the live profile and put our overlay mods at the top, disabled."""
    prof = m.copy_profile(SRC_PREFIX + qid, source=m.cfg.profile, overwrite=True)
    ml = m.modlist(prof)
    for name in reversed(mod_names):
        if ml.find(name) is None:
            ml.entries.insert(0, _mo2.ModEntry(_mo2.DISABLED, name))
    ml.save()
    return prof


def write_experiment(cfg, qid: str, req: dict, prof: str, mod_a: str | None, mod_b: str) -> Path:
    exp_dir = cfg.experiments_dir
    exp_dir.mkdir(parents=True, exist_ok=True)
    p = exp_dir / f"{REWRITE_PREFIX}{qid}.toml"
    label = _slug(req.get("label", "alao"))
    lines = [
        f'name = "{REWRITE_PREFIX}{qid}"',
        f'idea_id = "{req.get("idea", "")}"',
        f"repeats = {int(req.get('repeats', 3))}",
        f"duration_s = {float(req.get('duration_s', 300))}",
        f"warmup_s = {float(req.get('warmup_s', 30))}",
        f'profile = "{prof}"',
        f'save = "{req.get("save", "gammabaseline")}"',
        f'notes = "{label}"',
        "",
        "[baseline]",
        f'notes = "{"overlay " + mod_a if mod_a else "stock"}"',
        "[baseline.mods]",
    ]
    if mod_a:
        lines.append(f'"{mod_a}" = true')
    lines.append(f'"{mod_b}" = false')
    lines += ["", "[variant]", f'notes = "overlay {mod_b}"', "[variant.mods]", f'"{mod_b}" = true']
    if mod_a:
        lines.append(f'"{mod_a}" = false')
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def summarize_runs(cfg, exp_name: str) -> dict:
    arms: dict[str, list[dict]] = {}
    run_dirs = []
    for d in sorted(cfg.runs_dir.iterdir()):
        mf = d / "manifest.json"
        if not mf.is_file():
            continue
        try:
            man = json.loads(mf.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if man.get("experiment") != exp_name:
            continue
        met = {}
        if (d / "metrics.json").is_file():
            met = json.loads((d / "metrics.json").read_text(encoding="utf-8"))
        arms.setdefault(man.get("arm", "?"), []).append({"run_id": d.name, "status": man.get("status"), **met})
        run_dirs.append(d.name)

    def mean(vals):
        vals = [v for v in vals if isinstance(v, (int, float))]
        return round(statistics.fmean(vals), 2) if vals else None

    per_arm = {}
    for arm, runs in arms.items():
        per_arm[arm] = {
            "n": len(runs),
            "fps_avg": mean(r.get("fps_avg") for r in runs),
            "fps_1pct_low": mean(r.get("fps_1pct_low") for r in runs),
            "frametime_p99_ms": mean(r.get("frametime_p99_ms") for r in runs),
            "crashed": sum(1 for r in runs if r.get("crashed")),
            "runs": [r["run_id"] for r in runs],
        }
    b, v = per_arm.get("baseline", {}), per_arm.get("variant", {})
    delta = {}
    for k in ("fps_avg", "fps_1pct_low", "frametime_p99_ms"):
        if b.get(k) is not None and v.get(k) is not None:
            delta[k] = round(v[k] - b[k], 2)
            delta[k + "_pct"] = round(100.0 * (v[k] - b[k]) / b[k], 2) if b[k] else None
    summary = (f"baseline fps {b.get('fps_avg')} / 1%low {b.get('fps_1pct_low')}  ->  "
               f"variant fps {v.get('fps_avg')} / 1%low {v.get('fps_1pct_low')}  "
               f"(delta avg {delta.get('fps_avg_pct')}%, 1%low {delta.get('fps_1pct_low_pct')}%)")
    return {"experiment": exp_name, "arms": per_arm, "delta": delta, "run_dirs": run_dirs, "summary": summary}


def process(item: dict, dry_run: bool, keep: bool) -> dict:
    cfg = _config.get()
    m = _mo2.MO2(cfg)
    req = item["request"]
    req.setdefault("idea", item.get("idea", ""))
    qid = _slug(item["id"])
    mod_b = f"{REWRITE_PREFIX}{qid}-b"
    mod_a = f"{REWRITE_PREFIX}{qid}-a" if req.get("baseline_overlay") else None
    installed, prof, toml = [], None, None
    try:
        install_overlay(cfg, mod_b, Path(req["variant_overlay"]))
        installed.append(mod_b)
        if mod_a:
            install_overlay(cfg, mod_a, Path(req["baseline_overlay"]))
            installed.append(mod_a)
        prof = make_source_profile(m, qid, installed)
        toml = write_experiment(cfg, qid, req, prof, mod_a, mod_b)
        cmd = [sys.executable, "-m", "aalo", "run", "--experiment", str(toml)]
        if req.get("save"):
            cmd += ["--save", req["save"]]
        if dry_run:
            cmd.append("--dry-run")
        print(f"[fps-runner] {' '.join(cmd)}", flush=True)
        t0 = time.time()
        proc = subprocess.run(cmd, cwd=str(COORD.parent / "framework"))
        result = summarize_runs(cfg, toml.stem)
        result["harness_rc"] = proc.returncode
        result["wall_s"] = round(time.time() - t0, 1)
        result["overlay_mods"] = installed
        result["dry_run"] = dry_run
        if proc.returncode != 0:
            raise RuntimeError(f"aalo run exited {proc.returncode}: {result['summary']}")
        return result
    finally:
        if not keep:
            for name in installed:
                remove_overlay(cfg, name)
            if prof:
                m.delete_profile(prof)
            if toml and toml.is_file():
                toml.unlink()


def drain(once: bool, dry_run: bool, keep: bool, watch: float | None) -> int:
    n = 0
    while True:
        item = coord.queue_claim(RUNNER_OWNER)
        if item is None:
            if watch:
                time.sleep(watch)
                continue
            print(f"[fps-runner] queue empty ({n} processed)")
            return 0
        print(f"[fps-runner] {datetime.now():%H:%M:%S} claimed {item['id']} (idea {item['idea']}, {item['agent']})",
              flush=True)
        coord.post_status(RUNNER_OWNER, item["idea"], "fps-run", f"running {item['id']}")
        try:
            with coord.held("game", RUNNER_OWNER, ttl=1800, wait=3600, note=f"fps run {item['id']}"):
                result = process(item, dry_run, keep)
            coord.queue_finish(item["id"], True, result)
            coord.post_status(RUNNER_OWNER, item["idea"], "fps-done", result["summary"])
            print(f"[fps-runner] done {item['id']}: {result['summary']}", flush=True)
        except Exception:
            err = traceback.format_exc()
            coord.queue_finish(item["id"], False, error=err[-2000:])
            coord.post_status(RUNNER_OWNER, item["idea"], "fps-failed", err.strip().splitlines()[-1][:200])
            print(f"[fps-runner] FAILED {item['id']}\n{err}", file=sys.stderr, flush=True)
        n += 1
        if once:
            return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="harness dry-run: no game launch, synthetic samples")
    ap.add_argument("--keep", action="store_true", help="leave overlay mods / profile / toml in place afterwards")
    ap.add_argument("--watch", type=float, help="poll interval seconds; keep draining forever")
    ap.add_argument("--no-admin-check", action="store_true")
    a = ap.parse_args(argv)
    if not a.dry_run and not a.no_admin_check and not is_admin():
        print("fps_runner: this terminal is not elevated; MO2 launch will fail. "
              "Open an elevated shell, or use --dry-run.", file=sys.stderr)
        return 2
    return drain(a.once, a.dry_run, a.keep, a.watch)


if __name__ == "__main__":
    sys.exit(main())
