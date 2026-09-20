#!/usr/bin/env python3
"""
Run corpus_run.py once per fix-flag combination and print one gate table.

  py -3.12 tools/corpus_matrix.py --corpus C:\\code\\GIT\\anomaly_alao\\extracted\\gamma \\
      --corpus-name gamma-0.9.4 --label i052

Why this exists (I-052): idempotence (G5) is a property of a flag
*combination*, not of ALAO in the abstract. Every gate run up to gen-3 used
plain `--fix`, and both defects I-052 fixed only showed themselves once a
second flag was on - `--fix --fix-debug` on sr_monster.script and
`--fix --fix-nil` on 24 GAMMA files. One run per combination is the only way
the gate can see that class of bug, so this wrapper makes it one command.

The default set is the four combinations the README documents as normal usage;
`--combos` takes a comma-separated subset (or `all` for the sweep that also
covers --fix-yellow / --experimental / --remove-dead-code).

Everything else is handed straight to corpus_run.py, which still owns the run
ids, the working copies and the results.json files; this only sequences them
(never in parallel - G6 timing is meaningless when two 8-worker runs overlap)
and summarises. Hold the `corpus` lock around the whole thing:

  py -3.12 lab\\coord\\coord.py run corpus --ttl 3600 -- py -3.12 tools\\corpus_matrix.py ...
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CORPUS_RUN = REPO_ROOT / "tools" / "corpus_run.py"

# name -> fix flags. "fix" is the plain baseline every earlier gate ran.
COMBOS = {
    "fix": "--fix",
    "fix-debug": "--fix --fix-debug",
    "fix-nil": "--fix --fix-nil",
    "fix-debug-nil": "--fix --fix-debug --fix-nil",
    "fix-yellow": "--fix --fix-yellow",
    "fix-experimental": "--fix --experimental",
    "fix-dead-code": "--fix --remove-dead-code",
    "everything": "--fix --fix-debug --fix-nil --fix-yellow --experimental --remove-dead-code",
}
DEFAULT_COMBOS = ["fix", "fix-debug", "fix-nil", "fix-debug-nil"]


def run_one(name, flags, args):
    label = f"{args.label}-{name}" if args.label else name
    cmd = [sys.executable, str(CORPUS_RUN),
           "--corpus", str(args.corpus),
           "--label", label,
           f"--fix-flags={flags}"]
    if args.corpus_name:
        cmd += ["--corpus-name", args.corpus_name]
    if args.jobs:
        cmd += ["--jobs", str(args.jobs)]
    if args.keep_work:
        cmd += ["--keep-work"]
    if args.notes:
        cmd += ["--notes", args.notes]
    if args.out_root:
        cmd += ["--out-root", str(args.out_root)]
    if args.extra:
        cmd += args.extra

    print(f"\n{'#' * 70}\n# {name}: {flags}\n{'#' * 70}", flush=True)
    proc = subprocess.run(cmd, cwd=str(REPO_ROOT))
    run_id = None
    out_root = Path(args.out_root) if args.out_root else None
    if out_root is None:
        # same default corpus_run.py uses; re-derive it rather than duplicate
        sys.path.insert(0, str(REPO_ROOT / "tools"))
        from corpus_run import DEFAULT_LAB  # noqa: E402
        out_root = DEFAULT_LAB / "data" / "corpus"
    # newest run dir whose label matches
    cands = sorted((p for p in out_root.glob(f"*-{label}") if p.is_dir()),
                   key=lambda p: p.name)
    if cands:
        run_id = cands[-1].name
    return run_id, proc.returncode, out_root


def gate_row(out_root, run_id):
    if not run_id:
        return None
    rp = Path(out_root) / run_id / "results.json"
    if not rp.exists():
        return None
    r = json.loads(rp.read_text(encoding="utf-8"))
    return {
        "run_id": run_id,
        "files_modified": r.get("files_modified"),
        "edits": r.get("edits_applied"),
        "g4_compile": len(r.get("compile_failures_after_fix") or []),
        "g5_idem": len(r.get("idempotence_violations") or []),
        "g9_captures": len(r.get("captures") or []),
        "parse_failures": len(r.get("parse_failures") or []),
        "timeouts": len(r.get("timeouts") or []),
        "crashes": len(r.get("crashes") or []),
        "findings": sum((r.get("findings_by_pattern") or {}).values()),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus", type=Path, required=True)
    ap.add_argument("--corpus-name", default=None)
    ap.add_argument("--label", default=None, help="slug prefix; each run gets -<combo> appended")
    ap.add_argument("--combos", default=",".join(DEFAULT_COMBOS),
                    help='comma-separated combo names, or "all" (default: %(default)s). '
                         f'known: {", ".join(COMBOS)}')
    ap.add_argument("--jobs", "-j", type=int, default=None)
    ap.add_argument("--keep-work", action="store_true")
    ap.add_argument("--notes", default=None)
    ap.add_argument("--out-root", default=None, help="passed through to corpus_run.py's default if unset")
    ap.add_argument("--summary-json", type=Path, default=None,
                    help="write the gate table as JSON here too")
    ap.add_argument("extra", nargs="*", help="extra args forwarded to corpus_run.py")
    args = ap.parse_args()

    names = list(COMBOS) if args.combos == "all" else [
        c.strip() for c in args.combos.split(",") if c.strip()]
    unknown = [n for n in names if n not in COMBOS]
    if unknown:
        ap.error(f"unknown combo(s): {', '.join(unknown)}; known: {', '.join(COMBOS)}")

    rows, out_root = [], None
    failed = 0
    for name in names:
        run_id, rc, out_root = run_one(name, COMBOS[name], args)
        if rc != 0:
            failed += 1
        row = gate_row(out_root, run_id) or {"run_id": run_id or "?"}
        row["combo"] = name
        row["flags"] = COMBOS[name]
        row["returncode"] = rc
        rows.append(row)

    print("\n" + "=" * 118)
    print(f"{'combo':<17}{'run_id':<34}{'files':>7}{'edits':>8}{'G4':>5}{'G5':>5}{'G9':>5}"
          f"{'parse':>7}{'timeo':>7}{'crash':>7}{'findings':>10}")
    print("-" * 118)
    bad = []
    for row in rows:
        print(f"{row['combo']:<17}{str(row.get('run_id')):<34}"
              f"{str(row.get('files_modified')):>7}{str(row.get('edits')):>8}"
              f"{str(row.get('g4_compile')):>5}{str(row.get('g5_idem')):>5}"
              f"{str(row.get('g9_captures')):>5}{str(row.get('parse_failures')):>7}"
              f"{str(row.get('timeouts')):>7}{str(row.get('crashes')):>7}"
              f"{str(row.get('findings')):>10}")
        if row.get("g4_compile") or row.get("g5_idem") or row.get("g9_captures") \
                or row.get("crashes") or row.get("returncode"):
            bad.append(row["combo"])
    print("=" * 118)
    print("GATES CLEAN" if not bad else f"GATE FAILURES in: {', '.join(bad)}")

    if args.summary_json:
        args.summary_json.write_text(json.dumps(rows, indent=2), encoding="utf-8")
        print(f"written: {args.summary_json}")

    return 1 if (bad or failed) else 0


if __name__ == "__main__":
    sys.exit(main())
