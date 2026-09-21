"""I-065: read the post-load squad first-update burst back out of a queue item.

    py -3.12 lab/tools/i065_burst_report.py --queue <id> [--queue <id> ...]
    py -3.12 lab/tools/i065_burst_report.py --run <run dir name or path>

Three independent readings per run, because no single one is always there:

  profiler frm lines   slow frames (>= the profiler's 12 ms floor) whose biggest call is
                       `sim_squad_scripted.update`.  Run-scoped, NOT subject to the
                       dropped first 30 s window, but only written when a window closes,
                       so the capture has to outlive the window the burst ends in.
  profiler cb lines    `squad_on_first_update` calls per window.  All of them in window 1
                       with a worst `update` of 15-17 ms at frame 8 = the engine swept
                       behind the loading screen (mode A).  Spread over windows 1 and 2 =
                       the scheduler dribbled them out in play (mode B, the burst).
  presentmon.csv       frame times.  Only covers the burst when the request used a short
                       warm-up (the harness starts sampling warmup_s after the world
                       marker).  The burst is self-identifying: a train of >= 12 ms frames
                       ~455 ms apart (one per ALife tick).
  [alao_stagger] lines what the mod says it did.  Read these before crediting it: a
                       variant load with `0 pending` was a mode-A load and proves nothing.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

REPO = Path(r"C:\code\GIT\anomaly_alao")
RUNS = REPO / "lab" / "data" / "runs"
QUEUE = REPO / "lab" / "coord" / "queue"

SQUAD = "sim_squad_scripted.sim_squad_scripted.update"
TICK_MS = (380.0, 540.0)      # one ALife tick, measured 454-456 ms
FLOOR_MS = 12.0


def _kv(line: str) -> dict:
    return dict(p.split("=", 1) for p in line.split("|")[3:] if "=" in p)


def read_log(log: Path) -> dict:
    upm = 1000.0
    frames, first_cb, stagger, hit = [], {}, [], None
    for line in log.read_text(encoding="latin-1").splitlines():
        if "[alao_stagger" in line:
            stagger.append(line[line.index("[alao_stagger"):].strip())
            continue
        if "ALAOPROF|1|" not in line:
            continue
        kind = line.split("|")[2]
        kv = _kv(line)
        if kind == "hdr":
            upm = float(kv.get("units_per_ms", upm))
        elif kind == "frm" and kv.get("top", "").startswith(SQUAD):
            frames.append({"frame": int(kv["frame"]), "t": int(kv["t"]), "ms": float(kv["ms"]),
                           "script_ms": float(kv["u_top"]) / upm, "cb_ms": float(kv["u_cb"]) / upm,
                           "gc_kb": float(kv["gc1"]) - float(kv["gc0"]),
                           "worst_update_ms": max(float(x.split("~")[1]) for x in kv["top"].split(",")
                                                  if "~" in x) / upm})
        elif kind == "cb" and kv.get("name") == "squad_on_first_update":
            # with the mod the callback fires INSIDE actor_on_first_update, and the profiler
            # books a callback inside a callback as `nested`, not `calls`
            first_cb[int(kv["seq"])] = int(kv["calls"]) + int(kv.get("nested", 0) or 0)
        elif kind == "hit" and kv.get("name") == SQUAD:
            hit = {"max_ms": float(kv["max"]) / upm, "first_t": int(kv["first_t"]),
                   "first_frame": int(kv["first_frame"])}
    return {"frames": frames, "first_cb": first_cb, "stagger": stagger, "hit": hit}


def read_presentmon(path: Path) -> dict | None:
    if not path.is_file():
        return None
    slow, n, t_end = [], 0, 0.0
    with path.open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            try:
                t, ms = float(row["TimeInMs"]), float(row["MsBetweenPresents"])
            except (KeyError, ValueError, TypeError):   # a truncated last row reads as None
                continue
            n += 1
            t_end = t
            if ms >= FLOOR_MS and t <= 90_000:
                slow.append((t, ms))
    # the train: slow frames that sit one ALife tick after another slow frame
    train = []
    for i, (t, ms) in enumerate(slow):
        if any(TICK_MS[0] <= t - t0 <= TICK_MS[1] for t0, _ in slow[max(0, i - 6):i]):
            train.append((t, ms))
    return {"frames": n, "span_s": t_end / 1000.0, "slow_first_90s": len(slow),
            "train": train}


def describe(run_dir: Path) -> dict:
    out = {"run": run_dir.name, "arm": "?"}
    mf = run_dir / "manifest.json"
    if mf.is_file():
        man = json.loads(mf.read_text(encoding="utf-8"))
        out["arm"] = man.get("arm", "?")
        out["warmup_s"] = (man.get("warmup") or {}).get("warmup_s")
    log = run_dir / "xray.log"
    out.update(read_log(log) if log.is_file() else
               {"frames": [], "first_cb": {}, "stagger": [], "hit": None})
    out["pm"] = read_presentmon(run_dir / "presentmon.csv")
    # a frm row only counts when a squad update in it is itself expensive: an ordinary
    # tick can put a 0.3 ms squad update on top of a frame that is slow for another reason
    out["frames"] = [f for f in out["frames"] if f["worst_update_ms"] >= 1.0]
    fc = out["first_cb"]
    early = fc.get(1, 0) + fc.get(2, 0)       # later windows: squads spawned in play
    if not early:
        out["mode"] = "? (no profiler data)"
    elif fc.get(2, 0) < 0.05 * early:
        out["mode"] = "A (swept at load)"
    elif out["frames"]:
        out["mode"] = "B (burst in play)"
    else:
        out["mode"] = "B (by first_update windows; this profiler build has no frm squad rows)"
    return out


def print_run(d: dict) -> None:
    fr = d["frames"]
    print(f"- {d['run'][:15]} arm={d['arm']:<8} mode {d['mode']}; squad_on_first_update per window "
          f"{dict(sorted(d['first_cb'].items()))}")
    if d["hit"]:
        h = d["hit"]
        print(f"    worst single update {h['max_ms']:.2f} ms; first over the floor at t={h['first_t']} "
              f"frame {h['first_frame']}")
    if fr:
        ms = sorted(f["ms"] for f in fr)
        ts = [f["t"] for f in fr]
        gaps = sorted(b - a for a, b in zip(ts, ts[1:]))
        print(f"    profiler: {len(fr)} burst frames >= {FLOOR_MS:.0f} ms, t={ts[0]}..{ts[-1]} ms, "
              f"median gap {gaps[len(gaps) // 2] if gaps else 0} ms, frame ms min/med/max "
              f"{ms[0]:.0f}/{ms[len(ms) // 2]:.0f}/{ms[-1]:.0f}, >=20: {sum(m >= 20 for m in ms)}, "
              f">=30: {sum(m >= 30 for m in ms)}, script share "
              f"{sum(f['script_ms'] for f in fr) / sum(ms) * 100:.0f}%, callbacks "
              f"{sum(f['cb_ms'] for f in fr) / len(fr):.2f} ms/frame, worst update in them "
              f"{max(f['worst_update_ms'] for f in fr):.2f} ms, dGC {sum(f['gc_kb'] for f in fr) / len(fr):.0f} kB/frame")
    else:
        print("    profiler: no slow frame led by sim_squad_scripted.update")
    pm = d["pm"]
    if pm:
        tr = pm["train"]
        worst = max((m for _, m in tr), default=0.0)
        print(f"    presentmon: {pm['frames']} frames over {pm['span_s']:.0f} s (warmup_s={d.get('warmup_s')}); "
              f"{pm['slow_first_90s']} frames >= {FLOOR_MS:.0f} ms in the first 90 s, {len(tr)} of them in a "
              f"~455 ms train, worst {worst:.1f} ms")
    for line in d["stagger"]:
        print(f"    {line}")


def runs_of_queue(qid: str) -> list[Path]:
    for state in ("done", "running", "pending", "held", "failed"):
        for p in (QUEUE / state).glob(f"{qid}*.json"):
            item = json.loads(p.read_text(encoding="utf-8"))
            return [RUNS / r for r in (item.get("result") or {}).get("run_dirs", [])]
    raise SystemExit(f"no queue item {qid}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--queue", action="append", default=[])
    ap.add_argument("--run", action="append", default=[])
    a = ap.parse_args(argv)
    dirs = []
    for q in a.queue:
        dirs += runs_of_queue(q)
    for r in a.run:
        p = Path(r)
        dirs.append(p if p.is_dir() else RUNS / r)
    if not dirs:
        ap.error("give --queue or --run")
    rows = [describe(d) for d in dirs if d.is_dir()]
    for d in rows:
        print_run(d)
    print()
    for arm in sorted({d["arm"] for d in rows}):
        mine = [d for d in rows if d["arm"] == arm]
        burst = [d for d in mine if d["mode"].startswith("B")]
        worst = max((f["ms"] for d in burst for f in d["frames"]), default=0.0)
        print(f"{arm}: {len(burst)} of {len(mine)} loads with the burst; "
              f"burst frames per such load {[len(d['frames']) for d in burst]}; worst {worst:.0f} ms")
    return 0


if __name__ == "__main__":
    sys.exit(main())
