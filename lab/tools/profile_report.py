#!/usr/bin/env python3
"""Turn I-048 profiler dumps into a readable per-callback report.

    py -3.12 lab/tools/profile_report.py <run dir|xray.log> [...]        # one arm
    py -3.12 lab/tools/profile_report.py --queue <queue id>              # both arms of a run
    py -3.12 lab/tools/profile_report.py --arm-a <dirs...> --arm-b <dirs...>

A "run dir" is a directory under `lab/data/runs/` - the harness already copied
the engine log into it as `xray.log`, and that is where the ALAOPROF lines live.
Several dirs of the same arm are aggregated, and the spread across them is the
number that decides whether script-ms is an instrument at all (target < 5%).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

LAB = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LAB / "framework"))

from aalo import config as _config  # noqa: E402
from aalo import profiler as _profiler  # noqa: E402

# aalo.toml's `lab` always points at the MAIN checkout, never at a worktree -
# runs, the queue and the overlays are runtime state that exists once on the
# machine. Resolve both through it or a worktree finds empty directories.
_CFG = _config.get()
RUNS_ROOT = _CFG.runs_dir
MAIN_COORD = Path(_CFG.lab) / "coord"


def _fmt(v, nd=3):
    return "-" if v is None else f"{v:.{nd}f}"


def _resolve(paths):
    """Accept run dirs, raw logs, or run ids under lab/data/runs."""
    out = []
    runs_root = RUNS_ROOT
    for p in paths:
        p = Path(p)
        if p.is_dir():
            out.append(p)
        elif p.is_file():
            d = p.parent
            if p.name != "xray.log":   # a stray log: stage it so load_run finds it
                out.append(p)
                continue
            out.append(d)
        elif (runs_root / str(p)).is_dir():
            out.append(runs_root / str(p))
        else:
            print(f"! no such run dir or log: {p}", file=sys.stderr)
    return out


def _report_arm(name, dirs, top, drop_first, listeners=False, drop_rounds=0):
    logs = []
    for d in dirs:
        log = _profiler.load_run(d) if d.is_dir() else _profiler.load(d)
        if log and log.windows:
            logs.append((d, log))
    if not logs:
        print(f"### {name}: no ALAOPROF dumps in {len(dirs)} location(s)")
        return None
    hdr = logs[0][1].header
    print(f"### {name}")
    print(f"- timer `{hdr.timer}`, {hdr.units_per_ms:.1f} units/ms, "
          f"instrument cost {hdr.overhead_ns:.0f} ns per instrumented call, binders {hdr.binders}")
    rep = _profiler.compare_runs([d for d, _ in logs], drop_first=drop_first, drop_rounds=drop_rounds)
    s = rep["script_ms_per_frame"]
    print(f"- {rep['n_runs']} run(s), {rep['frames']} frames kept "
          f"(first {drop_first} window(s) of each run dropped"
          + (f", first {drop_rounds} whole round(s) dropped)" if drop_rounds else ")"))
    print(f"- **total script {_fmt(s['mean'])} ms/frame**, run-to-run cv "
          f"{_fmt(s['cv_pct'], 2)}%, range {_fmt(s['min'])}-{_fmt(s['max'])} ms")
    within = [w["cv_pct"] for w in rep["within_run"] if w["cv_pct"] is not None]
    if within:
        print(f"- within-run (window to window) cv {_fmt(min(within), 2)}-{_fmt(max(within), 2)}%")
    print()
    print("| # | callback | ms/frame | share | calls/frame | us/call | cv across runs |")
    print("|---|---|---:|---:|---:|---:|---:|")
    total = s["mean"] or 0.0
    for i, r in enumerate(rep["ranking"][:top], 1):
        share = 100.0 * r["ms_per_frame"] / total if total else 0.0
        us = (r["ms_per_frame"] * 1000.0 / r["calls_per_frame"]) if r["calls_per_frame"] else None
        print(f"| {i} | `{r['name']}` | {_fmt(r['ms_per_frame'], 4)} | {share:.1f}% | "
              f"{_fmt(r['calls_per_frame'], 2)} | {_fmt(us, 1)} | {_fmt(r['cv_pct'], 1)}% |")
    print()
    if listeners:
        rows = []
        for _, log in logs:
            rows = log.ranking(top=top, drop_first=drop_first, listeners=True)
            if rows:
                break
        if not rows:
            print(f"(no per-listener rows: the overlay ran with listeners={hdr.listeners})\n")
        else:
            print(f"#### {name}: per listener (one run, listeners={hdr.listeners})")
            print("| # | listener | ms/frame | calls/frame | us/call |")
            print("|---|---|---:|---:|---:|")
            for i, r in enumerate(rows, 1):
                print(f"| {i} | `{r['name']}` | {_fmt(r['ms_per_frame'], 4)} | "
                      f"{_fmt(r['calls_per_frame'], 2)} | {_fmt(r['us_per_call'], 1)} |")
            print()
    return rep


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="*", help="run dirs / run ids / xray.log files (one arm)")
    ap.add_argument("--arm-a", nargs="*", default=[], help="baseline arm")
    ap.add_argument("--arm-b", nargs="*", default=[], help="variant arm")
    ap.add_argument("--queue", help="queue item id: read both arms out of its result")
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--listeners", action="store_true",
                    help="also rank the individual subscribers (needs WRAP_LISTENERS in the overlay)")
    ap.add_argument("--drop-rounds", type=int, default=0,
                    help="skip this many whole runs from the start of the arm; 1 drops the session "
                         "warm-up round, which runs about 10%% high in script-ms")
    ap.add_argument("--drop-first", type=int, default=1,
                    help="windows to drop from the start of each run (default 1: it straddles the load)")
    ap.add_argument("--json", type=Path, help="also write the aggregate here")
    a = ap.parse_args(argv)

    arms: dict = {}
    if a.queue:
        sys.path.insert(0, str(MAIN_COORD))
        import coord
        item = None
        for it in coord.queue_list():
            if it["id"] == a.queue or it["id"].endswith(a.queue):
                item = it
                break
        if item is None:
            print(f"no queue item {a.queue} under {MAIN_COORD / 'queue'}", file=sys.stderr)
            return 2
        for arm, info in ((item.get("result") or {}).get("arms", {})).items():
            arms[arm] = [RUNS_ROOT / r for r in info.get("runs", [])]
    elif a.arm_a or a.arm_b:
        if a.arm_a:
            arms["baseline"] = _resolve(a.arm_a)
        if a.arm_b:
            arms["variant"] = _resolve(a.arm_b)
    elif a.paths:
        arms["runs"] = _resolve(a.paths)
    else:
        ap.error("give run dirs, --arm-a/--arm-b, or --queue")

    out = {}
    for name, dirs in arms.items():
        rep = _report_arm(name, dirs, a.top, a.drop_first, a.listeners, a.drop_rounds)
        if rep:
            out[name] = rep
    b = (out.get("baseline") or {}).get("script_ms_per_frame") or {}
    v = (out.get("variant") or {}).get("script_ms_per_frame") or {}
    if b.get("mean") and v.get("mean"):
        d = v["mean"] - b["mean"]
        print(f"**delta: {d:+.4f} ms/frame ({100.0 * d / b['mean']:+.2f}%)**  "
              f"— resolvable only if it clears the arms' cv "
              f"({_fmt(b.get('cv_pct'), 2)}% / {_fmt(v.get('cv_pct'), 2)}%)")
    if a.json:
        a.json.write_text(json.dumps(out, indent=2), encoding="utf-8")
        print(f"\nwrote {a.json}")
    return 0 if out else 1


if __name__ == "__main__":
    sys.exit(main())
