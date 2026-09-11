"""``py -3.12 -m aalo`` - command line for the lab.

Run it from ``lab/framework`` (or with that directory on PYTHONPATH)::

    py -3.12 -m aalo paths
    py -3.12 -m aalo mo2 list --enabled
    py -3.12 -m aalo snapshot take --label before-tuning
    py -3.12 -m aalo run --dry-run --idea I-001
    py -3.12 -m aalo ideas list
    py -3.12 -m aalo log parse <file>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__, config as _config
from . import ideas as _ideas
from . import ltx as _ltx
from . import metrics as _metrics
from . import mo2 as _mo2
from . import runner as _runner
from . import snapshot as _snapshot
from . import xraylog as _xraylog


def _print_json(obj) -> None:
    print(json.dumps(obj, indent=2, default=str))


# -- commands ---------------------------------------------------------------


def cmd_paths(args) -> int:
    cfg = _config.get()
    data = cfg.as_dict()
    data["config_file"] = str(cfg.source)
    # user.ltx moves into the profile once MO2 has run the game with
    # LocalSettings=true, so show which file is actually authoritative.
    data["effective_user_ltx"] = str(cfg.effective_user_ltx())
    data["user_ltx_is_profile_local"] = cfg.user_ltx_is_profile_local()
    data["exists"] = {
        "game_root": cfg.game_root.is_dir(),
        "anomaly": cfg.anomaly.is_dir(),
        "mo2_root": cfg.mo2_root.is_dir(),
        "mo2_exe": cfg.mo2_exe.is_file(),
        "game_exe": cfg.game_exe.is_file(),
        "user_ltx": cfg.user_ltx.is_file(),
        "logs_dir": cfg.logs_dir.is_dir(),
        "data": cfg.data.is_dir(),
    }
    data["presentmon"] = str(_metrics.find_presentmon() or "")
    if args.json:
        _print_json(data)
        return 0
    for k, v in data.items():
        if k == "exists":
            continue
        print(f"{k:<16} {v}")
    print("exists:")
    for k, v in data["exists"].items():
        print(f"  {'OK ' if v else 'MISSING'} {k}")
    return 0


def cmd_mo2(args) -> int:
    cfg = _config.get()
    m = _mo2.MO2(cfg)
    if args.mo2_cmd == "list":
        ml = m.modlist(args.profile)
        entries = ml.mods(include_separators=args.separators)
        if args.enabled:
            entries = [e for e in entries if e.enabled]
        if args.disabled:
            entries = [e for e in entries if not e.enabled]
        if args.filter:
            needle = args.filter.lower()
            entries = [e for e in entries if needle in e.name.lower()]
        if args.json:
            _print_json([{"name": e.name, "enabled": e.enabled, "separator": e.is_separator} for e in entries])
            return 0
        for e in entries:
            print(f"{'+' if e.enabled else '-'} {e.name}")
        print(f"\n{len(entries)} shown; profile {args.profile or cfg.profile}: "
              f"{len(ml.mods())} mods, {len(ml.enabled_mods())} enabled")
        return 0
    if args.mo2_cmd == "profiles":
        for p in m.profiles():
            mark = "*" if p == m.selected_profile else " "
            print(f"{mark} {p}")
        return 0
    if args.mo2_cmd == "exes":
        for e in m.executables():
            print(f"{e.index:>2}  {e.title:<24} {e.binary}")
        return 0
    if args.mo2_cmd == "command":
        print(" ".join(f'"{c}"' if " " in c else c for c in m.command_for(profile=args.profile)))
        return 0
    return 1


def cmd_snapshot(args) -> int:
    cfg = _config.get()
    if args.snap_cmd in (None, "take"):
        d = _snapshot.take(label=args.label, profile=args.profile, cfg=cfg)
        print(f"snapshot {d.name} -> {d}")
        return 0
    if args.snap_cmd == "list":
        for m in _snapshot.list_snapshots(cfg):
            print(f"{m['snapshot_id']}  profile={m.get('profile')}  files={len(m.get('files', []))}  {m.get('label') or ''}")
        return 0
    if args.snap_cmd == "restore":
        restored = _snapshot.restore(args.snapshot, cfg=cfg, dry_run=args.dry_run)
        verb = "would restore" if args.dry_run else "restored"
        print(f"{verb} {len(restored)} file(s)")
        for r in restored[:20]:
            print(f"  {r}")
        return 0
    if args.snap_cmd == "diff":
        _print_json(_snapshot.diff_user_ltx(args.before, args.after))
        return 0
    return 1


def cmd_run(args) -> int:
    cfg = _config.get()
    if args.experiment:
        exp = _runner.load_experiment(args.experiment, cfg)
        if args.save is not None:
            exp.save = args.save
        if args.warmup is not None:
            exp.warmup_s = args.warmup
        if args.duration is not None:
            exp.duration_s = args.duration
        runs = _runner.run_experiment(exp, cfg=cfg, dry_run=args.dry_run, repeats=args.repeats)
        for r in runs:
            met = r.metrics or {}
            print(f"{r.run_id}  arm={r.manifest.get('arm')}  status={r.manifest['status']}  fps_avg={met.get('fps_avg')}")
        return 0
    changes = dict(kv.split("=", 1) for kv in (args.set or []))
    toggles = {}
    for spec in args.enable or []:
        toggles[spec] = True
    for spec in args.disable or []:
        toggles[spec] = False
    run = _runner.run_once(
        cfg=cfg,
        idea_id=args.idea,
        slug=args.slug or (args.idea or "run"),
        dry_run=args.dry_run,
        duration_s=args.duration,
        timeout_s=args.timeout,
        warmup_s=args.warmup,
        user_ltx_changes=changes or None,
        mod_toggles=toggles or None,
        notes=args.notes or "",
        profile=args.profile,
        autoload_save=args.save,
    )
    print(f"run {run.run_id} -> {run.dir}")
    _print_json(run.metrics)
    return 0


def cmd_runs(args) -> int:
    rows = _runner.list_runs()
    if args.json:
        _print_json(rows)
        return 0
    for r in rows[: args.limit]:
        met = r.get("metrics") or {}
        print(f"{r['run_id']:<40} {r.get('status','?'):<8} idea={r.get('idea_id') or '-':<7} "
              f"fps_avg={met.get('fps_avg')} p99={met.get('frametime_p99_ms')}")
    print(f"\n{len(rows)} run(s)")
    return 0


def cmd_ideas(args) -> int:
    cfg = _config.get()
    pool = _ideas.IdeaPool.load(cfg=cfg)
    if args.ideas_cmd == "list":
        if args.json:
            _print_json(pool.ideas)
            return 0
        for i in pool.ideas:
            if args.status and i.get("status") != args.status:
                continue
            score = i.get("score")
            print(f"{i['id']:<7} g{i.get('generation',0)} {i.get('status','?'):<8} "
                  f"score={score if score is not None else '-':<6} {i.get('category','?'):<7} {i['title']}")
        print(f"\n{len(pool)} idea(s), generations <= {pool.max_generation}")
        return 0
    if args.ideas_cmd == "add":
        idea = pool.add(
            args.title,
            category=args.category,
            hypothesis=args.hypothesis or "",
            change=args.change or "",
            measure=args.measure or "",
            expected_gain=args.gain,
            risk=args.risk,
            parent=args.parent,
            generation=args.generation,
            notes=args.notes or "",
        )
        pool.save()
        print(f"added {idea['id']}: {idea['title']}")
        return 0
    if args.ideas_cmd == "score":
        idea = pool.score(args.id, args.value, notes=args.notes)
        pool.save()
        print(f"{idea['id']} score={idea['score']}")
        return 0
    if args.ideas_cmd == "status":
        idea = pool.set_status(args.id, args.value)
        pool.save()
        print(f"{idea['id']} status={idea['status']}")
        return 0
    if args.ideas_cmd == "prune":
        idea = pool.prune(args.id, reason=args.reason or "")
        pool.save()
        print(f"{idea['id']} pruned")
        return 0
    if args.ideas_cmd == "beam":
        result = pool.beam(k=args.keep, generation=args.generation, min_score=args.min_score)
        if args.spawn:
            children = pool.spawn(per_parent=args.spawn, generation=result["generation"])
            result["spawned"] = [c["id"] for c in children]
        pool.save()
        _print_json(result)
        return 0
    return 1


def cmd_log(args) -> int:
    path = Path(args.file) if args.file else _xraylog.newest_log()
    if path is None or not Path(path).is_file():
        print("no log found", file=sys.stderr)
        return 2
    log = _xraylog.load(path)
    if args.json:
        _print_json(log.summary())
        return 0
    s = log.summary()
    print(f"file          {s['path']}")
    print(f"lines         {s['lines']}")
    print(f"crashed       {s['crashed']}")
    print(f"load_time_s   {s['load_time_s']}")
    print(f"levels        {', '.join(s['levels']) or '-'}")
    print(f"warnings      {s['warning_count']}")
    print(f"errors        {s['error_count']}")
    for e in s["errors"][:10]:
        print(f"  ! {e}")
    if args.warnings:
        for w in log.warning_texts(args.warnings):
            print(f"  ~ {w}")
    return 0


def cmd_ltx(args) -> int:
    doc = _ltx.load_auto(args.file)
    if isinstance(doc, _ltx.UserLtx):
        data = doc.to_dict()
    else:
        data = {(sec or ""): entries for sec, entries in doc.to_dict().items()}
    if args.key:
        needle = args.key.lower()
        if isinstance(doc, _ltx.UserLtx):
            data = {k: v for k, v in data.items() if needle in k.lower()}
        else:
            data = {s: {k: v for k, v in e.items() if needle in k.lower()} for s, e in data.items()}
            data = {s: e for s, e in data.items() if e}
    _print_json(data)
    return 0


# -- parser -----------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="aalo", description="Anomaly A-Life Optimization lab")
    p.add_argument("--version", action="version", version=f"aalo {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("paths", help="show resolved paths and what exists")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_paths)

    sp = sub.add_parser("mo2", help="inspect the Mod Organizer 2 instance")
    msub = sp.add_subparsers(dest="mo2_cmd", required=True)
    ml = msub.add_parser("list", help="list mods in a profile")
    ml.add_argument("--profile")
    ml.add_argument("--enabled", action="store_true")
    ml.add_argument("--disabled", action="store_true")
    ml.add_argument("--separators", action="store_true", help="include separators")
    ml.add_argument("--filter")
    ml.add_argument("--json", action="store_true")
    msub.add_parser("profiles", help="list profiles")
    msub.add_parser("exes", help="list MO2 custom executables")
    mc = msub.add_parser("command", help="print the launch command")
    mc.add_argument("--profile")
    sp.set_defaults(func=cmd_mo2)

    sp = sub.add_parser("snapshot", help="snapshot/restore user.ltx and the MO2 profile")
    ssub = sp.add_subparsers(dest="snap_cmd")
    st = ssub.add_parser("take")
    st.add_argument("--label")
    st.add_argument("--profile")
    ssub.add_parser("list")
    sr = ssub.add_parser("restore")
    sr.add_argument("snapshot")
    sr.add_argument("--dry-run", action="store_true")
    sd = ssub.add_parser("diff")
    sd.add_argument("before")
    sd.add_argument("after")
    sp.set_defaults(func=cmd_snapshot, snap_cmd=None, label=None, profile=None)

    sp = sub.add_parser("run", help="execute a run or an experiment")
    sp.add_argument("--dry-run", action="store_true", help="do everything except launch the game")
    sp.add_argument("--idea")
    sp.add_argument("--slug")
    sp.add_argument("--notes")
    sp.add_argument("--profile")
    sp.add_argument("--duration", type=float, help="seconds of gameplay to measure")
    sp.add_argument("--warmup", type=float,
                    help="seconds to wait after the level loads before sampling (default 30)")
    sp.add_argument("--timeout", type=float)
    sp.add_argument("--set", action="append", metavar="KEY=VALUE", help="user.ltx console command to set")
    sp.add_argument("--enable", action="append", metavar="MOD")
    sp.add_argument("--disable", action="append", metavar="MOD")
    sp.add_argument("--experiment", help="experiment name or .toml path")
    sp.add_argument("--repeats", type=int)
    sp.add_argument("--save", metavar="NAME",
                    help="auto-load this save on launch (no main menu, keypress_on_start off); "
                         "--save \"\" forces a manual load; default: aalo.toml [run] autoload_save")
    sp.set_defaults(func=cmd_run)

    sp = sub.add_parser("runs", help="list runs")
    sp.add_argument("--limit", type=int, default=20)
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_runs)

    sp = sub.add_parser("ideas", help="manage the idea pool")
    isub = sp.add_subparsers(dest="ideas_cmd", required=True)
    il = isub.add_parser("list")
    il.add_argument("--status", choices=_ideas.STATUSES)
    il.add_argument("--json", action="store_true")
    ia = isub.add_parser("add")
    ia.add_argument("title")
    ia.add_argument("--category", choices=_ideas.CATEGORIES, default="other")
    ia.add_argument("--hypothesis")
    ia.add_argument("--change")
    ia.add_argument("--measure")
    ia.add_argument("--gain", choices=_ideas.LEVELS, default="med")
    ia.add_argument("--risk", choices=_ideas.LEVELS, default="low")
    ia.add_argument("--parent")
    ia.add_argument("--generation", type=int, default=0)
    ia.add_argument("--notes")
    isc = isub.add_parser("score")
    isc.add_argument("id")
    isc.add_argument("value", type=float)
    isc.add_argument("--notes")
    ist = isub.add_parser("status")
    ist.add_argument("id")
    ist.add_argument("value", choices=_ideas.STATUSES)
    ip = isub.add_parser("prune")
    ip.add_argument("id")
    ip.add_argument("--reason")
    ib = isub.add_parser("beam")
    ib.add_argument("--keep", type=int, default=3)
    ib.add_argument("--generation", type=int)
    ib.add_argument("--min-score", type=float)
    ib.add_argument("--spawn", type=int, default=0, help="children per surviving parent")
    sp.set_defaults(func=cmd_ideas)

    sp = sub.add_parser("log", help="parse an engine log")
    lsub = sp.add_subparsers(dest="log_cmd", required=True)
    lp = lsub.add_parser("parse")
    lp.add_argument("file", nargs="?", help="defaults to the newest xray log")
    lp.add_argument("--json", action="store_true")
    lp.add_argument("--warnings", type=int, help="also print N warnings")
    sp.set_defaults(func=cmd_log)

    sp = sub.add_parser("ltx", help="dump an ltx or user.ltx as JSON")
    sp.add_argument("file")
    sp.add_argument("--key", help="substring filter on keys")
    sp.set_defaults(func=cmd_ltx)

    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
