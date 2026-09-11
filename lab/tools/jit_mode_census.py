"""Corpus census of the I-013 JIT-mode classification.

Answers the three questions the beam asked:
  * of the per-frame bodies in the enabled GAMMA corpus, how many run
    compiled / mixed / interpreted;
  * which abort reasons dominate, by count;
  * (I-002) how many `table_insert_append` sites sit in a body that compiles,
    where the rewrite buys ~nothing, versus one that does not.

    py -3.12 lab/tools/jit_mode_census.py --corpus C:\\...\\extracted\\gamma
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def census_one(path_str):
    from ast_analyzer import ASTAnalyzer

    path = Path(path_str)
    analyzer = ASTAnalyzer()
    try:
        findings = analyzer.analyze_file(path)
    except Exception:
        return None

    modes = Counter()
    reasons = Counter()
    for cb in analyzer.per_frame_callbacks:
        info = analyzer.jit_modes.get(id(cb.scope))
        if info is None:
            continue
        modes[info.mode] += 1
        for site in info.sites:
            reasons[site.reason] += 1

    # every function body, not only per-frame ones
    all_modes = Counter(info.mode for info in analyzer.jit_modes.values())

    inserts = Counter()
    globals_ = Counter()
    for f in findings:
        if f.pattern_name == "table_insert_append":
            inserts[f.details.get("jit_mode", "unknown")] += 1
        elif f.pattern_name == "uncached_globals_summary":
            globals_[f.details.get("jit_mode", "unknown")] += 1

    # the other agents' patterns do not carry details['jit_mode'], so look the
    # mode up by which function body the finding's line falls inside
    others = {}
    for pattern in ("vector_alloc_in_loop", "string_concat_in_loop",
                    "string_find_plain", "distance_to_comparison"):
        others[pattern] = Counter()
    for f in findings:
        if f.pattern_name not in others:
            continue
        others[f.pattern_name][_mode_at_line(analyzer, f.line_num)] += 1
    return modes, reasons, all_modes, inserts, globals_, others


def _mode_at_line(analyzer, line):
    """Mode of the innermost function body containing `line`."""
    best, best_span = "unknown", None
    for scope in analyzer.scopes:
        if scope.scope_type != "function":
            continue
        if not (scope.start_line <= line <= scope.end_line):
            continue
        span = scope.end_line - scope.start_line
        if best_span is None or span < best_span:
            info = analyzer.jit_modes.get(id(scope))
            if info is not None:
                best, best_span = info.mode, span
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    root = Path(args.corpus)
    files = sorted(root.rglob("*.script")) + sorted(root.rglob("*.lua"))
    print("%d files under %s\n" % (len(files), root))

    modes, reasons, all_modes = Counter(), Counter(), Counter()
    inserts, globals_ = Counter(), Counter()
    others = {}
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for res in pool.map(census_one, [str(f) for f in files], chunksize=16):
            if res is None:
                continue
            m, r, am, ins, gl, oth = res
            modes += m
            reasons += r
            all_modes += am
            inserts += ins
            globals_ += gl
            for pattern, counter in oth.items():
                others.setdefault(pattern, Counter())
                others[pattern] += counter

    def table(title, counter):
        total = sum(counter.values())
        print(title, "(total %d)" % total)
        for key, n in counter.most_common():
            print("  %-58s %6d  %5.1f%%" % (key, n, 100.0 * n / max(total, 1)))
        print()

    table("per-frame bodies by JIT mode", modes)
    table("ALL function bodies by JIT mode", all_modes)
    table("abort reasons inside per-frame bodies, by count", reasons)
    table("table_insert_append sites by the mode of their body (I-002)", inserts)
    table("uncached_globals_summary findings by mode", globals_)
    for pattern in sorted(others):
        table("%s findings by mode" % pattern, others[pattern])


if __name__ == "__main__":
    main()
