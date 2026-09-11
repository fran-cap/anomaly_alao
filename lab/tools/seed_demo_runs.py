#!/usr/bin/env python
"""Create demo dry-runs so the dashboard has data before the first real run.

    py -3.12 tools/seed_demo_runs.py            # 3 runs
    py -3.12 tools/seed_demo_runs.py --count 5
    py -3.12 tools/seed_demo_runs.py --clean    # remove previous demo runs first

Everything written here is synthetic and marked ``"demo": true`` in the run
manifest, so real results are never confused with seeded ones.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "framework"))

from aalo import config as _config  # noqa: E402
from aalo import ideas as _ideas  # noqa: E402
from aalo import runner as _runner  # noqa: E402

# Declared on demo variant runs so the dashboard has a config_diff to render.
# Nothing is written to user.ltx: these runs are synthetic.
DEMO_DIFF = {"r2_sun_quality": ["st_opt_medium", "st_opt_low"]}

DEMO_IDEAS = [
    dict(
        title="Lower sun shadow quality",
        category="render",
        hypothesis="Sun shadow cascades cost more than they look like they do outdoors.",
        change="user.ltx: r2_sun_quality st_opt_low, r2_sun_tsm off",
        measure="fps_avg and fps_1pct_low outdoors in Garbage",
        expected_gain="med",
        risk="low",
    ),
    dict(
        title="Disable the prefetcher mod",
        category="mods",
        hypothesis="Prefetching stalls the main thread during level load.",
        change="modlist: disable Meatchunk's prefetcher for G.A.M.M.A",
        measure="load_time_s from the engine log",
        expected_gain="low",
        risk="low",
    ),
    dict(
        title="Trim A-Life online radius",
        category="alife",
        hypothesis="Fewer online NPCs means fewer script ticks, raising 1% lows.",
        change="alife config: smaller switch distance",
        measure="fps_1pct_low and frametime_p99_ms while a firefight is nearby",
        expected_gain="high",
        risk="med",
    ),
]


def seed_ideas(cfg) -> list:
    """Return ids to attach demo runs to.

    A real pool already exists most of the time (the beam agent owns it), so the
    placeholder ideas below are only added when the pool is empty.
    """
    pool = _ideas.IdeaPool.load(cfg=cfg)
    if pool.ideas:
        return [i["id"] for i in pool.ideas[: len(DEMO_IDEAS)]]
    for spec in DEMO_IDEAS:
        pool.add(**spec, notes="seeded by tools/seed_demo_runs.py")
    pool.save()
    return [i["id"] for i in pool.ideas]


def clean(cfg) -> int:
    removed = 0
    for d in sorted(cfg.runs_dir.glob("*")):
        m = d / "manifest.json"
        if not m.is_file():
            continue
        try:
            data = json.loads(m.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if data.get("demo"):
            shutil.rmtree(d)
            removed += 1
    return removed


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="seed demo runs for the dashboard")
    ap.add_argument("--count", type=int, default=3)
    ap.add_argument("--clean", action="store_true", help="delete earlier demo runs first")
    ap.add_argument("--duration", type=float, default=30.0)
    args = ap.parse_args(argv)

    cfg = _config.get()
    cfg.ensure_data_dirs()
    if args.clean:
        print(f"removed {clean(cfg)} earlier demo run(s)")

    idea_ids = seed_ideas(cfg)[: args.count] or [None]
    total = 0
    for n, idea in enumerate(idea_ids):
        for arm in ("baseline", "variant"):
            # The dashboard reads the arm from the first word of the notes, and
            # a baseline's notes must never contain the word "variant".
            notes = f"{arm} synthetic demo data, not a real measurement"
            run = _runner.run_once(
                cfg=cfg,
                idea_id=idea,
                slug=f"demo-{n + 1}-{arm}",
                dry_run=True,
                duration_s=args.duration,
                notes=notes,
                # Declared, not applied: a demo run must not touch user.ltx.
                config_diff={} if arm == "baseline" else dict(DEMO_DIFF),
            )
            run.manifest["demo"] = True
            run.manifest["arm"] = arm
            run.write_manifest()
            met = run.metrics
            total += 1
            print(f"{run.run_id}  idea={idea}  arm={arm}  fps_avg={met['fps_avg']}  1%low={met['fps_1pct_low']}")
    print(f"\n{total} demo run(s) in {cfg.runs_dir} ({len(idea_ids)} idea(s), baseline + variant each)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
