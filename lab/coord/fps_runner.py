#!/usr/bin/env python3
"""Drain the FPS queue: run each queued in-game A/B under the `game` lock.

MUST run from an ELEVATED terminal (MO2 is RUNASADMIN and PresentMon needs an
ETW session; see lab/framework/README.md "Elevation caveat").  Close RTSS /
Afterburner first.  Agents never call this; they `coord queue submit` a request
and the organizer (or whoever holds the elevated shell) runs:

    py -3.12 C:\\code\\GIT\\anomaly_alao\\lab\\coord\\fps_runner.py            # wait for work, run it, keep waiting
    py -3.12 ...\\fps_runner.py --once                                           # one item, then exit
    py -3.12 ...\\fps_runner.py --exit-when-empty                                # old drain-and-exit behaviour
    py -3.12 ...\\fps_runner.py --dry-run                                        # harness dry-run, no game
    py -3.12 ...\\fps_runner.py --poll 30                                        # poll interval (default 60 s)

The game is only ever launched for a claimed queue item. With nothing queued the
runner idles, printing a heartbeat every few minutes, until Ctrl+C.

Request JSON an agent submits (all paths absolute):

    {
      "label": "counter-append",                 # short slug, [a-z0-9-]
      "variant_overlay":  "C:/.../overlay-b",    # dir with gamedata/scripts/... (build_overlay.py output)
      "baseline_overlay": null,                  # null = stock game; or another overlay dir
      "variant_overlay_bottom": null,            # optional: a LOWEST-priority overlay (rewritten vanilla
      "baseline_overlay_bottom": null,           #   scripts: every mod still overrides it), same for baseline
      "profiler_overlay": "C:/.../lab/profiler", # optional: I-048 script-side profiler, installed at the
                                                 #   TOP and enabled in BOTH arms (it is the instrument,
                                                 #   not the treatment). Its dumps ride the engine log,
                                                 #   which every run dir already keeps as xray.log.
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
from aalo import config as _config, mo2 as _mo2, profiler as _profiler  # noqa: E402

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


def restore_selected_profile(cfg, wanted: str) -> bool:
    """MO2 persists the `-p <profile>` we launch with into ModOrganizer.ini, so
    after a run its selected profile points at an aalo-* copy we then delete.
    Put the live profile back (a one-line byte-exact edit, nothing else touched)."""
    import re
    ini = cfg.mo2_root / "ModOrganizer.ini" if hasattr(cfg, "mo2_root") else cfg.mo2_exe.parent / "ModOrganizer.ini"
    if not ini.is_file():
        return False
    raw = ini.read_bytes()
    m = re.search(rb"^selected_profile=.*$", raw, re.M)
    if not m:
        return False
    line = m.group()
    current = line.split(b"=", 1)[1]
    if current.startswith(b"@ByteArray(") and current.endswith(b")"):
        current = current[len(b"@ByteArray("):-1]
    if current.decode("utf-8", "replace") == wanted:
        return False
    newline = b"selected_profile=@ByteArray(" + wanted.encode("utf-8") + b")"
    ini.write_bytes(raw[:m.start()] + newline + raw[m.end():])
    return True


def make_source_profile(m: _mo2.MO2, qid: str, top: list[str], bottom: list[str] = ()) -> str:
    """Clone the live profile; *top* mods go first (highest priority, they win),
    *bottom* mods go last (lowest priority, every other mod overrides them - the
    place for a rewritten-vanilla-scripts mod). All inserted disabled."""
    prof = m.copy_profile(SRC_PREFIX + qid, source=m.cfg.profile, overwrite=True)
    ml = m.modlist(prof)
    for name in reversed(top):
        if ml.find(name) is None:
            ml.entries.insert(0, _mo2.ModEntry(_mo2.DISABLED, name))
    for name in bottom:
        if ml.find(name) is None:
            ml.entries.append(_mo2.ModEntry(_mo2.DISABLED, name))
    ml.save()
    return prof


def write_experiment(cfg, qid: str, req: dict, prof: str, arm_mods: dict) -> Path:
    """*arm_mods* = {"baseline": [mod names], "variant": [mod names]}; each arm
    enables its own mods and disables the other arm's."""
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
    ]
    for arm in ("baseline", "variant"):
        mine = arm_mods.get(arm, [])
        # a mod both arms enable (the profiler) must not land in the other arm's
        # disable list, or the TOML would set it true and false in one table
        others = [n for a, ns in arm_mods.items() if a != arm for n in ns if n not in mine]
        lines += ["", f"[{arm}]", f'notes = "{("overlay " + " ".join(mine)) if mine else "stock"}"', f"[{arm}.mods]"]
        lines += [f'"{n}" = true' for n in mine] + [f'"{n}" = false' for n in others]
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
    capped_runs = []
    for arm, runs in arms.items():
        # a round pinned to a refresh rate measured the cap, not the scripts:
        # keep it in the record, keep it out of the means
        capped = [r for r in runs if (r.get("extra") or {}).get("capped")]
        capped_runs += [r["run_id"] for r in capped]
        good = [r for r in runs if r not in capped] or runs
        per_arm[arm] = {
            "n": len(good),
            "n_all": len(runs),
            "fps_avg": mean(r.get("fps_avg") for r in good),
            "fps_1pct_low": mean(r.get("fps_1pct_low") for r in good),
            "frametime_p99_ms": mean(r.get("frametime_p99_ms") for r in good),
            "fps_avg_all_rounds": mean(r.get("fps_avg") for r in runs),
            "crashed": sum(1 for r in runs if r.get("crashed")),
            "capped": [r["run_id"] for r in capped],
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
    if capped_runs:
        summary += f"  [{len(capped_runs)} capped round(s) excluded]"
    return {"experiment": exp_name, "arms": per_arm, "delta": delta, "run_dirs": run_dirs,
            "capped_runs": capped_runs, "summary": summary}


def summarize_profiler(cfg, per_arm: dict) -> dict | None:
    """Fold the I-048 profiler dumps out of each run's xray.log into the result.

    Returns None when no run carried a dump (no profiler overlay, or it failed
    to install - which shows up as `errors` rather than silence when it did try).
    Also writes `<run>/profiler.json` so the raw per-window numbers stay next to
    the frametimes instead of only in the queue item.
    """
    out: dict = {"arms": {}}
    any_dump = False
    for arm, info in per_arm.items():
        dirs = [cfg.runs_dir / r for r in info.get("runs", [])]
        dirs = [d for d in dirs if (d / "xray.log").is_file()]
        for d in dirs:
            log = _profiler.load_run(d)
            if log is None or not log.windows:
                continue
            (d / "profiler.json").write_text(
                json.dumps({"summary": log.summary(), "ranking": log.ranking(top=40)}, indent=2),
                encoding="utf-8")
        rep = _profiler.compare_runs(dirs)
        if rep["n_runs"]:
            any_dump = True
        rep["ranking"] = rep["ranking"][:20]
        # the first round of an arm runs ~10% high in script-ms (session
        # warm-up the in-level warm-up misses); keep both numbers rather than
        # quietly picking one
        warm = _profiler.compare_runs(dirs, drop_rounds=1)
        rep["script_ms_per_frame_warm"] = warm["script_ms_per_frame"]
        rep["n_runs_warm"] = warm["n_runs"]
        out["arms"][arm] = rep
    if not any_dump:
        return None
    b = out["arms"].get("baseline", {}).get("script_ms_per_frame") or {}
    v = out["arms"].get("variant", {}).get("script_ms_per_frame") or {}
    if b.get("mean") and v.get("mean"):
        out["delta_script_ms_per_frame"] = round(v["mean"] - b["mean"], 4)
        out["delta_pct"] = round(100.0 * (v["mean"] - b["mean"]) / b["mean"], 2)
    out["summary"] = (
        f"script ms/frame baseline {b.get('mean')} (cv {b.get('cv_pct')}%) -> "
        f"variant {v.get('mean')} (cv {v.get('cv_pct')}%)")
    return out


def process(item: dict, dry_run: bool, keep: bool) -> dict:
    cfg = _config.get()
    m = _mo2.MO2(cfg)
    req = item["request"]
    req.setdefault("idea", item.get("idea", ""))
    qid = _slug(item["id"])
    # (request key, arms, suffix, position)
    slots = [
        ("variant_overlay", ("variant",), "b", "top"),
        ("baseline_overlay", ("baseline",), "a", "top"),
        ("variant_overlay_bottom", ("variant",), "b2", "bottom"),
        ("baseline_overlay_bottom", ("baseline",), "a2", "bottom"),
        # the instrument, not the treatment: same mod, same priority, both arms
        ("profiler_overlay", ("baseline", "variant"), "p", "top"),
    ]
    installed, top, bottom, prof, toml = [], [], [], None, None
    arm_mods: dict = {"baseline": [], "variant": []}
    try:
        for key, arms, suffix, pos in slots:
            src = req.get(key)
            if not src:
                continue
            name = f"{REWRITE_PREFIX}{qid}-{suffix}"
            install_overlay(cfg, name, Path(src))
            installed.append(name)
            (top if pos == "top" else bottom).append(name)
            for arm in arms:
                arm_mods[arm].append(name)
        if not (req.get("variant_overlay") or req.get("variant_overlay_bottom")):
            raise ValueError("request has no variant_overlay / variant_overlay_bottom")
        prof = make_source_profile(m, qid, top, bottom)
        toml = write_experiment(cfg, qid, req, prof, arm_mods)
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
        result["overlay_mods"] = {"top": top, "bottom": bottom}
        result["dry_run"] = dry_run
        if req.get("profiler_overlay"):
            # NB: not `prof` - that name holds the source profile the finally
            # block has to delete, and shadowing it leaks a profile per run
            prof_report = summarize_profiler(cfg, result.get("arms", {}))
            result["profiler"] = prof_report
            if prof_report:
                result["summary"] += "  |  " + prof_report["summary"]
            else:
                result["summary"] += "  |  profiler: no ALAOPROF dumps in any run log"
        if proc.returncode != 0:
            raise RuntimeError(f"aalo run exited {proc.returncode}: {result['summary']}")
        return result
    finally:
        if not keep:
            for name in installed:
                remove_overlay(cfg, name)
            if prof:
                m.delete_profile(prof)
        if restore_selected_profile(cfg, cfg.profile):
            print(f"[fps-runner] restored MO2 selected profile to {cfg.profile}", flush=True)
            if toml and toml.is_file():
                toml.unlink()


def drain(once: bool, dry_run: bool, keep: bool, poll: float, exit_when_empty: bool) -> int:
    n = 0
    idle_since = None
    last_beat = 0.0
    while True:
        item = coord.queue_claim(RUNNER_OWNER)
        if item is None:
            if exit_when_empty:
                print(f"[fps-runner] queue empty ({n} processed)")
                return 0
            now = time.time()
            if idle_since is None:
                idle_since = now
                print(f"[fps-runner] {datetime.now():%H:%M:%S} queue empty, waiting (poll {poll:g} s, Ctrl+C to stop)",
                      flush=True)
                coord.post_status(RUNNER_OWNER, "", "idle", "waiting for queue items")
            elif now - last_beat >= 300:
                print(f"[fps-runner] {datetime.now():%H:%M:%S} still waiting, idle {int((now - idle_since) / 60)} min, "
                      f"{n} processed this session", flush=True)
            last_beat = now
            try:
                time.sleep(poll)
            except KeyboardInterrupt:
                print("\n[fps-runner] stopped while idle")
                coord.post_status(RUNNER_OWNER, "", "stopped", "runner exited while idle")
                return 0
            continue
        idle_since = None
        print(f"[fps-runner] {datetime.now():%H:%M:%S} claimed {item['id']} (idea {item['idea']}, {item['agent']})",
              flush=True)
        coord.post_status(RUNNER_OWNER, item["idea"], "fps-run", f"running {item['id']}")
        try:
            with coord.held("game", RUNNER_OWNER, ttl=1800, wait=3600, note=f"fps run {item['id']}"):
                result = process(item, dry_run, keep)
            coord.queue_finish(item["id"], True, result)
            coord.post_status(RUNNER_OWNER, item["idea"], "fps-done", result["summary"])
            print(f"[fps-runner] done {item['id']}: {result['summary']}", flush=True)
        except KeyboardInterrupt:
            # process()'s finally has already removed the overlays/profile/toml
            coord.queue_finish(item["id"], False, error="interrupted (Ctrl+C) by the runner operator")
            coord.post_status(RUNNER_OWNER, item["idea"], "fps-failed", f"{item['id']} interrupted by Ctrl+C")
            print(f"\n[fps-runner] interrupted during {item['id']}; item marked failed, cleanup done", flush=True)
            return 130
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
    ap.add_argument("--once", action="store_true", help="process one item (waiting for it if needed), then exit")
    ap.add_argument("--exit-when-empty", action="store_true", help="drain the queue and exit instead of waiting")
    ap.add_argument("--poll", type=float, default=60.0, help="seconds between queue checks while idle (default 60)")
    ap.add_argument("--dry-run", action="store_true", help="harness dry-run: no game launch, synthetic samples")
    ap.add_argument("--keep", action="store_true", help="leave overlay mods / profile / toml in place afterwards")
    ap.add_argument("--watch", type=float, help=argparse.SUPPRESS)  # old spelling of --poll
    ap.add_argument("--no-admin-check", action="store_true")
    a = ap.parse_args(argv)
    if a.watch:
        a.poll = a.watch
    if not a.dry_run and not a.no_admin_check and not is_admin():
        print("fps_runner: this terminal is not elevated; MO2 launch will fail. "
              "Open an elevated shell, or use --dry-run.", file=sys.stderr)
        return 2
    return drain(a.once, a.dry_run, a.keep, max(5.0, a.poll), a.exit_when_empty)


if __name__ == "__main__":
    sys.exit(main())
