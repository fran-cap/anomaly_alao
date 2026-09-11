#!/usr/bin/env python3
"""
Diff two corpus_run.py runs and print a markdown report.

  py -3.12 tools/corpus_compare.py --latest
  py -3.12 tools/corpus_compare.py 20260910-221500-vanilla-fix 20260910-224500-vanilla-fix
  py -3.12 tools/corpus_compare.py <base> <new> --out D:\\...\\reports\\delta.md

Shows per-pattern finding deltas, new/fixed parse failures, timeouts, crashes,
compile failures, idempotence violations, and the timing change.  `--latest`
picks the two most recent run dirs; add `--corpus gamma-0.9.4` to compare only
runs over the same corpus (otherwise you can end up diffing vanilla against
gamma, which is meaningless).
"""

import argparse
import json
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parent.parent / "lab" / "data" / "corpus"  # <repo>/lab/data/corpus


def load_run(root: Path, run_id: str):
    d = root / run_id
    if not d.is_dir():
        raise SystemExit(f"run not found: {d}")
    res = json.loads((d / "results.json").read_text(encoding="utf-8"))
    man_path = d / "manifest.json"
    man = json.loads(man_path.read_text(encoding="utf-8")) if man_path.is_file() else {}
    return man, res


def list_runs(root: Path, corpus=None):
    runs = []
    for d in sorted(root.iterdir() if root.is_dir() else []):
        if not (d / "results.json").is_file():
            continue
        man = {}
        if (d / "manifest.json").is_file():
            try:
                man = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
            except Exception:
                pass
        if corpus and man.get("corpus") != corpus:
            continue
        runs.append(d.name)
    return runs


def _files(entries):
    """parse_failures/crashes are dicts with a 'file'; timeouts are bare strings."""
    out = set()
    for e in entries or []:
        out.add(e["file"] if isinstance(e, dict) else e)
    return out


def delta_str(a, b):
    d = b - a
    if d == 0:
        return "0"
    return f"{d:+d}"


def fmt_secs(v):
    return "-" if v is None else f"{v:.1f}s"


def compare(base_id, new_id, base, new, limit):
    bman, bres = base
    nman, nres = new
    lines = []
    A = lines.append

    A(f"# Corpus comparison: `{base_id}` -> `{new_id}`\n")
    A("| | base | new |")
    A("|---|---|---|")
    A(f"| run_id | `{base_id}` | `{new_id}` |")
    A(f"| corpus | {bman.get('corpus','?')} | {nman.get('corpus','?')} |")
    A(f"| corpus_files | {bman.get('corpus_files','?')} | {nman.get('corpus_files','?')} |")
    A(f"| alao_commit | `{str(bman.get('alao_commit'))[:10]}` (dirty={bman.get('alao_dirty')}) | "
      f"`{str(nman.get('alao_commit'))[:10]}` (dirty={nman.get('alao_dirty')}) |")
    A(f"| flags | `{' '.join(map(str, bman.get('flags', [])))}` | `{' '.join(map(str, nman.get('flags', [])))}` |")
    A(f"| status | {bman.get('status','?')} | {nman.get('status','?')} |")
    A("")

    if bman.get("corpus") != nman.get("corpus"):
        A("> **Warning:** these runs used different corpora; the deltas below are not comparable.\n")

    A("## Headline numbers\n")
    A("| metric | base | new | delta |")
    A("|---|---:|---:|---:|")
    rows = [
        ("analyze_s", bres.get("analyze_s"), nres.get("analyze_s"), "secs"),
        ("fix_s", bres.get("fix_s"), nres.get("fix_s"), "secs"),
        ("findings total", sum(bres.get("findings_by_pattern", {}).values()),
         sum(nres.get("findings_by_pattern", {}).values()), "int"),
        ("parse_failures", len(bres.get("parse_failures") or []), len(nres.get("parse_failures") or []), "int"),
        ("timeouts", len(bres.get("timeouts") or []), len(nres.get("timeouts") or []), "int"),
        ("crashes", len(bres.get("crashes") or []), len(nres.get("crashes") or []), "int"),
        ("files_modified", bres.get("files_modified") or 0, nres.get("files_modified") or 0, "int"),
        ("edits_applied", bres.get("edits_applied"), nres.get("edits_applied"), "int"),
        ("compile_failures_after_fix", len(bres.get("compile_failures_after_fix") or []),
         len(nres.get("compile_failures_after_fix") or []), "int"),
        ("idempotence_violations", len(bres.get("idempotence_violations") or []),
         len(nres.get("idempotence_violations") or []), "int"),
    ]
    for name, b, n, kind in rows:
        if kind == "secs":
            d = "-" if (b is None or n is None) else f"{n - b:+.1f}s"
            A(f"| {name} | {fmt_secs(b)} | {fmt_secs(n)} | {d} |")
        else:
            d = "-" if (b is None or n is None) else delta_str(b, n)
            A(f"| {name} | {'-' if b is None else b} | {'-' if n is None else n} | {d} |")
    A("")

    A("## Findings by severity\n")
    A("| severity | base | new | delta |")
    A("|---|---:|---:|---:|")
    bs, ns = bres.get("findings_by_severity", {}), nres.get("findings_by_severity", {})
    for sev in sorted(set(bs) | set(ns)):
        A(f"| {sev} | {bs.get(sev,0)} | {ns.get(sev,0)} | {delta_str(bs.get(sev,0), ns.get(sev,0))} |")
    A("")

    A("## Findings by pattern\n")
    bp, np_ = bres.get("findings_by_pattern", {}), nres.get("findings_by_pattern", {})
    changed = [(p, bp.get(p, 0), np_.get(p, 0)) for p in set(bp) | set(np_)]
    changed = [c for c in changed if c[1] != c[2]]
    changed.sort(key=lambda c: -abs(c[2] - c[1]))
    if not changed:
        A("_No per-pattern changes._\n")
    else:
        A("| pattern | base | new | delta |")
        A("|---|---:|---:|---:|")
        for p, b, n in changed[:limit]:
            A(f"| `{p}` | {b} | {n} | {delta_str(b, n)} |")
        if len(changed) > limit:
            hidden = sum(n - b for _p, b, n in changed[limit:])
            A(f"\n_{len(changed) - limit} more patterns changed (raise `--limit`); "
              f"they account for {hidden:+d} of the findings delta._")
        A("")

    # I-031 housekeeping: the note in next-session.md said the severity totals
    # did not reconcile with the per-pattern deltas. The underlying numbers do
    # reconcile; what never added up is the *printed* pattern table, which lists
    # only changed patterns and only the top `--limit` of those. Say so here, and
    # shout if the stored totals ever really disagree.
    for _label, _res in (("base", bres), ("new", nres)):
        pat_total = sum((_res.get("findings_by_pattern") or {}).values())
        sev_total = sum((_res.get("findings_by_severity") or {}).values())
        if pat_total != sev_total:
            A(f"> **Warning:** {_label} run: findings_by_pattern sums to "
              f"{pat_total} but findings_by_severity sums to {sev_total}.\n")
    shown_delta = sum(n - b for _p, b, n in changed[:limit])
    total_delta = (sum((nres.get("findings_by_pattern") or {}).values())
                   - sum((bres.get("findings_by_pattern") or {}).values()))
    if changed and shown_delta != total_delta:
        A(f"_Pattern rows shown account for {shown_delta:+d} of the "
          f"{total_delta:+d} total findings delta; the rest is in patterns "
          f"below the --limit cut._\n")

    for title, key in (("Parse failures", "parse_failures"), ("Timeouts", "timeouts"),
                       ("Crashes", "crashes"), ("Compile failures after fix", "compile_failures_after_fix"),
                       ("Idempotence violations", "idempotence_violations")):
        b, n = _files(bres.get(key)), _files(nres.get(key))
        new_only, fixed = sorted(n - b), sorted(b - n)
        if not new_only and not fixed:
            continue
        A(f"## {title}\n")
        if new_only:
            A(f"**New ({len(new_only)}):**\n")
            for f in new_only[:limit]:
                A(f"- `{f}`")
            if len(new_only) > limit:
                A(f"- _...{len(new_only) - limit} more_")
            A("")
        if fixed:
            A(f"**Gone ({len(fixed)}):**\n")
            for f in fixed[:limit]:
                A(f"- `{f}`")
            if len(fixed) > limit:
                A(f"- _...{len(fixed) - limit} more_")
            A("")

    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="Compare two ALAO corpus runs.")
    ap.add_argument("runs", nargs="*", help="two run ids (base, new)")
    ap.add_argument("--latest", action="store_true", help="use the two most recent runs")
    ap.add_argument("--corpus", default=None, help="restrict --latest/--list to one corpus id")
    ap.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="data/corpus root")
    ap.add_argument("--list", action="store_true", help="list available run ids and exit")
    ap.add_argument("--limit", type=int, default=30, help="max rows per table section")
    ap.add_argument("--out", type=Path, default=None, help="also write the markdown here")
    args = ap.parse_args()

    if args.list:
        for r in list_runs(args.root, args.corpus):
            print(r)
        return

    if args.latest:
        runs = list_runs(args.root, args.corpus)
        if len(runs) < 2:
            raise SystemExit(f"need at least 2 runs in {args.root}, found {len(runs)}")
        base_id, new_id = runs[-2], runs[-1]
    elif len(args.runs) == 2:
        base_id, new_id = args.runs
    else:
        raise SystemExit("give two run ids or use --latest")

    md = compare(base_id, new_id, load_run(args.root, base_id), load_run(args.root, new_id), args.limit)
    print(md)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(md, encoding="utf-8")
        print(f"\nwritten: {args.out}")


if __name__ == "__main__":
    main()
