"""API shape tests for the AALO dashboard (dashboard/server.py).

Run:  py -3.12 -m pytest tests/test_dashboard.py -q
"""

from __future__ import annotations

import json
import shutil
import socket
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
DASHBOARD = REPO / "dashboard"
FIXTURES = DASHBOARD / "fixtures"

sys.path.insert(0, str(DASHBOARD))

import server as dash  # noqa: E402


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def get(base: str, path: str):
    with urllib.request.urlopen(base + path, timeout=10) as r:
        return r.status, json.loads(r.read().decode("utf-8"))


def post(base: str, path: str, payload):
    req = urllib.request.Request(
        base + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


@pytest.fixture(scope="module")
def data_dir(tmp_path_factory):
    """A writable copy of the fixtures, so status/score POSTs are isolated."""
    dest = tmp_path_factory.mktemp("aalo-data")
    shutil.copytree(FIXTURES, dest, dirs_exist_ok=True)
    return dest


@pytest.fixture(scope="module")
def base(data_dir):
    port = free_port()
    httpd = dash.make_server(data_dir, "127.0.0.1", port, quiet=True)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        yield "http://127.0.0.1:%d" % port
    finally:
        httpd.shutdown()
        httpd.server_close()
        t.join(timeout=5)


# ---------------------------------------------------------------- ideas ----

def test_ideas_shape(base):
    status, body = get(base, "/api/ideas")
    assert status == 200
    ideas = body["ideas"]
    assert len(ideas) == 2
    required = {"id", "title", "category", "hypothesis", "change", "measure",
                "expected_gain", "risk", "status", "score", "parent",
                "generation", "notes"}
    for idea in ideas:
        assert required <= set(idea)
        assert idea["status"] in dash.IDEA_STATUSES
        assert idea["expected_gain"] in {"low", "med", "high"}
        assert idea["risk"] in {"low", "med", "high"}


# ----------------------------------------------------------------- runs ----

def test_runs_list_shape(base):
    status, body = get(base, "/api/runs")
    assert status == 200
    runs = body["runs"]
    assert len(runs) == 3
    # newest first
    assert runs[0]["run_id"] == "20260902-090000-shadow-cascade"
    for run in runs:
        assert {"run_id", "idea_id", "status", "started", "fps_avg",
                "fps_1pct_low", "frametime_p99_ms", "duration_s",
                "crashed", "config_diff"} <= set(run)
        assert run["status"] in {"planned", "running", "done", "failed"}
    by_id = {r["run_id"]: r for r in runs}
    assert by_id["20260902-090000-shadow-cascade"]["crashed"] is True
    assert by_id["20260901-114500-alife-radius"]["idea_id"] == "I-001"
    assert by_id["20260901-114500-alife-radius"]["fps_avg"] == pytest.approx(58.9)


def test_run_detail_downsamples(base):
    status, body = get(base, "/api/runs/20260901-114500-alife-radius")
    assert status == 200
    assert body["manifest"]["idea_id"] == "I-001"
    assert body["metrics"]["crashed"] is False
    assert body["manifest"]["config_diff"]["alife.ltx:switch_distance"] == [150, 110]
    assert body["sample_count"] > dash.MAX_SAMPLES
    assert body["downsampled"] is True
    samples = body["samples"]
    assert 0 < len(samples) <= dash.MAX_SAMPLES
    assert {"t_s", "frametime_ms", "fps"} == set(samples[0])
    assert all(s["frametime_ms"] > 0 for s in samples)
    # time stays monotonic through downsampling
    assert all(samples[i]["t_s"] <= samples[i + 1]["t_s"] for i in range(len(samples) - 1))


def test_unknown_run_is_404(base):
    try:
        get(base, "/api/runs/nope-does-not-exist")
        pytest.fail("expected 404")
    except urllib.error.HTTPError as e:
        assert e.code == 404


def test_run_id_traversal_rejected(base):
    for bad in ("..", "%2e%2e%2f%2e%2e"):
        try:
            get(base, "/api/runs/" + bad)
        except urllib.error.HTTPError as e:
            assert e.code in (400, 404)


# -------------------------------------------------------------- summary ----

def test_summary_shape(base):
    status, body = get(base, "/api/summary")
    assert status == 200
    assert body["runs_total"] == 3
    assert body["runs_done"] == 2
    assert body["runs_failed"] == 1
    assert body["runs_crashed"] == 1
    assert body["ideas_total"] == 2
    assert body["ideas_by_status"].get("kept") == 1
    assert body["best_run"]["run_id"] == "20260901-114500-alife-radius"
    assert body["baseline"]["run_id"] == "20260901-101500-baseline"
    deltas = {d["idea_id"]: d for d in body["deltas"]}
    assert "I-001" in deltas
    d = deltas["I-001"]
    assert d["baseline_run"] == "20260901-101500-baseline"
    assert d["variant_run"] == "20260901-114500-alife-radius"
    assert d["fps_avg_delta"] == pytest.approx(6.5, abs=0.01)
    assert d["fps_avg_pct"] > 0


# --------------------------------------------------------------- writes ----

def test_post_status_and_score_persist(base, data_dir):
    status, body = post(base, "/api/ideas/I-002/status", {"status": "queued"})
    assert status == 200 and body["idea"]["status"] == "queued"

    status, body = post(base, "/api/ideas/I-002/score", {"score": 0.41})
    assert status == 200 and body["idea"]["score"] == pytest.approx(0.41)

    on_disk = json.loads((data_dir / "ideas.json").read_text(encoding="utf-8"))
    idea = next(i for i in on_disk["ideas"] if i["id"] == "I-002")
    assert idea["status"] == "queued"
    assert idea["score"] == pytest.approx(0.41)
    # the rest of the record survives the patch
    assert idea["category"] == "render"
    assert idea["parent"] == "I-001"

    status, _ = post(base, "/api/ideas/I-002/score", {"score": None})
    assert status == 200


def test_post_rejects_bad_input(base):
    assert post(base, "/api/ideas/I-001/status", {"status": "bogus"})[0] == 400
    assert post(base, "/api/ideas/I-001/status", {})[0] == 400
    assert post(base, "/api/ideas/I-001/score", {"score": "abc"})[0] == 400
    assert post(base, "/api/ideas/NOPE/status", {"status": "kept"})[0] == 404


# ------------------------------------------------------------ tolerance ----

def test_missing_data_dir_returns_empty(tmp_path):
    port = free_port()
    httpd = dash.make_server(tmp_path / "not-there", "127.0.0.1", port, quiet=True)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        b = "http://127.0.0.1:%d" % port
        assert get(b, "/api/ideas")[1] == {"ideas": []}
        assert get(b, "/api/runs")[1] == {"runs": []}
        s = get(b, "/api/summary")[1]
        assert s["runs_total"] == 0 and s["best_run"] is None and s["deltas"] == []
    finally:
        httpd.shutdown()
        httpd.server_close()
        t.join(timeout=5)


def test_corrupt_files_do_not_500(tmp_path):
    (tmp_path / "ideas.json").write_text("{not json", encoding="utf-8")
    run = tmp_path / "runs" / "20260101-000000-broken"
    run.mkdir(parents=True)
    (run / "manifest.json").write_text("[]", encoding="utf-8")
    (run / "samples.csv").write_text("t_s,frametime_ms,fps\nx,y,z\n", encoding="utf-8")

    port = free_port()
    httpd = dash.make_server(tmp_path, "127.0.0.1", port, quiet=True)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        b = "http://127.0.0.1:%d" % port
        assert get(b, "/api/ideas") == (200, {"ideas": []})
        code, body = get(b, "/api/runs")
        assert code == 200 and len(body["runs"]) == 1
        code, body = get(b, "/api/runs/20260101-000000-broken")
        assert code == 200 and body["samples"] == [] and body["metrics"] == {}
        assert get(b, "/api/summary")[0] == 200
    finally:
        httpd.shutdown()
        httpd.server_close()
        t.join(timeout=5)


# --------------------------------------------------------------- static ----

def test_index_and_assets_served(base):
    for path, needle in (("/", "AALO Lab"), ("/app.js", "api/summary"), ("/style.css", "--bg")):
        with urllib.request.urlopen(base + path, timeout=10) as r:
            assert r.status == 200
            assert needle in r.read().decode("utf-8")


def test_static_traversal_blocked(base):
    try:
        urllib.request.urlopen(base + "/../server.py", timeout=10)
        pytest.fail("expected an error")
    except urllib.error.HTTPError as e:
        assert e.code in (403, 404)


# ----------------------------------------------------------- unit bits -----

def test_downsample_keeps_peaks_and_order():
    rows = [{"t_s": float(i), "frametime_ms": 16.0, "fps": 62.5} for i in range(10000)]
    rows[4321]["frametime_ms"] = 999.0
    out = dash.downsample(rows, dash.MAX_SAMPLES)
    assert len(out) <= dash.MAX_SAMPLES
    assert max(r["frametime_ms"] for r in out) == 999.0
    assert all(out[i]["t_s"] < out[i + 1]["t_s"] for i in range(len(out) - 1))
    assert dash.downsample(rows[:5], dash.MAX_SAMPLES) == rows[:5]


def test_write_json_atomic_leaves_no_temp(tmp_path):
    target = tmp_path / "ideas.json"
    dash.write_json_atomic(target, {"ideas": []})
    assert json.loads(target.read_text(encoding="utf-8")) == {"ideas": []}
    assert [p.name for p in tmp_path.iterdir()] == ["ideas.json"]


# --------------------------------------------------------------- corpus ----

BASE_RUN = "20260908-101500-gamma-base"
FIX_RUN = "20260909-094500-gamma-idem-fix"
VERIFY_RUN = "20260909-161000-gamma-verify"


def test_corpus_list_shape(base):
    status, body = get(base, "/api/corpus")
    assert status == 200
    runs = body["runs"]
    assert len(runs) == 3
    # newest started first
    assert [r["run_id"] for r in runs] == [VERIFY_RUN, FIX_RUN, BASE_RUN]
    for run in runs:
        assert {"run_id", "alao_commit", "alao_commit_short", "alao_dirty",
                "corpus", "corpus_files", "flags", "started", "finished",
                "status", "analyze_s", "fix_s", "findings_total",
                "findings_by_severity", "parse_failures",
                "compile_failures_after_fix", "idempotence_violations",
                "health"} <= set(run)
        assert run["health"] in {"ok", "warn", "fail"}
        assert run["corpus_files"] == 2079
        assert len(run["alao_commit_short"]) == 8
    by_id = {r["run_id"]: r for r in runs}
    # the broken run: compile failures + idempotence violations -> fail
    assert by_id[BASE_RUN]["health"] == "fail"
    assert by_id[BASE_RUN]["parse_failures"] == 3
    assert by_id[BASE_RUN]["compile_failures_after_fix"] == 2
    assert by_id[BASE_RUN]["idempotence_violations"] == 2
    # parse failures only -> warn
    assert by_id[FIX_RUN]["health"] == "warn"
    assert by_id[FIX_RUN]["parse_failures"] == 1
    # the clean run
    assert by_id[VERIFY_RUN]["health"] == "ok"
    assert by_id[VERIFY_RUN]["parse_failures"] == 0
    assert by_id[VERIFY_RUN]["alao_dirty"] is True
    # findings_total is the sum of findings_by_pattern
    assert by_id[VERIFY_RUN]["findings_total"] == 2710
    # two distinct commits across three runs
    assert len({r["alao_commit"] for r in runs}) == 2


def test_corpus_detail_shape(base):
    status, body = get(base, "/api/corpus/" + BASE_RUN)
    assert status == 200
    assert body["run_id"] == BASE_RUN
    assert body["manifest"]["corpus"] == "gamma-0.9.4"
    patterns = body["patterns"]
    assert patterns[0]["pattern"] == "global-lookup-in-hot-fn"
    counts = [p["count"] for p in patterns]
    assert counts == sorted(counts, reverse=True)
    assert body["findings_by_pattern"]["table-concat-in-loop"] == 402
    assert body["findings_by_severity"]["RED"] == 88
    assert len(body["parse_failure_list"]) == 3
    assert {"file", "error"} <= set(body["parse_failure_list"][0])
    assert len(body["compile_failure_list"]) == 2
    assert body["idempotence_violation_list"] == [
        "mods/Food Drug Drink Animations/gamedata/scripts/fdda_main.script",
        "mods/Grok Body Health Realism/gamedata/scripts/grok_bhr.script",
    ]
    assert body["health"] == "fail"


def test_corpus_unknown_run_is_404(base):
    try:
        get(base, "/api/corpus/nope-does-not-exist")
        pytest.fail("expected 404")
    except urllib.error.HTTPError as e:
        assert e.code == 404


def test_corpus_traversal_rejected(base):
    for bad in ("..", "%2e%2e%2f%2e%2e"):
        try:
            get(base, "/api/corpus/" + bad)
        except urllib.error.HTTPError as e:
            assert e.code in (400, 404)


def test_corpus_compare_defaults_to_latest_two(base):
    status, body = get(base, "/api/corpus/compare")
    assert status == 200
    assert body["a"]["run_id"] == VERIFY_RUN
    assert body["b"]["run_id"] == FIX_RUN
    assert body["same_commit"] is True
    assert body["findings_total"] == {"a": 2710, "b": 2696, "delta": 14}
    by_pattern = {p["pattern"]: p for p in body["patterns"]}
    assert by_pattern["global-lookup-in-hot-fn"]["delta"] == 7
    assert by_pattern["table-concat-in-loop"]["delta"] == -3
    # biggest absolute delta first
    deltas = [abs(p["delta"]) for p in body["patterns"]]
    assert deltas == sorted(deltas, reverse=True)
    parse = body["failures"]["parse_failures"]
    assert parse == {
        "a_count": 0, "b_count": 1, "added": [],
        "removed": ["mods/Boomsticks and Sharpsticks/gamedata/scripts/bas_ui_mcm.script"],
        "unchanged": [],
    }
    assert body["timing"]["analyze_s"]["delta"] == pytest.approx(-22.3, abs=0.01)
    assert body["timing"]["fix_s"]["delta"] == pytest.approx(-7.6, abs=0.01)


def test_corpus_compare_explicit_pair(base):
    status, body = get(base, "/api/corpus/compare?a=%s&b=%s" % (FIX_RUN, BASE_RUN))
    assert status == 200
    assert body["a"]["run_id"] == FIX_RUN and body["b"]["run_id"] == BASE_RUN
    assert body["same_commit"] is False
    # the idempotence and compile regressions are fixed in a, so all "removed"
    idem = body["failures"]["idempotence_violations"]
    assert idem["a_count"] == 0 and idem["b_count"] == 2
    assert len(idem["removed"]) == 2 and idem["added"] == []
    compile_diff = body["failures"]["compile_failures_after_fix"]
    assert compile_diff["a_count"] == 0 and len(compile_diff["removed"]) == 2
    by_pattern = {p["pattern"]: p for p in body["patterns"]}
    assert by_pattern["nil-guard-before-alive"]["new"] is True
    assert by_pattern["nil-guard-before-alive"]["b"] == 0
    sev = {s["severity"]: s for s in body["severity"]}
    assert sev["RED"]["delta"] == -17


def test_corpus_compare_unknown_run_is_404(base):
    try:
        get(base, "/api/corpus/compare?a=nope&b=" + BASE_RUN)
        pytest.fail("expected 404")
    except urllib.error.HTTPError as e:
        assert e.code == 404


def test_summary_carries_corpus_block(base):
    status, body = get(base, "/api/summary")
    assert status == 200
    c = body["corpus"]
    assert c["runs_total"] == 3
    assert c["latest"]["run_id"] == VERIFY_RUN
    assert c["health"] == "ok"
    assert c["findings_total"] == 2710
    assert c["parse_failures"] == 0
    assert c["compile_failures_after_fix"] == 0
    assert c["idempotence_violations"] == 0
    # trend is oldest-first, one point per run
    assert [t["run_id"] for t in c["trend"]] == [BASE_RUN, FIX_RUN, VERIFY_RUN]
    assert c["trend"][0]["health"] == "fail"
    # the pre-existing summary keys survive
    assert body["runs_total"] == 3 and body["ideas_total"] == 2


def test_ideas_archive(base):
    status, body = get(base, "/api/ideas-archive")
    assert status == 200
    ideas = body["ideas"]
    assert [i["id"] for i in ideas] == ["G-001", "G-002"]
    assert all(i["status"] == "pruned" for i in ideas)
    assert body["source"].endswith("ideas-game-knobs.json")


def test_corpus_tolerates_missing_and_corrupt(tmp_path):
    """No corpus/ dir at all, then a run with a broken manifest and results."""
    port = free_port()
    httpd = dash.make_server(tmp_path, "127.0.0.1", port, quiet=True)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        b = "http://127.0.0.1:%d" % port
        assert get(b, "/api/corpus") == (200, {"runs": []})
        assert get(b, "/api/ideas-archive")[1]["ideas"] == []
        s = get(b, "/api/summary")[1]["corpus"]
        assert s["runs_total"] == 0 and s["latest"] is None and s["health"] == "unknown"
        cmp_body = get(b, "/api/corpus/compare")[1]
        assert cmp_body["a"] is None and cmp_body["patterns"] == []

        broken = tmp_path / "corpus" / "20260101-000000-broken"
        broken.mkdir(parents=True)
        (broken / "manifest.json").write_text("{not json", encoding="utf-8")
        (broken / "results.json").write_text("[]", encoding="utf-8")
        code, body = get(b, "/api/corpus")
        assert code == 200 and len(body["runs"]) == 1
        run = body["runs"][0]
        # no usable results.json yet: never claim a clean bill of health
        assert run["findings_total"] == 0 and run["health"] == "unknown"
        assert run["has_results"] is False
        # ordering falls back to the YYYYMMDD-HHMMSS run id when started is absent
        assert dash.corpus_started_key(run) == "2026-01-01T00:00:00"
        assert get(b, "/api/summary")[1]["corpus"]["pending"] == 1
        code, detail = get(b, "/api/corpus/20260101-000000-broken")
        assert code == 200 and detail["patterns"] == []
        assert get(b, "/api/summary")[0] == 200
    finally:
        httpd.shutdown()
        httpd.server_close()
        t.join(timeout=5)
