#!/usr/bin/env python3
"""AALO lab dashboard - stdlib-only HTTP server.

Serves a JSON API over the shared data/ directory (see CONTRACT.md) plus a
static single-page UI.  Never crashes on malformed data: bad files are skipped
and reported on stderr.

    py -3.12 dashboard/server.py --port 8765 --data D:\\GOG_Games\\Gamma\\aalo-lab\\data
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import tempfile
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

HERE = Path(__file__).resolve().parent
STATIC_DIR = HERE / "static"
DEFAULT_DATA = HERE.parent / "data"

MAX_SAMPLES = 2000
MAX_BODY = 1 << 20  # 1 MiB

IDEA_STATUSES = {"proposed", "queued", "running", "kept", "pruned"}

_write_lock = threading.Lock()


def warn(msg: str) -> None:
    print("[dashboard] " + msg, file=sys.stderr, flush=True)


# --------------------------------------------------------------------------
# data access
# --------------------------------------------------------------------------

def read_json(path: Path):
    """Return parsed JSON or None (missing / unreadable / malformed)."""
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return None
    except Exception as exc:  # noqa: BLE001 - tolerate anything on disk
        warn("cannot read %s: %s" % (path, exc))
        return None


def write_json_atomic(path: Path, payload) -> None:
    """Write JSON to path atomically (temp file in the same dir + os.replace)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _num(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


class Store:
    """Read/write access to the shared data directory."""

    def __init__(self, root: Path):
        self.root = Path(root)

    # ---- ideas ----------------------------------------------------------
    @property
    def ideas_path(self) -> Path:
        return self.root / "ideas.json"

    def load_ideas(self) -> list:
        raw = read_json(self.ideas_path)
        if raw is None:
            return []
        if isinstance(raw, list):
            items = raw
        elif isinstance(raw, dict):
            items = raw.get("ideas")
        else:
            items = None
        if not isinstance(items, list):
            warn("%s: no 'ideas' list, treating as empty" % self.ideas_path)
            return []
        out = []
        for item in items:
            if isinstance(item, dict) and item.get("id"):
                out.append(item)
            else:
                warn("%s: skipping malformed idea entry" % self.ideas_path)
        return out

    def update_idea(self, idea_id: str, field: str, value):
        """Patch one field of one idea and rewrite ideas.json atomically."""
        with _write_lock:
            raw = read_json(self.ideas_path)
            if isinstance(raw, list):
                raw = {"ideas": raw}
            if not isinstance(raw, dict) or not isinstance(raw.get("ideas"), list):
                return None
            for item in raw["ideas"]:
                if isinstance(item, dict) and item.get("id") == idea_id:
                    item[field] = value
                    write_json_atomic(self.ideas_path, raw)
                    return item
            return None

    # ---- corpus ---------------------------------------------------------
    @property
    def corpus_dir(self) -> Path:
        return self.root / "corpus"

    def corpus_run_ids(self) -> list:
        try:
            return sorted(p.name for p in self.corpus_dir.iterdir() if p.is_dir())
        except FileNotFoundError:
            return []
        except Exception as exc:  # noqa: BLE001
            warn("cannot list %s: %s" % (self.corpus_dir, exc))
            return []

    def safe_corpus_dir(self, run_id: str):
        if not run_id or "/" in run_id or "\\" in run_id or run_id in (".", ".."):
            return None
        try:
            candidate = (self.corpus_dir / run_id).resolve()
            root = self.corpus_dir.resolve()
        except OSError:
            return None
        if root not in candidate.parents:
            return None
        return candidate

    def load_corpus_run(self, run_id: str):
        run_dir = self.safe_corpus_dir(run_id)
        if run_dir is None or not run_dir.is_dir():
            return None
        manifest = read_json(run_dir / "manifest.json")
        if not isinstance(manifest, dict):
            if manifest is not None:
                warn("corpus %s: manifest.json is not an object" % run_id)
            manifest = {}
        results = read_json(run_dir / "results.json")
        if not isinstance(results, dict):
            if results is not None:
                warn("corpus %s: results.json is not an object" % run_id)
            results = {}
        manifest.setdefault("run_id", run_id)
        return {"run_id": run_id, "manifest": manifest, "results": results}

    # ---- runs -----------------------------------------------------------
    @property
    def runs_dir(self) -> Path:
        return self.root / "runs"

    def run_ids(self) -> list:
        try:
            return sorted(p.name for p in self.runs_dir.iterdir() if p.is_dir())
        except FileNotFoundError:
            return []
        except Exception as exc:  # noqa: BLE001
            warn("cannot list %s: %s" % (self.runs_dir, exc))
            return []

    def safe_run_dir(self, run_id: str):
        """Resolve run_id under runs/ rejecting traversal."""
        if not run_id or "/" in run_id or "\\" in run_id or run_id in (".", ".."):
            return None
        try:
            candidate = (self.runs_dir / run_id).resolve()
            runs_root = self.runs_dir.resolve()
        except OSError:
            return None
        if runs_root not in candidate.parents:
            return None
        return candidate

    def load_run(self, run_id: str):
        run_dir = self.safe_run_dir(run_id)
        if run_dir is None or not run_dir.is_dir():
            return None
        manifest = read_json(run_dir / "manifest.json")
        if not isinstance(manifest, dict):
            if manifest is not None:
                warn("%s: manifest.json is not an object" % run_id)
            manifest = {}
        metrics = read_json(run_dir / "metrics.json")
        if not isinstance(metrics, dict):
            if metrics is not None:
                warn("%s: metrics.json is not an object" % run_id)
            metrics = {}
        manifest.setdefault("run_id", run_id)
        return {"run_id": run_id, "manifest": manifest, "metrics": metrics}

    def load_samples(self, run_id: str):
        """Return (downsampled rows, total row count)."""
        run_dir = self.safe_run_dir(run_id)
        if run_dir is None:
            return [], 0
        path = run_dir / "samples.csv"
        rows = []
        try:
            with path.open("r", encoding="utf-8", newline="") as fh:
                reader = csv.DictReader(fh)
                if not reader.fieldnames:
                    return [], 0
                cols = {}
                for c in reader.fieldnames:
                    if c:
                        cols[c.strip().lower()] = c
                t_col = cols.get("t_s") or cols.get("t")
                ft_col = cols.get("frametime_ms") or cols.get("frametime")
                fps_col = cols.get("fps")
                for i, row in enumerate(reader):
                    t = _num(row.get(t_col)) if t_col else None
                    ft = _num(row.get(ft_col)) if ft_col else None
                    fps = _num(row.get(fps_col)) if fps_col else None
                    if ft is None and fps:
                        ft = 1000.0 / fps
                    if fps is None and ft:
                        fps = 1000.0 / ft
                    if t is None:
                        t = float(i)
                    if ft is None:
                        continue
                    rows.append({"t_s": t, "frametime_ms": ft, "fps": fps})
        except FileNotFoundError:
            return [], 0
        except Exception as exc:  # noqa: BLE001
            warn("%s: cannot read samples.csv: %s" % (run_id, exc))
            return [], 0
        return downsample(rows, MAX_SAMPLES), len(rows)


def downsample(rows: list, limit: int) -> list:
    """Bucket rows to <=limit points, keeping the worst frametime per bucket."""
    n = len(rows)
    if n <= limit:
        return rows
    out = []
    for b in range(limit):
        start = (b * n) // limit
        end = ((b + 1) * n) // limit
        if end <= start:
            continue
        out.append(max(rows[start:end], key=lambda r: r["frametime_ms"]))
    return out


# --------------------------------------------------------------------------
# API payload builders
# --------------------------------------------------------------------------

def _as_list(value) -> list:
    return value if isinstance(value, list) else []


def _as_dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _counts(mapping) -> dict:
    """Keep only {str: number} entries of a findings_by_* map."""
    out = {}
    for key, val in _as_dict(mapping).items():
        n = _num(val)
        if n is not None:
            out[str(key)] = int(n) if float(n).is_integer() else n
    return out


def _failure_files(items) -> list:
    """Normalise a failure list to plain file paths.

    Entries are either {"file": ..., "error": ...} records or bare strings
    (idempotence_violations is a list of files).
    """
    out = []
    for item in _as_list(items):
        if isinstance(item, dict):
            f = item.get("file")
            if f:
                out.append(str(f))
        elif isinstance(item, str):
            out.append(item)
    return out


def corpus_started_key(row: dict) -> str:
    """Sort key for a corpus run: manifest.started, else the run_id stamp.

    A run still being written has no manifest.json yet; run ids are
    YYYYMMDD-HHMMSS-<slug>, so the id alone orders it correctly.
    """
    started = row.get("started")
    if started:
        return str(started)
    rid = str(row.get("run_id") or "")
    if len(rid) >= 15 and rid[:8].isdigit() and rid[8] == "-" and rid[9:15].isdigit():
        return "%s-%s-%sT%s:%s:%s" % (rid[0:4], rid[4:6], rid[6:8],
                                      rid[9:11], rid[11:13], rid[13:15])
    return ""


def corpus_health(results: dict) -> str:
    """ok | warn | fail for one corpus run.

    fail: any compile failure after --fix, idempotence violation, or crash.
    warn: any parse failure or timeout.
    """
    results = _as_dict(results)
    if not results:
        return "unknown"
    if _as_list(results.get("compile_failures_after_fix")) \
            or _as_list(results.get("idempotence_violations")) \
            or _as_list(results.get("crashes")) \
            or _as_list(results.get("differential_failures")):
        return "fail"
    if _as_list(results.get("parse_failures")) or _as_list(results.get("timeouts")):
        return "warn"
    return "ok"


def corpus_row(run: dict) -> dict:
    """manifest + results squashed into one flat, countable record."""
    m = _as_dict(run.get("manifest"))
    r = _as_dict(run.get("results"))
    by_pattern = _counts(r.get("findings_by_pattern"))
    by_severity = _counts(r.get("findings_by_severity"))
    commit = m.get("alao_commit") or ""
    flags = m.get("flags")
    return {
        "run_id": run.get("run_id"),
        "alao_commit": commit,
        "alao_commit_short": str(commit)[:8],
        "alao_dirty": bool(m.get("alao_dirty")),
        "corpus": m.get("corpus"),
        "corpus_files": _num(m.get("corpus_files")),
        "flags": flags if isinstance(flags, list) else [],
        "started": m.get("started"),
        "finished": m.get("finished"),
        "status": m.get("status"),
        "notes": m.get("notes") or "",
        "analyze_s": _num(r.get("analyze_s")),
        "fix_s": _num(r.get("fix_s")),
        "files_modified": _num(r.get("files_modified")),
        "edits_applied": _num(r.get("edits_applied")),
        "edits_dropped_overlap": _num(r.get("edits_dropped_overlap")),
        "findings_total": sum(by_pattern.values()),
        "findings_by_severity": by_severity,
        "pattern_count": len(by_pattern),
        "parse_failures": len(_as_list(r.get("parse_failures"))),
        "timeouts": len(_as_list(r.get("timeouts"))),
        "crashes": len(_as_list(r.get("crashes"))),
        "compile_failures_after_fix": len(_as_list(r.get("compile_failures_after_fix"))),
        "idempotence_violations": len(_as_list(r.get("idempotence_violations"))),
        "differential_failures": len(_as_list(r.get("differential_failures"))),
        "health": corpus_health(r),
        "has_results": bool(r),
        "has_manifest": bool(m) and set(m) != {"run_id"},
    }


def corpus_payload(store: Store) -> list:
    out = []
    for run_id in store.corpus_run_ids():
        run = store.load_corpus_run(run_id)
        if run is None:
            continue
        out.append(corpus_row(run))
    out.sort(key=lambda r: (corpus_started_key(r), r["run_id"] or ""), reverse=True)
    return out


def corpus_detail_payload(run: dict) -> dict:
    r = _as_dict(run.get("results"))
    row = corpus_row(run)
    by_pattern = _counts(r.get("findings_by_pattern"))
    row.update({
        "manifest": _as_dict(run.get("manifest")),
        "findings_by_pattern": by_pattern,
        "patterns": [{"pattern": k, "count": v} for k, v in
                     sorted(by_pattern.items(), key=lambda kv: (-kv[1], kv[0]))],
        "parse_failure_list": _as_list(r.get("parse_failures")),
        "compile_failure_list": _as_list(r.get("compile_failures_after_fix")),
        "idempotence_violation_list": _as_list(r.get("idempotence_violations")),
        "timeout_list": _as_list(r.get("timeouts")),
        "crash_list": _as_list(r.get("crashes")),
        "differential_failure_list": _as_list(r.get("differential_failures")),
        "extra": _as_dict(r.get("extra")),
    })
    return row


def _delta(a, b):
    if a is None or b is None:
        return None
    return round(a - b, 3)


def _list_diff(a_items, b_items) -> dict:
    """Set diff of two failure lists, keyed by file path.

    a is the newer run: "added" is what a has and b does not.
    """
    a_files = _failure_files(a_items)
    b_files = _failure_files(b_items)
    a_set, b_set = set(a_files), set(b_files)
    return {
        "a_count": len(a_files),
        "b_count": len(b_files),
        "added": sorted(a_set - b_set),
        "removed": sorted(b_set - a_set),
        "unchanged": sorted(a_set & b_set),
    }


def corpus_compare_payload(store: Store, a_id=None, b_id=None):
    """Compare two corpus runs.  Defaults: a = latest, b = the one before it.

    Returns None when a requested run does not exist, or {"error": ...} when
    there are not enough runs to compare.
    """
    rows = corpus_payload(store)
    # a run still being written has no results.json; never default to it
    done = [r for r in rows if r["has_results"]] or rows
    if a_id is None:
        a_id = done[0]["run_id"] if done else None
    if b_id is None:
        if a_id is not None:
            ids = [r["run_id"] for r in done]
            try:
                idx = ids.index(a_id)
            except ValueError:
                idx = -1
            b_id = ids[idx + 1] if 0 <= idx < len(ids) - 1 else None
    if a_id is None or b_id is None:
        return {"a": None, "b": None, "patterns": [], "severity": [],
                "failures": {}, "timing": {}, "findings_total": None,
                "note": "need two corpus runs to compare"}

    a_run = store.load_corpus_run(a_id)
    b_run = store.load_corpus_run(b_id)
    if a_run is None or b_run is None:
        return None

    a = corpus_detail_payload(a_run)
    b = corpus_detail_payload(b_run)
    ap, bp = a["findings_by_pattern"], b["findings_by_pattern"]

    patterns = []
    for name in sorted(set(ap) | set(bp)):
        av, bv = ap.get(name, 0), bp.get(name, 0)
        patterns.append({"pattern": name, "a": av, "b": bv, "delta": av - bv,
                         "new": name not in bp, "gone": name not in ap})
    patterns.sort(key=lambda p: (-abs(p["delta"]), -max(p["a"], p["b"]), p["pattern"]))

    a_sev, b_sev = a["findings_by_severity"], b["findings_by_severity"]
    severity = []
    for name in sorted(set(a_sev) | set(b_sev)):
        av, bv = a_sev.get(name, 0), b_sev.get(name, 0)
        severity.append({"severity": name, "a": av, "b": bv, "delta": av - bv})

    def head(row):
        return {k: row[k] for k in (
            "run_id", "started", "status", "health", "alao_commit",
            "alao_commit_short", "alao_dirty", "corpus", "corpus_files",
            "findings_total", "files_modified", "edits_applied",
            "parse_failures", "compile_failures_after_fix",
            "idempotence_violations", "analyze_s", "fix_s")}

    return {
        "a": head(a),
        "b": head(b),
        "same_commit": bool(a["alao_commit"]) and a["alao_commit"] == b["alao_commit"],
        "patterns": patterns,
        "severity": severity,
        "findings_total": {"a": a["findings_total"], "b": b["findings_total"],
                           "delta": a["findings_total"] - b["findings_total"]},
        "failures": {
            "parse_failures": _list_diff(a["parse_failure_list"], b["parse_failure_list"]),
            "compile_failures_after_fix": _list_diff(
                a["compile_failure_list"], b["compile_failure_list"]),
            "idempotence_violations": _list_diff(
                a["idempotence_violation_list"], b["idempotence_violation_list"]),
            "timeouts": _list_diff(a["timeout_list"], b["timeout_list"]),
        },
        "timing": {
            "analyze_s": {"a": a["analyze_s"], "b": b["analyze_s"],
                          "delta": _delta(a["analyze_s"], b["analyze_s"])},
            "fix_s": {"a": a["fix_s"], "b": b["fix_s"],
                      "delta": _delta(a["fix_s"], b["fix_s"])},
        },
    }


def corpus_summary(store: Store) -> dict:
    """Latest corpus run folded into the /api/summary payload."""
    rows = corpus_payload(store)
    if not rows:
        return {
            "runs_total": 0, "pending": 0, "latest": None, "health": "unknown",
            "findings_total": 0, "parse_failures": 0,
            "compile_failures_after_fix": 0, "idempotence_violations": 0,
        }
    done = [r for r in rows if r["has_results"]]
    latest = (done or rows)[0]
    return {
        "runs_total": len(rows),
        "pending": len(rows) - len(done),
        "latest": latest,
        "health": latest["health"],
        "findings_total": latest["findings_total"],
        "parse_failures": latest["parse_failures"],
        "compile_failures_after_fix": latest["compile_failures_after_fix"],
        "idempotence_violations": latest["idempotence_violations"],
        "trend": [{
            "run_id": r["run_id"],
            "started": r["started"],
            "findings_total": r["findings_total"],
            "parse_failures": r["parse_failures"],
            "compile_failures_after_fix": r["compile_failures_after_fix"],
            "idempotence_violations": r["idempotence_violations"],
            "health": r["health"],
        } for r in reversed(done or rows)],
    }


def runs_payload(store: Store) -> list:
    out = []
    for run_id in store.run_ids():
        run = store.load_run(run_id)
        if run is None:
            continue
        m = run["manifest"]
        met = run["metrics"]
        cfg = m.get("config_diff")
        crashed = met.get("crashed")
        out.append({
            "run_id": run_id,
            "idea_id": m.get("idea_id"),
            "status": m.get("status"),
            "started": m.get("started"),
            "finished": m.get("finished"),
            "exe": m.get("exe"),
            "mo2_profile": m.get("mo2_profile"),
            "notes": m.get("notes") or "",
            "arm": m.get("arm") or ("baseline" if _is_baseline({"idea_id": m.get("idea_id"), "notes": m.get("notes") or ""}) else "variant"),
            "experiment": m.get("experiment") or "",
            "mods_enabled": sorted(k[4:] for k, val in (cfg.items() if isinstance(cfg, dict) else [])
                                   if k.startswith("mod/") and isinstance(val, list) and val[-1] == "enabled"),
            "config_diff": cfg if isinstance(cfg, dict) else {},
            "capped": bool((met.get("extra") or {}).get("capped")),
            "fps_avg": _num(met.get("fps_avg")),
            "fps_1pct_low": _num(met.get("fps_1pct_low")),
            "frametime_p99_ms": _num(met.get("frametime_p99_ms")),
            "load_time_s": _num(met.get("load_time_s")),
            "ram_peak_mb": _num(met.get("ram_peak_mb")),
            "vram_peak_mb": _num(met.get("vram_peak_mb")),
            "duration_s": _num(met.get("duration_s")),
            "crashed": bool(crashed) if crashed is not None else None,
            "has_metrics": bool(met),
        })
    out.sort(key=lambda r: (r.get("started") or "", r["run_id"]), reverse=True)
    return out


def _is_baseline(run: dict) -> bool:
    """A run is a baseline when it carries no idea_id, or its manifest.notes
    tag it as one: notes starting with "baseline" (optionally bracketed) or
    containing "baseline=true".  Notes that mention "variant" never count, so
    prose like "compared against the baseline" does not misfire.
    """
    notes = (run.get("notes") or "").strip().lower()
    if "variant" in notes:
        return False
    if notes.startswith("baseline") or notes.startswith("[baseline]") \
            or "baseline=true" in notes:
        return True
    return run.get("idea_id") in (None, "", "baseline")


def summary_payload(store: Store) -> dict:
    ideas = store.load_ideas()
    runs = runs_payload(store)

    ideas_by_status = {}
    for idea in ideas:
        key = idea.get("status") or "unknown"
        ideas_by_status[key] = ideas_by_status.get(key, 0) + 1

    runs_by_status = {}
    for run in runs:
        key = run.get("status") or "unknown"
        runs_by_status[key] = runs_by_status.get(key, 0) + 1

    scored = [r for r in runs if r["fps_avg"] is not None and not r.get("crashed")]
    best = max(scored, key=lambda r: r["fps_avg"]) if scored else None

    best_per_idea = {}
    for run in scored:
        iid = run.get("idea_id")
        if not iid:
            continue
        cur = best_per_idea.get(iid)
        if cur is None or run["fps_avg"] > cur["fps_avg"]:
            best_per_idea[iid] = {
                "run_id": run["run_id"],
                "fps_avg": run["fps_avg"],
                "fps_1pct_low": run["fps_1pct_low"],
                "frametime_p99_ms": run["frametime_p99_ms"],
            }

    # Baseline vs variant deltas.  A run counts as a baseline when its
    # manifest.notes mention "baseline" or it carries no idea_id.  A variant is
    # compared against a baseline sharing its idea id, else the newest global
    # baseline.
    baselines = [r for r in scored if _is_baseline(r)]
    global_baseline = max(baselines, key=lambda r: r.get("started") or "") if baselines else None
    per_idea_baseline = {}
    for run in baselines:
        iid = run.get("idea_id")
        if iid:
            per_idea_baseline.setdefault(iid, run)

    deltas = []
    for iid in sorted(best_per_idea):
        best_run = best_per_idea[iid]
        base = per_idea_baseline.get(iid) or global_baseline
        if base is None or base["run_id"] == best_run["run_id"]:
            continue
        entry = {
            "idea_id": iid,
            "baseline_run": base["run_id"],
            "variant_run": best_run["run_id"],
            "fps_avg_delta": round(best_run["fps_avg"] - base["fps_avg"], 3),
        }
        if base["fps_avg"]:
            entry["fps_avg_pct"] = round(
                100.0 * (best_run["fps_avg"] - base["fps_avg"]) / base["fps_avg"], 2)
        if best_run["fps_1pct_low"] is not None and base["fps_1pct_low"] is not None:
            entry["fps_1pct_low_delta"] = round(best_run["fps_1pct_low"] - base["fps_1pct_low"], 3)
        if best_run["frametime_p99_ms"] is not None and base["frametime_p99_ms"] is not None:
            entry["frametime_p99_ms_delta"] = round(
                best_run["frametime_p99_ms"] - base["frametime_p99_ms"], 3)
        deltas.append(entry)

    return {
        "corpus": corpus_summary(store),
        "ideas_total": len(ideas),
        "ideas_by_status": ideas_by_status,
        "runs_total": len(runs),
        "runs_by_status": runs_by_status,
        "runs_done": runs_by_status.get("done", 0),
        "runs_failed": runs_by_status.get("failed", 0),
        "runs_crashed": sum(1 for r in runs if r.get("crashed")),
        "best_run": best,
        "best_run_per_idea": best_per_idea,
        "baseline": None if global_baseline is None else {
            "run_id": global_baseline["run_id"],
            "fps_avg": global_baseline["fps_avg"],
        },
        "deltas": deltas,
    }


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".json": "application/json; charset=utf-8",
    ".ico": "image/x-icon",
}


class Handler(BaseHTTPRequestHandler):
    server_version = "AALODashboard/1.0"
    protocol_version = "HTTP/1.1"

    @property
    def store(self) -> Store:
        return self.server.store  # type: ignore[attr-defined]

    def log_message(self, fmt, *args):
        if getattr(self.server, "quiet", False):
            return
        sys.stderr.write("[dashboard] %s %s\n" % (self.address_string(), fmt % args))

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def send_json(self, payload, code: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self._send(code, body, "application/json; charset=utf-8")

    def send_error_json(self, code: int, message: str) -> None:
        self.send_json({"error": message}, code)

    def read_body_json(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return None
        if length <= 0 or length > MAX_BODY:
            return None
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:  # noqa: BLE001
            return None

    # ---- routing --------------------------------------------------------
    def do_GET(self):
        try:
            self.route_get()
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            self.send_error_json(500, "internal error")

    do_HEAD = do_GET

    def do_POST(self):
        try:
            self.route_post()
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            self.send_error_json(500, "internal error")

    def route_get(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        if path.startswith("/api/"):
            return self.route_api_get(path, parsed.query)
        return self.serve_static(path)

    def route_api_get(self, path: str, query: str = ""):
        store = self.store
        if path == "/api/ideas":
            return self.send_json({"ideas": store.load_ideas()})
        if path in ("/api/corpus", "/api/corpus/"):
            return self.send_json({"runs": corpus_payload(store)})
        if path == "/api/corpus/compare":
            params = parse_qs(query)
            a = (params.get("a") or [None])[0]
            b = (params.get("b") or [None])[0]
            result = corpus_compare_payload(store, a or None, b or None)
            if result is None:
                return self.send_error_json(404, "unknown corpus run")
            return self.send_json(result)
        if path.startswith("/api/corpus/"):
            run_id = path[len("/api/corpus/"):].strip("/")
            run = store.load_corpus_run(run_id)
            if run is None:
                return self.send_error_json(404, "unknown corpus run %r" % run_id)
            return self.send_json(corpus_detail_payload(run))
        if path == "/api/runs":
            return self.send_json({"runs": runs_payload(store)})
        if path == "/api/summary":
            return self.send_json(summary_payload(store))
        if path == "/api/health":
            return self.send_json({"ok": True, "data_dir": str(store.root)})
        if path.startswith("/api/runs/"):
            run_id = path[len("/api/runs/"):].strip("/")
            run = store.load_run(run_id)
            if run is None:
                return self.send_error_json(404, "unknown run %r" % run_id)
            samples, total = store.load_samples(run_id)
            return self.send_json({
                "run_id": run_id,
                "manifest": run["manifest"],
                "metrics": run["metrics"],
                "samples": samples,
                "sample_count": total,
                "downsampled": total > len(samples),
            })
        return self.send_error_json(404, "no such endpoint")

    def route_post(self):
        path = unquote(urlparse(self.path).path)
        parts = [p for p in path.split("/") if p]
        if len(parts) == 4 and parts[0] == "api" and parts[1] == "ideas" \
                and parts[3] in ("status", "score"):
            idea_id = parts[2]
            field = parts[3]
            payload = self.read_body_json()
            if not isinstance(payload, dict) or field not in payload:
                return self.send_error_json(
                    400, "body must be a JSON object with a %r key" % field)
            value = payload[field]
            if field == "status":
                if not isinstance(value, str) or value not in IDEA_STATUSES:
                    return self.send_error_json(
                        400, "status must be one of %s" % sorted(IDEA_STATUSES))
            elif value is not None:
                value = _num(value)
                if value is None:
                    return self.send_error_json(400, "score must be a number or null")
            if not self.store.ideas_path.exists():
                return self.send_error_json(404, "ideas.json not found")
            try:
                updated = self.store.update_idea(idea_id, field, value)
            except Exception as exc:  # noqa: BLE001
                warn("write failed: %s" % exc)
                return self.send_error_json(500, "could not write ideas.json")
            if updated is None:
                return self.send_error_json(404, "unknown idea %r" % idea_id)
            return self.send_json({"ok": True, "idea": updated})
        return self.send_error_json(404, "no such endpoint")

    # ---- static ---------------------------------------------------------
    def serve_static(self, path: str):
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        try:
            target = (STATIC_DIR / rel).resolve()
            static_root = STATIC_DIR.resolve()
        except OSError:
            return self.send_error_json(500, "static dir missing")
        if static_root != target and static_root not in target.parents:
            return self.send_error_json(403, "forbidden")
        if not target.is_file():
            return self.send_error_json(404, "not found")
        try:
            body = target.read_bytes()
        except OSError as exc:
            warn("cannot read %s: %s" % (target, exc))
            return self.send_error_json(500, "cannot read file")
        self._send(200, body, CONTENT_TYPES.get(target.suffix.lower(), "application/octet-stream"))


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, handler, store: Store, quiet: bool = False):
        super().__init__(addr, handler)
        self.store = store
        self.quiet = quiet


def make_server(data_dir, host: str = "127.0.0.1", port: int = 8765, quiet: bool = False):
    return DashboardServer((host, port), Handler, Store(Path(data_dir)), quiet)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="AALO lab results dashboard")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--data", default=str(DEFAULT_DATA),
                    help="path to the shared data/ directory (default: ../data)")
    ap.add_argument("--quiet", action="store_true", help="suppress the access log")
    args = ap.parse_args(argv)

    data_dir = Path(args.data).expanduser()
    if not data_dir.exists():
        warn("data dir %s does not exist yet - serving empty results" % data_dir)

    httpd = make_server(data_dir, args.host, args.port, args.quiet)
    print("[dashboard] serving %s on http://%s:%d/" % (data_dir, args.host, args.port), flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[dashboard] bye", flush=True)
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
