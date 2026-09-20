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


def _report_hitch(name, dirs, top, pct, scope=None):
    """I-058: the tail, not the mean - worst single call, p99, first slow call.

    Aggregated across the runs of an arm by taking the worst max and the
    earliest first-call, because a hitch is a per-event property and the runs
    are repeats of the same event, not samples of one distribution.
    """
    rows: dict = {}
    seen = 0
    for d in dirs:
        log = _profiler.load_run(d) if Path(d).is_dir() else _profiler.load(d)
        if not log or not log.hitches:
            continue
        seen += 1
        for r in log.hitch_ranking(scope=scope, pct=pct):
            key = (r["scope"], r["name"])
            cur = rows.get(key)
            if cur is None:
                cur = dict(r)
                cur["first"] = (r["first_ms"], r["first_frame"], r["first_t"])
                rows[key] = cur
                continue
            cur["calls"] += r["calls"]
            cur["above_floor"] += r["above_floor"]
            if (r["max_ms"] or 0) > (cur["max_ms"] or 0):
                cur["max_ms"] = r["max_ms"]
            # the earliest first slow call across the repeats of the arm
            if r["first_frame"] < cur["first"][1]:
                cur["first"] = (r["first_ms"], r["first_frame"], r["first_t"])
            # widest (most pessimistic) percentile bracket of the repeats
            if (r["p_lo_ms"] or 0) > (cur["p_lo_ms"] or 0):
                cur["p_lo_ms"], cur["p_hi_ms"] = r["p_lo_ms"], r["p_hi_ms"]
    if not seen:
        print(f"#### {name}: no hitch (`hit`) lines - the overlay was not the hitch build\n")
        return []
    ranked = sorted(rows.values(), key=lambda r: (r["max_ms"] or 0.0), reverse=True)[:top]
    print(f"#### {name}: hitch tail ({seen} run(s), floor "
          f"{_fmt(ranked[0]['floor_ms'], 3) if ranked else '?'} ms)")
    print(f"| # | scope | name | calls | >=floor | max ms | p{pct:g} ms | first ms | first frame |")
    print("|---|---|---|---:|---:|---:|---:|---:|---:|")
    for i, r in enumerate(ranked, 1):
        lo, hi = r["p_lo_ms"], r["p_hi_ms"]
        p = f"{_fmt(lo, 2)}+" if hi is None else f"{_fmt(lo, 2)}-{_fmt(hi, 2)}"
        first = r.get("first") or (r["first_ms"], r["first_frame"], r["first_t"])
        print(f"| {i} | {r['scope']} | `{r['name']}` | {r['calls']} | {r['above_floor']} | "
              f"{_fmt(r['max_ms'], 2)} | {p} | {_fmt(first[0], 2)} | {first[1]} |")
    print()
    return ranked


def _report_frames(name, dirs, top, min_ms=0.0):
    """I-062: the slow-frame table, and how much of a slow frame was script.

    The one number this exists for is `inv ms` - frame ms minus the union of
    top-level script regions in that frame.  Big and flat across the slow
    frames means the cost is engine side (or Lua nothing on the wrap lists
    reaches); small means we are looking straight at it in the `top` column.
    """
    rows, summaries, wdr = [], [], None
    for d in dirs:
        log = _profiler.load_run(d) if Path(d).is_dir() else _profiler.load(d)
        if not log or not log.frames:
            continue
        wdr = wdr or log.walkout
        rows.extend(log.frame_rows(min_ms))
        summaries.append(log.frame_summary(min_ms))
    if not rows:
        print(f"#### {name}: no `frm` lines - the overlay was not the walkout build\n")
        return []
    rows.sort(key=lambda r: r["ms"], reverse=True)
    if wdr:
        print(f"#### {name}: slow frames (floor {_fmt(wdr.frame_floor_ms, 0)} ms, "
              f"wrapped {wdr.walkout}"
              + (f", pending {wdr.pending}" if wdr.pending else "") + ")")
    else:
        print(f"#### {name}: slow frames")
    n_sf = sum(s["n"] for s in summaries)
    med = [s["median_script_pct"] for s in summaries if s["median_script_pct"] is not None]
    print(f"- {n_sf} slow frame(s) over {len(summaries)} run(s); median script share "
          + (f"{min(med):.0f}-{max(med):.0f}%" if med else "-")
          + f"; {sum(s['spawns'] for s in summaries)} net_spawn / "
          f"{sum(s['destroys'] for s in summaries)} net_destroy in them; "
          f"{sum(s['gc_frames'] for s in summaries)} with a GC drop > 64 KB")
    print("| # | frame | t | ms | dt_dev | script ms | script % | inv ms | cb | bnd | eng | evt | sp/de | dGC kB | biggest call |")
    print("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|---|")
    for i, r in enumerate(rows[:top], 1):
        biggest = "-"
        if r["top"] and r["top"][0][0] not in ("-", ""):
            biggest = f"`{r['top'][0][0]}` {_fmt(r['top'][0][1], 2)}"
        print(f"| {i} | {r['frame']} | {r['t']:.0f} | {r['ms']:.0f} | {_fmt(r['dt_dev'], 4)} | "
              f"{_fmt(r['script_ms'], 2)} | {_fmt(r['script_pct'], 0)}% | "
              f"{_fmt(r['invisible_ms'], 2)} | {_fmt(r['cb_ms'], 2)} | {_fmt(r['bnd_ms'], 2)} | "
              f"{_fmt(r['eng_ms'], 2)} | {_fmt(r['evt_ms'], 2)} | {r['spawn']}/{r['destroy']} | "
              f"{r['gc_delta_kb']:+.0f} | {biggest} |")
    print()
    agg = {}
    for s in summaries:
        for a in s["top_scopes"]:
            m = agg.setdefault(a["name"], {"name": a["name"], "n": 0, "ms": 0.0, "max_ms": 0.0})
            m["n"] += a["n"]
            m["ms"] += a["ms"]
            m["max_ms"] = max(m["max_ms"], a["max_ms"])
    print(f"#### {name}: scopes on top of the slow frames")
    print("| # | scope | frames | total ms | worst ms |")
    print("|---|---|---:|---:|---:|")
    for i, a in enumerate(sorted(agg.values(), key=lambda x: x["ms"], reverse=True)[:top], 1):
        print(f"| {i} | `{a['name']}` | {a['n']} | {_fmt(a['ms'], 2)} | {_fmt(a['max_ms'], 2)} |")
    print()
    return rows


def _report_axes(name, dirs, top, drop_first):
    """I-062: the bnd / eng / evt rankings, kept apart from the cb ranking."""
    out = {}
    for tag, title in (("bnd", "object binders and se_* server objects"),
                       ("eng", "engine-called globals"),
                       ("evt", "time-event / deferred bodies")):
        rows = []
        for d in dirs:
            log = _profiler.load_run(d) if Path(d).is_dir() else _profiler.load(d)
            if not log:
                continue
            rows = log.axis_ranking(tag, top=top, drop_first=drop_first)
            if rows:
                break
        if not rows:
            continue
        out[tag] = rows
        print(f"#### {name}: axis `{tag}` - {title}")
        print("| # | scope | ms/frame | calls/frame | us/call |")
        print("|---|---|---:|---:|---:|")
        for i, r in enumerate(rows, 1):
            print(f"| {i} | `{r['name']}` | {_fmt(r['ms_per_frame'], 4)} | "
                  f"{_fmt(r['calls_per_frame'], 2)} | {_fmt(r['us_per_call'], 1)} |")
        print()
    if not out:
        print(f"(no `axs` rows: the overlay was not the walkout build)\n")
    return out


def _report_trace(name, dirs, top, prefix=None):
    """I-063: one row per call of the traced listener, in call order."""
    rows = []
    for d in dirs:
        log = _profiler.load_run(d) if Path(d).is_dir() else _profiler.load(d)
        if not log or not log.traces:
            continue
        rows.extend(log.trace_rows(prefix))
    if not rows:
        print(f"#### {name}: no `trace` lines - the overlay had TRACE_LISTENERS off\n")
        return []
    print(f"#### {name}: per-call trace ({len(rows)} call(s))")
    print("| # | t | frame | ms | cells pre->post | grid pre->post | idxer |")
    print("|---|---:|---:|---:|---|---|---:|")
    for i, r in enumerate(rows[:top], 1):
        pre, post = r["pre"], r["post"]
        cells = f"{pre.get('cells', '-')} -> {post.get('cells', '-')}"
        grid = f"{pre.get('grid', '-')} -> {post.get('grid', '-')}"
        grew = r["grew"]
        mark = " **+**" if any(v for v in grew.values()) else ""
        print(f"| {i} | {r['t']:.0f} | {r['frame']} | {_fmt(r['ms'], 2)} | {cells}{mark} | "
              f"{grid} | {post.get('idxer', '-')} |")
    grown = [r for r in rows if any(v for v in r["grew"].values())]
    flat = [r for r in rows if r not in grown]
    def _mean(rs):
        vals = [r["ms"] for r in rs if r["ms"] is not None]
        return sum(vals) / len(vals) if vals else None
    print(f"\n- calls where the cell pool or grid grew: {len(grown)}, mean "
          f"{_fmt(_mean(grown), 2)} ms; calls with no growth: {len(flat)}, mean "
          f"{_fmt(_mean(flat), 2)} ms\n")
    return rows


def _report_arm(name, dirs, top, drop_first, listeners=False, drop_rounds=0,
                hitch=False, hitch_pct=99.0, frames=False, axes=False,
                trace=False, frame_min_ms=0.0, trace_prefix=None):
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
    if hitch:
        rep["hitch"] = _report_hitch(name, [d for d, _ in logs], top, hitch_pct)
    if frames:
        rep["frames"] = _report_frames(name, [d for d, _ in logs], top, frame_min_ms)
    if axes:
        rep["axes"] = _report_axes(name, [d for d, _ in logs], top, drop_first)
    if trace:
        rep["trace"] = _report_trace(name, [d for d, _ in logs], top, trace_prefix)
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
    ap.add_argument("--hitch", action="store_true",
                    help="I-058: also report the tail (worst call, p99, first slow call). "
                         "Needs the hitch build of the overlay (alao-profiler-hitch*)")
    ap.add_argument("--hitch-pct", type=float, default=99.0,
                    help="percentile for --hitch (default 99)")
    ap.add_argument("--frames", action="store_true",
                    help="I-062: the slow-frame table - frame ms, script ms and what is "
                         "left over. Needs the walkout build (alao-profiler-walkout*)")
    ap.add_argument("--frame-min-ms", type=float, default=0.0,
                    help="only report frames at least this long (on top of the overlay's floor)")
    ap.add_argument("--axes", action="store_true",
                    help="I-062: rank the binder / engine-global / time-event axes")
    ap.add_argument("--trace", action="store_true",
                    help="I-063: one row per call of the traced listener")
    ap.add_argument("--trace-prefix", help="only trace rows whose name starts with this")
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
        rep = _report_arm(name, dirs, a.top, a.drop_first, a.listeners, a.drop_rounds,
                          a.hitch, a.hitch_pct, a.frames, a.axes, a.trace,
                          a.frame_min_ms, a.trace_prefix)
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
