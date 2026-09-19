#!/usr/bin/env python3
"""
Run ALAO over a scratch corpus and record everything the lab contract asks for.

  py -3.12 tools/corpus_run.py --corpus extracted/vanilla --corpus-name vanilla-1.5.3
  py -3.12 tools/corpus_run.py --corpus extracted/gamma --corpus-name gamma-0.9.4 \
      --fix-flags "--fix --fix-debug --fix-nil" --jobs 8

What it does, in order:
  1. copies the corpus to a fresh working dir (the corpus itself is never touched)
  2. runs `stalker_lua_lint.py <work> --report <json>` as a subprocess, timing it
  3. reads the report JSON into findings_by_pattern / findings_by_severity
  4. attributes parse failures / timeouts / crashes per file (see the note below)
  5. optionally re-runs with the given --fix flags on the same working copy
  6. compile-checks every rewritten file with lupa's bundled LuaJIT 2.0
  7. copies the fixed tree, drops the .alao-bak files, fixes again -> idempotence
  8. G9 (I-046): re-runs the transformer over every original and asserts that no
     inserted `local` binds over a live outer name -> results["captures"]
  9. writes manifest.json + results.json + diffs/ into data/corpus/<run_id>/

Note on failure attribution: ALAO's JSON report contains only findings, and its
stdout prints just counts ("Files with parse errors: N") unless you pass -v,
which on a big corpus also dumps every finding.  So we re-run the repo's own
`analyze_file_worker` in-process (same timeout, same code path) purely to get
file + error text for each failure.  Counts from ALAO's stdout are kept
alongside as a cross-check in extra.stdout_counts.
"""

import argparse
import concurrent.futures
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from models import detect_file_encoding  # noqa: E402
from capture_gate import flags_from_cli, scan_paths  # noqa: E402

# The lab data is shared state: runs from every git worktree must land in the
# main checkout so corpus_compare can diff across agents. Override with
# ALAO_LAB or --out-root. Falls back to <this repo>/lab when the main
# checkout is not where we expect it (another machine).
_MAIN_LAB = Path(r"C:\code\GIT\anomaly_alao\lab")
DEFAULT_LAB = Path(os.environ.get("ALAO_LAB") or (_MAIN_LAB if _MAIN_LAB.is_dir() else REPO_ROOT / "lab"))
DEFAULT_INSTALL = Path(r"D:\GOG_Games\Gamma\S.T.A.L.K.E.R. GAMMA")
BAK_SUFFIX = ".alao-bak"
MAX_DIFFS = 50


# ---------------------------------------------------------------- helpers

def run_id_for(label: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-") or "run"
    return f"{datetime.now():%Y%m%d-%H%M%S}-{slug}"


def git_state(repo: Path):
    def git(*a):
        try:
            return subprocess.run(["git", *a], cwd=repo, capture_output=True,
                                  text=True, timeout=30).stdout.strip()
        except Exception:
            return ""
    return git("rev-parse", "HEAD") or None, bool(git("status", "--porcelain"))


def read_text_any(path: Path) -> str:
    """Read using ALAO's own encoding detection so diffs don't mangle CP1251."""
    try:
        return path.read_text(encoding=detect_file_encoding(path))
    except Exception:
        return path.read_text(encoding="latin-1")


def iter_scripts(root: Path):
    for ext in ("*.script", "*.lua"):
        yield from root.rglob(ext)


def bak_files(root: Path):
    return sorted(root.rglob("*" + BAK_SUFFIX))


def rel(root: Path, path: Path) -> str:
    try:
        return str(Path(path).relative_to(root)).replace("\\", "/")
    except ValueError:
        return str(path)


# ---------------------------------------------------------------- ALAO subprocess

STDOUT_PATTERNS = {
    "files_analyzed": r"Files analyzed:\s*(\d+)",
    "files_with_issues": r"Files with issues:\s*(\d+)",
    "files_skipped": r"Files skipped \(timeout/error\):\s*(\d+)",
    "parse_errors": r"Files with parse errors:\s*(\d+)",
    "files_modified": r"Files modified:\s*(\d+)",
    "edits_applied": r"Total edits applied:\s*(\d+)",
}


def parse_stdout_counts(text: str) -> dict:
    out = {}
    for key, pat in STDOUT_PATTERNS.items():
        m = re.search(pat, text)
        out[key] = int(m.group(1)) if m else None
    return out


def run_alao(work: Path, extra_args, timeout_s, log_path: Path):
    cmd = [sys.executable, str(REPO_ROOT / "stalker_lua_lint.py"), str(work), *extra_args]
    print(f"[alao] {' '.join(cmd[1:])}")
    t0 = time.perf_counter()
    proc = subprocess.run(cmd, cwd=str(REPO_ROOT), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout_s)
    wall = time.perf_counter() - t0
    log_path.write_text(
        f"$ {' '.join(cmd)}\n\n--- returncode {proc.returncode} in {wall:.1f}s ---\n"
        f"\n--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}\n",
        encoding="utf-8")
    return proc, wall


# ---------------------------------------------------------------- failure probe

def _probe_one(args_tuple):
    """Module-level so ProcessPoolExecutor can pickle it (same rule as ALAO)."""
    from stalker_lua_lint import analyze_file_worker
    return analyze_file_worker(args_tuple)


def failures_from_report(report_path: Path, work: Path):
    """Read per-file failures straight out of ALAO's JSON report (I-029).

    Returns (parse_failures, timeouts, crashes) or None when the report predates
    the failure keys, in which case the caller falls back to the probe pass.
    Paths in the report are absolute; they get relativised to the working copy
    so a run stays comparable across machines and worktrees.
    """
    if not report_path.is_file():
        return None
    try:
        data = json.loads(report_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if "parse_failures" not in data or "timeouts" not in data:
        return None

    def _rel(p):
        try:
            return rel(work, Path(p))
        except Exception:
            return str(p)

    parse_failures = [{"file": _rel(e["file"]), "error": e.get("error", "")}
                      for e in data.get("parse_failures") or []]
    timeouts = [_rel(p) for p in data.get("timeouts") or []]
    crashes = [{"file": _rel(e["file"]), "traceback": (e.get("traceback") or "")[-4000:]}
               for e in data.get("crashes") or []]
    return parse_failures, timeouts, crashes


def edits_from_report(report_path: Path):
    """The per-file edit accounting ALAO now publishes (I-029). None if absent."""
    if not report_path.is_file():
        return None
    try:
        data = json.loads(report_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data.get("edits_totals")


def compile_failures_from_report(report_path: Path, work: Path):
    """Rewrites ALAO refused to write because they did not compile (I-004)."""
    if not report_path.is_file():
        return None
    try:
        data = json.loads(report_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if "compile_failures" not in data:
        return None
    out = []
    for e in data.get("compile_failures") or []:
        try:
            f = rel(work, Path(e["file"]))
        except Exception:
            f = str(e.get("file"))
        out.append({"file": f, "error": e.get("error", "")})
    return out


def probe_failures(work: Path, timeout: float, cache_threshold: int, workers: int):
    """Re-run ALAO's per-file analyzer to attribute failures to concrete files."""
    items = [("probe", p, timeout, cache_threshold, False) for p in sorted(iter_scripts(work))]
    parse_failures, timeouts, crashes = [], [], []
    if not items:
        return parse_failures, timeouts, crashes
    print(f"[probe] attributing failures over {len(items)} files with {workers} workers...")
    with concurrent.futures.ProcessPoolExecutor(max_workers=workers) as ex:
        for i, (_mod, path, _findings, error) in enumerate(ex.map(_probe_one, items, chunksize=8), 1):
            if i % 250 == 0:
                print(f"\r[probe] {i}/{len(items)}", end="", flush=True)
            if not error:
                continue
            # Since I-035 the worker returns a (kind, message) pair; older ALAO
            # returned one string that had to be substring-matched.
            if isinstance(error, tuple):
                kind, message = error
            else:
                message = error
                first_line = message.splitlines()[0] if message else ""
                if "TimeoutError" in first_line:
                    kind = "timeout"
                elif "SyntaxError" in first_line or "parse" in message.lower():
                    kind = "parse"
                else:
                    kind = "crash"
            first = message.splitlines()[0] if message else ""
            entry_file = rel(work, path)
            if kind == "timeout":
                timeouts.append(entry_file)
            elif kind in ("parse", "encoding"):
                parse_failures.append({"file": entry_file, "error": first})
            else:
                crashes.append({"file": entry_file, "traceback": message[-4000:]})
    print(f"\r[probe] done: {len(parse_failures)} parse, {len(timeouts)} timeout, {len(crashes)} crash")
    return parse_failures, timeouts, crashes


# ---------------------------------------------------------------- report JSON

def summarize_report(report_path: Path):
    if not report_path.is_file():
        return {}, {}, None
    data = json.loads(report_path.read_text(encoding="utf-8"))
    by_pattern, by_sev = {}, {"GREEN": 0, "YELLOW": 0, "RED": 0, "DEBUG": 0}
    for _mod, files in (data.get("findings") or {}).items():
        for _f, findings in files.items():
            for fd in findings:
                p = fd.get("pattern", "?")
                by_pattern[p] = by_pattern.get(p, 0) + 1
                sev = fd.get("severity", "?")
                by_sev[sev] = by_sev.get(sev, 0) + 1
    by_pattern = dict(sorted(by_pattern.items(), key=lambda kv: -kv[1]))
    return by_pattern, by_sev, data.get("summary")


# ---------------------------------------------------------------- lupa compile check

def compile_check(files, root: Path):
    """Compile each (new, original) pair with LuaJIT 2.0 via lupa.

    Only counts as a failure when the ORIGINAL compiled and the rewrite does
    not - a mod that was already broken is not ALAO's fault.
    """
    try:
        from lupa import luajit20
    except ImportError:
        return None, "lupa (LuaJIT 2.0) not installed - compile check skipped"
    lua = luajit20.LuaRuntime(unpack_returned_tuples=True)
    # loadstring returns 1 value on success and 2 on failure; normalise it so
    # lupa always hands us a (bool, message) pair.
    check = lua.eval(
        "function(src, name)"
        "  local f, e = loadstring(src, name)"
        "  if f then return true, '' end"
        "  return false, tostring(e)"
        "end"
    )

    def compiles(path: Path):
        try:
            src = read_text_any(path)
        except Exception as e:
            return False, f"read failed: {e}"
        try:
            ok, err = check(src, "@" + path.name)
        except Exception as e:  # lupa refused the source outright
            return False, f"lupa error: {e}"
        return bool(ok), str(err)

    failures = []
    for new_path, orig_path in files:
        ok, err = compiles(new_path)
        if ok:
            continue
        # a mod that was already broken before the rewrite is not ALAO's fault
        if orig_path and orig_path.is_file() and not compiles(orig_path)[0]:
            continue
        failures.append({"file": rel(root, new_path), "error": err})
    return failures, None


# ---------------------------------------------------------------- diffs

def write_diffs(root: Path, pairs, diff_dir: Path, limit=MAX_DIFFS):
    diff_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    for new_path, orig_path in pairs[:limit]:
        try:
            a = read_text_any(orig_path).splitlines(keepends=True)
            b = read_text_any(new_path).splitlines(keepends=True)
        except Exception:
            continue
        name = rel(root, new_path).replace("/", "__") + ".diff"
        text = "".join(difflib.unified_diff(a, b, fromfile=rel(root, orig_path),
                                            tofile=rel(root, new_path), n=3))
        (diff_dir / name).write_text(text, encoding="utf-8", errors="replace")
        written += 1
    return written


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description="Run ALAO over a corpus and record contract results.")
    ap.add_argument("--corpus", type=Path, required=True, help="extracted corpus dir (MO2 layout)")
    ap.add_argument("--corpus-name", default=None,
                    help='contract corpus id, e.g. "gamma-0.9.4" / "vanilla-1.5.3" / "fixtures"')
    ap.add_argument("--label", default=None, help="run_id slug (default: derived from corpus-name)")
    ap.add_argument("--fix-flags", default="",
                    help='fix flags to apply after analyze, e.g. "--fix --fix-debug --fix-nil". '
                         'For a single flag use the = form (--fix-flags=--fix), otherwise argparse '
                         'swallows it as an option of its own.')
    ap.add_argument("--analyze-args", default="", help="extra args for the analyze pass")
    ap.add_argument("--timeout", type=float, default=10.0, help="ALAO per-file timeout (default 10)")
    ap.add_argument("--cache-threshold", type=int, default=4)
    ap.add_argument("--jobs", "-j", type=int, default=None, help="workers for the fix pass and the probe")
    ap.add_argument("--single-thread", action="store_true", help="pass --single-thread to the fix pass")
    ap.add_argument("--proc-timeout", type=float, default=7200.0,
                    help="wall-clock limit per ALAO subprocess (default 7200s)")
    ap.add_argument("--use-repo-exclude", action="store_true",
                    help="honour the repo's alao_exclude.txt (default: override it with an empty "
                         "list, so a regression run sees every mod whatever a local edit of that "
                         "file says - it ships empty since I-036, it used to exclude VANILLA_SCRIPTS)")
    ap.add_argument("--no-probe", action="store_true",
                    help="skip per-file failure attribution entirely (only relevant "
                         "for an ALAO older than I-029, which has no failure data in "
                         "its report)")
    ap.add_argument("--probe", action="store_true",
                    help="run the duplicate analyze pass anyway and cross-check it "
                         "against the report's failure data (slow; off by default "
                         "since I-029)")
    ap.add_argument("--no-idempotence", action="store_true", help="skip the second fix pass")
    ap.add_argument("--no-capture-gate", action="store_true",
                    help="skip G9, the shadowing/capture scan over the originals (I-046)")
    ap.add_argument("--out-root", type=Path, default=DEFAULT_LAB / "data" / "corpus")
    ap.add_argument("--work-root", type=Path, default=None,
                    help="where working copies live (default: <repo>/extracted/_work)")
    ap.add_argument("--keep-work", action="store_true", help="do not delete the working copies at the end")
    ap.add_argument("--notes", default="")
    args = ap.parse_args()

    corpus = args.corpus.resolve()
    if not corpus.is_dir():
        raise SystemExit(f"corpus not found: {corpus}")
    try:
        corpus.relative_to(DEFAULT_INSTALL)
        raise SystemExit("refusing to run against the read-only game install; extract a corpus first")
    except ValueError:
        pass

    corpus_name = args.corpus_name or corpus.name
    run_id = run_id_for(args.label or corpus_name + ("-fix" if args.fix_flags else "-analyze"))
    out_dir = (args.out_root / run_id).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    diff_dir = out_dir / "diffs"
    work_root = (args.work_root or (REPO_ROOT / "extracted" / "_work")).resolve()
    work = work_root / run_id / "work"
    jobs = args.jobs or min(8, os.cpu_count() or 4)

    started = datetime.now().isoformat(timespec="seconds")
    commit, dirty = git_state(REPO_ROOT)
    fix_flags = args.fix_flags.split()
    analyze_extra = args.analyze_args.split()

    # the repo auto-loads alao_exclude.txt (it shipped with VANILLA_SCRIPTS in
    # it until I-036); a regression run should see the whole corpus whatever
    # someone has since put in that file, so hand it an empty list.
    common_args = []
    if not args.use_repo_exclude:
        empty_exclude = out_dir / "empty-exclude.txt"
        empty_exclude.write_text("# corpus_run: exclusions disabled\n", encoding="utf-8")
        common_args = ["--exclude", str(empty_exclude)]

    print(f"[run] run_id  : {run_id}")
    print(f"[run] corpus  : {corpus}")
    print(f"[run] work    : {work}")
    print(f"[run] out     : {out_dir}")

    # 1. fresh working copy (exclude any manifest / stale backups)
    if work.exists():
        shutil.rmtree(work)
    work.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(corpus, work, ignore=shutil.ignore_patterns("corpus_manifest.json", "*" + BAK_SUFFIX))
    corpus_files = sum(1 for _ in iter_scripts(work))
    print(f"[run] files   : {corpus_files}")

    results = {
        "analyze_s": None, "fix_s": None,
        "parse_failures": [], "timeouts": [], "crashes": [],
        "findings_by_pattern": {}, "findings_by_severity": {},
        "files_modified": 0, "edits_applied": None, "edits_dropped_overlap": None,
        "compile_failures_after_fix": [], "idempotence_violations": [],
        "captures": [],
        "differential_failures": [], "extra": {},
    }
    notes = [args.notes] if args.notes else []
    status = "done"

    # 2. analyze
    report_json = out_dir / "alao-report.json"
    analyze_args = [
        "--report", str(report_json),
        "--timeout", str(args.timeout),
        "--cache-threshold", str(args.cache_threshold),
        *common_args, *analyze_extra,
    ]
    try:
        proc, analyze_s = run_alao(work, analyze_args, args.proc_timeout, out_dir / "analyze.log")
        results["analyze_s"] = round(analyze_s, 2)
        results["extra"]["analyze_returncode"] = proc.returncode
        results["extra"]["stdout_counts"] = parse_stdout_counts(proc.stdout)
        if proc.returncode != 0:
            status = "failed"
            notes.append(f"analyze exited {proc.returncode}; see analyze.log")
    except subprocess.TimeoutExpired:
        status = "failed"
        notes.append(f"analyze pass exceeded --proc-timeout ({args.proc_timeout}s)")
        results["extra"]["analyze_returncode"] = "proc-timeout"

    by_pattern, by_sev, summary = summarize_report(report_json)
    results["findings_by_pattern"] = by_pattern
    results["findings_by_severity"] = by_sev
    results["extra"]["report_summary"] = summary

    # 3. per-file failure attribution. Since I-029 ALAO publishes this in the
    # report itself, so the duplicate analyze pass this harness used to run over
    # every file - roughly doubling its runtime - is only needed for an older
    # ALAO, or as an explicit cross-check with --probe.
    from_report = failures_from_report(report_json, work)
    if from_report is not None and not args.probe:
        results["parse_failures"], results["timeouts"], results["crashes"] = from_report
        results["extra"]["failure_source"] = "alao-report"
    elif args.no_probe:
        notes.append("failure attribution skipped (--no-probe); counts only in extra.stdout_counts")
        results["extra"]["failure_source"] = "none"
    else:
        pf, to, cr = probe_failures(work, args.timeout, args.cache_threshold, jobs)
        results["parse_failures"], results["timeouts"], results["crashes"] = pf, to, cr
        results["extra"]["failure_source"] = "probe"
        if from_report is not None:
            # --probe cross-check: the report and the probe must agree
            rep_pf, rep_to, rep_cr = from_report
            mismatch = {
                k: {"report": r, "probe": p}
                for k, r, p in (
                    ("parse_failures", len(rep_pf), len(pf)),
                    ("timeouts", len(rep_to), len(to)),
                    ("crashes", len(rep_cr), len(cr)),
                )
                if r != p
            }
            results["extra"]["probe_vs_report"] = mismatch or "agree"
            if mismatch:
                notes.append(f"probe and report disagree on failures: {mismatch}")

    # 4. fix pass
    if fix_flags:
        fix_report = out_dir / "alao-fix-report.json"
        fargs = [*fix_flags, "--no-first-time-auto-backup", "--timeout", str(args.timeout),
                 "--cache-threshold", str(args.cache_threshold),
                 "--report", str(fix_report), *common_args]
        fargs += ["--single-thread"] if args.single_thread else ["-j", str(jobs)]
        try:
            fproc, fix_s = run_alao(work, fargs, args.proc_timeout, out_dir / "fix.log")
            results["fix_s"] = round(fix_s, 2)
            results["extra"]["fix_returncode"] = fproc.returncode
            results["extra"]["fix_stdout_counts"] = parse_stdout_counts(fproc.stdout)
            counts = results["extra"]["fix_stdout_counts"]
            results["edits_applied"] = counts.get("edits_applied")
            if fproc.returncode != 0:
                status = "failed"
                notes.append(f"fix exited {fproc.returncode}; see fix.log")
        except subprocess.TimeoutExpired:
            status = "failed"
            notes.append(f"fix pass exceeded --proc-timeout ({args.proc_timeout}s)")
            results["extra"]["fix_returncode"] = "proc-timeout"

        # files modified == files that got a .alao-bak sibling
        baks = bak_files(work)
        pairs = []
        for bak in baks:
            new_path = bak.with_suffix("")  # foo.script.alao-bak -> foo.script
            if new_path.is_file():
                pairs.append((new_path, bak))
        results["files_modified"] = len(pairs)

        # edit accounting straight from the fix run's report (I-029). Before it
        # existed, edits_dropped_overlap was written as null in every run.
        totals = edits_from_report(fix_report)
        if totals:
            results["edits_applied"] = totals.get("edits_applied", results["edits_applied"])
            results["edits_dropped_overlap"] = totals.get("edits_dropped_overlap")
            results["extra"]["edits_generated"] = totals.get("edits_generated")
        else:
            notes.append("edits_dropped_overlap is null: this ALAO predates I-029 and "
                         "publishes no per-file edit breakdown.")

        # 5. compile-check the rewrites. ALAO verifies before writing since
        # I-004, so anything it refused is already in the report; this external
        # pass stays as the independent check that what DID get written loads.
        refused = compile_failures_from_report(fix_report, work)
        if refused:
            results["extra"]["compile_failures_refused_by_alao"] = refused
            notes.append(f"ALAO refused to write {len(refused)} rewrite(s) that did not compile")
        failures, skip_note = compile_check(pairs, work)
        if skip_note:
            notes.append(skip_note)
        results["compile_failures_after_fix"] = (failures or []) + (refused or [])

        # 6. diffs
        written = write_diffs(work, pairs, diff_dir)
        results["extra"]["diffs_written"] = written

        # 7. idempotence: fix a copy of the fixed tree again, with backups removed
        if not args.no_idempotence and pairs:
            work2 = work.parent / "work2"
            if work2.exists():
                shutil.rmtree(work2)
            shutil.copytree(work, work2)
            for bak in bak_files(work2):
                bak.unlink()
            try:
                run_alao(work2, fargs, args.proc_timeout, out_dir / "fix-pass2.log")
            except subprocess.TimeoutExpired:
                notes.append("second fix pass timed out; idempotence result is partial")
            violations = []
            for new_path, _bak in pairs:
                second = work2 / Path(rel(work, new_path))
                if not second.is_file():
                    continue
                if second.read_bytes() != new_path.read_bytes():
                    violations.append(rel(work, new_path))
            results["idempotence_violations"] = violations
            results["extra"]["idempotence_second_pass_modified"] = len(bak_files(work2))
            if not args.keep_work:
                shutil.rmtree(work2, ignore_errors=True)

        # 8. G9 (I-046): no insertion may bind over a live outer name. The scan
        # asks the transformer what it inserts into each ORIGINAL (the .alao-bak
        # sibling) and checks each name against the scope it lands in. Cheap
        # next to the fix pass, and it catches the class of silent miscompile
        # that G4 (it compiles) and G5 (it is idempotent) both wave through.
        if pairs and not args.no_capture_gate:
            print(f"[g9] capture scan over {len(pairs)} originals with {jobs} workers...")
            t_g9 = time.perf_counter()
            scan = scan_paths([bak for _new, bak in pairs], jobs=jobs,
                              **flags_from_cli(fix_flags))
            results["captures"] = [c.__dict__ for c in scan.captures]
            info = scan.as_dict()
            info.pop("captures", None)
            info["seconds"] = round(time.perf_counter() - t_g9, 2)
            results["extra"]["capture_scan"] = info
            print(f"[g9] {scan.summary()}")
            if scan.captures:
                status = "failed"
                notes.append(f"G9: {len(scan.captures)} inserted name(s) shadow a live outer "
                             f"binding, e.g. {scan.captures[0]}")

    finished = datetime.now().isoformat(timespec="seconds")

    manifest = {
        "run_id": run_id,
        "alao_commit": commit,
        "alao_dirty": dirty,
        "corpus": corpus_name,
        "corpus_files": corpus_files,
        "flags": analyze_args if not fix_flags else analyze_args + ["|"] + fix_flags,
        "started": started,
        "finished": finished,
        "status": status,
        "notes": " ".join(notes),
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (out_dir / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")

    if not args.keep_work:
        shutil.rmtree(work.parent, ignore_errors=True)

    # 8. summary table
    top = list(results["findings_by_pattern"].items())[:15]
    print("\n" + "=" * 62)
    print(f"run_id                     {run_id}")
    print(f"corpus / files             {corpus_name} / {corpus_files}")
    print(f"alao commit                {commit} (dirty={dirty})")
    print(f"status                     {status}")
    print(f"analyze_s / fix_s          {results['analyze_s']} / {results['fix_s']}")
    print(f"parse failures             {len(results['parse_failures'])}")
    print(f"timeouts                   {len(results['timeouts'])}")
    print(f"crashes                    {len(results['crashes'])}")
    print(f"files modified             {results['files_modified']}")
    print(f"edits applied              {results['edits_applied']}")
    print(f"compile failures after fix {len(results['compile_failures_after_fix'])}")
    print(f"idempotence violations     {len(results['idempotence_violations'])}")
    cap_info = results["extra"].get("capture_scan") or {}
    print(f"captures (G9)              {len(results['captures'])}"
          + (f"  [{cap_info.get('insertions')} insertions in {cap_info.get('files')} files, "
             f"{len(cap_info.get('advisories') or [])} advisories]" if cap_info else ""))
    print(f"findings total             {sum(results['findings_by_pattern'].values())}")
    print(f"by severity                {results['findings_by_severity']}")
    print("-" * 62)
    print(f"{'pattern':<44}{'count':>8}")
    for pat, n in top:
        print(f"{pat:<44}{n:>8}")
    print("=" * 62)
    print(f"written: {out_dir}")


if __name__ == "__main__":
    main()
