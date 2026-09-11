"""End-to-end CLI runs against a temp MO2 tree.

Everything here goes through a real subprocess so argparse, the worker pools and
the exit codes are exercised the way a user hits them.
"""

import json
import zipfile
from pathlib import Path

import pytest

from conftest import luajit_compiles


MOD_A = """
function f(x)
    return math.pow(x, 2)
end
"""

MOD_B = """
function g(t, v)
    table.insert(t, v)
    my_global = 1
    return t
end
"""

TREE = {
    "ModA": {"a.script": MOD_A},
    "ModB": {"b.script": MOD_B},
}


@pytest.fixture
def tree(mods_tree):
    return mods_tree(TREE)


def _scripts(root):
    return sorted(root.glob("*/gamedata/scripts/*.script"))


def _snapshot(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in _scripts(root)}


def _assert_no_traceback(proc):
    assert "Traceback" not in proc.stderr, proc.stderr


# ---------------------------------------------------------------------------
# analyze-only
# ---------------------------------------------------------------------------

def test_analyze_only_writes_nothing(tree, run_cli):
    before = _snapshot(tree)
    listing_before = sorted(p.name for p in tree.rglob("*"))

    proc = run_cli(tree, check=True)

    _assert_no_traceback(proc)
    assert "ANALYSIS SUMMARY" in proc.stdout
    assert _snapshot(tree) == before
    assert sorted(p.name for p in tree.rglob("*")) == listing_before


def test_analyze_finds_both_mods(tree, run_cli):
    proc = run_cli(tree, check=True)
    assert "ModA" in proc.stdout
    assert "ModB" in proc.stdout
    assert "math_pow_simple" in proc.stdout
    assert "global_write" in proc.stdout


# ---------------------------------------------------------------------------
# reports
# ---------------------------------------------------------------------------

def test_json_report_has_the_expected_shape(tree, run_cli, tmp_path, read_json):
    out = tmp_path / "report.json"
    run_cli(tree, "--report", out, "-q", check=True)

    assert out.exists()
    data = read_json(out)
    assert set(data) == {"generated", "summary", "findings"}
    assert set(data["summary"]) == {"total", "green", "yellow", "red", "debug"}
    assert data["summary"]["green"] == 2
    assert data["summary"]["red"] == 1
    assert data["summary"]["total"] == 3

    patterns = {
        f["pattern"]
        for files in data["findings"].values()
        for entries in files.values()
        for f in entries
    }
    assert {"math_pow_simple", "table_insert_append", "global_write"} <= patterns


def test_txt_report_is_written(tree, run_cli, tmp_path):
    out = tmp_path / "report.txt"
    run_cli(tree, "--report", out, "-q", check=True)

    text = out.read_text(encoding="utf-8", errors="replace")
    assert "Anomaly Lua Script Analysis Report" in text
    assert "SUMMARY" in text
    assert "math_pow_simple" in text


def test_html_report_is_written(tree, run_cli, tmp_path):
    out = tmp_path / "report.html"
    run_cli(tree, "--report", out, "-q", check=True)

    html = out.read_text(encoding="utf-8", errors="replace")
    assert html.lstrip().startswith("<!DOCTYPE html>")
    assert "</html>" in html
    assert "math_pow_simple" in html


def test_report_run_still_writes_no_script_changes(tree, run_cli, tmp_path):
    before = _snapshot(tree)
    run_cli(tree, "--report", tmp_path / "r.json", "-q", check=True)
    assert _snapshot(tree) == before


# ---------------------------------------------------------------------------
# --fix / --revert
# ---------------------------------------------------------------------------

def test_fix_rewrites_scripts_and_creates_backups(tree, run_cli):
    proc = run_cli(tree, "--fix", "--single-thread", "-q", check=True)
    _assert_no_traceback(proc)

    a = tree / "ModA" / "gamedata" / "scripts" / "a.script"
    b = tree / "ModB" / "gamedata" / "scripts" / "b.script"

    assert "x*x" in a.read_text(encoding="utf-8")
    assert "math.pow" not in a.read_text(encoding="utf-8")
    assert "t[#t+1] = v" in b.read_text(encoding="utf-8")

    assert (tree / "ModA" / "gamedata" / "scripts" / "a.script.alao-bak").exists()
    assert (tree / "ModB" / "gamedata" / "scripts" / "b.script.alao-bak").exists()

    for path in (a, b):
        ok, err = luajit_compiles(path.read_text(encoding="utf-8"))
        assert ok, f"{path.name}: {err}"


def test_first_fix_creates_a_zip_backup_of_every_script(tree, run_cli):
    run_cli(tree, "--fix", "--single-thread", "-q", check=True)

    zips = list(tree.glob("scripts-backup-*.zip"))
    assert len(zips) == 1, f"expected one zip backup, found {zips}"

    with zipfile.ZipFile(zips[0]) as z:
        names = z.namelist()
    assert any(n.endswith("a.script") for n in names)
    assert any(n.endswith("b.script") for n in names)


def test_no_first_time_auto_backup_skips_the_zip(tree, run_cli):
    run_cli(tree, "--fix", "--single-thread", "--no-first-time-auto-backup", "-q", check=True)
    assert list(tree.glob("scripts-backup-*.zip")) == []


def test_revert_restores_byte_identical_files(tree, run_cli):
    before = _snapshot(tree)

    run_cli(tree, "--fix", "--single-thread", "-q", check=True)
    assert _snapshot(tree) != before

    proc = run_cli(tree, "--revert", "-q", stdin="y\n", check=True)
    _assert_no_traceback(proc)

    assert _snapshot(tree) == before
    assert list(tree.rglob("*.alao-bak")) == []


def test_a_second_fix_run_skips_files_that_already_have_a_backup(tree, run_cli):
    run_cli(tree, "--fix", "--single-thread", "-q", check=True)
    after_first = _snapshot(tree)

    proc = run_cli(tree, "--fix", "--single-thread", check=True)
    _assert_no_traceback(proc)

    assert "existing backups" in proc.stdout
    assert _snapshot(tree) == after_first


def test_backups_are_not_clobbered_by_the_second_run(tree, run_cli):
    original = _snapshot(tree)
    run_cli(tree, "--fix", "--single-thread", "-q", check=True)
    run_cli(tree, "--fix", "--single-thread", "-q", check=True)

    run_cli(tree, "--revert", "-q", stdin="y\n", check=True)
    assert _snapshot(tree) == original


def test_list_backups_reports_them_without_restoring(tree, run_cli):
    run_cli(tree, "--fix", "--single-thread", "-q", check=True)
    after_fix = _snapshot(tree)

    proc = run_cli(tree, "--list-backups", check=True)
    _assert_no_traceback(proc)
    assert "a.script" in proc.stdout
    assert _snapshot(tree) == after_fix
    assert len(list(tree.rglob("*.alao-bak"))) == 2


def test_fix_debug_comments_out_logging(mods_tree, run_cli):
    root = mods_tree({"ModLog": {"l.script": 'function f(x)\n    log("hi")\n    return x\nend\n'}})
    run_cli(root, "--fix", "--fix-debug", "--single-thread", "-q", check=True)

    text = (root / "ModLog" / "gamedata" / "scripts" / "l.script").read_text(encoding="utf-8")
    assert '-- log("hi")' in text
    ok, err = luajit_compiles(text)
    assert ok, err


# ---------------------------------------------------------------------------
# --direct
# ---------------------------------------------------------------------------

def test_direct_mode_analyzes_loose_scripts(tmp_path, run_cli):
    loose = tmp_path / "loose"
    loose.mkdir()
    (loose / "a.script").write_text(MOD_A.lstrip(), encoding="utf-8")
    (loose / "b.script").write_text(MOD_B.lstrip(), encoding="utf-8")

    proc = run_cli(loose, "--direct", check=True)
    _assert_no_traceback(proc)
    assert "Files analyzed: 2" in proc.stdout


def test_direct_mode_on_a_single_file(tmp_path, run_cli):
    path = tmp_path / "only.script"
    path.write_text(MOD_A.lstrip(), encoding="utf-8")

    proc = run_cli(path, "--direct", check=True)
    assert "Files analyzed: 1" in proc.stdout


def test_direct_mode_fixes_loose_scripts(tmp_path, run_cli):
    loose = tmp_path / "loose"
    loose.mkdir()
    path = loose / "a.script"
    path.write_text(MOD_A.lstrip(), encoding="utf-8")

    run_cli(loose, "--direct", "--fix", "--single-thread", "-q", check=True)

    assert "x*x" in path.read_text(encoding="utf-8")
    assert (loose / "a.script.alao-bak").exists()


def test_mods_layout_is_not_found_without_direct(tmp_path, run_cli):
    loose = tmp_path / "loose"
    loose.mkdir()
    (loose / "a.script").write_text(MOD_A.lstrip(), encoding="utf-8")

    proc = run_cli(loose)
    assert "No mods with scripts found." in proc.stdout


# ---------------------------------------------------------------------------
# --exclude
# ---------------------------------------------------------------------------

def test_exclude_drops_the_named_mod(tree, run_cli, tmp_path):
    exclude = tmp_path / "exclude.txt"
    exclude.write_text("ModB\n", encoding="utf-8")

    proc = run_cli(tree, "--exclude", exclude, check=True)
    _assert_no_traceback(proc)

    assert "Excluded 1 mods" in proc.stdout
    assert "Files analyzed: 1" in proc.stdout
    assert "global_write" not in proc.stdout


def test_exclude_ignores_comments_and_blank_lines(tree, run_cli, tmp_path):
    exclude = tmp_path / "exclude.txt"
    exclude.write_text("# a comment\n\n   \nModB\n", encoding="utf-8")

    proc = run_cli(tree, "--exclude", exclude, check=True)
    assert "Files analyzed: 1" in proc.stdout


def test_excluded_mod_is_not_fixed(tree, run_cli, tmp_path):
    exclude = tmp_path / "exclude.txt"
    exclude.write_text("ModB\n", encoding="utf-8")

    b = tree / "ModB" / "gamedata" / "scripts" / "b.script"
    before = b.read_bytes()

    run_cli(tree, "--fix", "--single-thread", "-q", "--exclude", exclude, check=True)

    assert b.read_bytes() == before
    assert not (tree / "ModB" / "gamedata" / "scripts" / "b.script.alao-bak").exists()
    assert (tree / "ModA" / "gamedata" / "scripts" / "a.script.alao-bak").exists()


def test_a_missing_exclude_file_only_warns(tree, run_cli, tmp_path):
    proc = run_cli(tree, "--exclude", tmp_path / "nope.txt", check=True)
    assert "Exclude file not found" in proc.stdout
    assert "Files analyzed: 2" in proc.stdout


# ---------------------------------------------------------------------------
# --timeout
# ---------------------------------------------------------------------------

def _pathological_source(n=1200):
    lines = ["function heavy(t)", "    local x = 0"]
    for i in range(n):
        k = i % 50 + 1
        lines.append(
            f"    x = x + math.floor(t[{k}]) * math.max({i}, t[{k}])"
            f" + string.len(tostring(t[{k}]))"
        )
    lines += ["    return x", "end", ""]
    return "\n".join(lines)


def test_a_tiny_timeout_is_reported_and_does_not_crash(mods_tree, run_cli):
    root = mods_tree({"ModSlow": {"heavy.script": _pathological_source()}})

    proc = run_cli(root, "--timeout", "0.01", "--single-thread")

    assert proc.returncode == 0
    _assert_no_traceback(proc)
    assert "Files with parse errors: 1" in proc.stdout
    assert "Files analyzed: 0" in proc.stdout


@pytest.mark.xfail(
    strict=True,
    reason="stalker_lua_lint.py:738 buckets TimeoutError together with SyntaxError "
           "under the 'Files with parse errors' counter, and even -v prints it as "
           "[PARSE ERROR]. A file ALAO ran out of time on is reported as a file it "
           "could not parse, which sends anyone debugging a corpus after the wrong "
           "problem.",
)
def test_a_timeout_is_reported_as_a_timeout_not_a_parse_error(mods_tree, run_cli):
    root = mods_tree({"ModSlow": {"heavy.script": _pathological_source()}})

    proc = run_cli(root, "--timeout", "0.01", "--single-thread", "-v")

    assert "Files with parse errors: 1" not in proc.stdout
    assert "timeout" in proc.stdout.lower()


@pytest.mark.xfail(
    strict=True,
    reason="reporter.py:368 _save_json writes only 'generated', 'summary' and "
           "'findings'. Parse failures, timeouts, crashes, per-file edit counts and "
           "dropped-edit counts never reach the JSON report, so a regression "
           "harness has to scrape stdout for everything about failures.",
)
def test_the_json_report_records_files_that_failed(mods_tree, run_cli, tmp_path, read_json):
    root = mods_tree({"ModSlow": {"heavy.script": _pathological_source()}})
    out = tmp_path / "report.json"

    run_cli(root, "--timeout", "0.01", "--single-thread", "--report", out, "-q")

    data = read_json(out)
    assert {"parse_failures", "timeouts"} & set(data), (
        f"JSON report carries no failure information, only {sorted(data)}"
    )


def test_the_same_file_analyzes_fine_with_a_generous_timeout(mods_tree, run_cli):
    root = mods_tree({"ModSlow": {"heavy.script": _pathological_source()}})

    proc = run_cli(root, "--timeout", "60", "--single-thread", check=True)

    _assert_no_traceback(proc)
    assert "Files analyzed: 1" in proc.stdout


@pytest.mark.xfail(
    strict=True,
    reason="--timeout only guards the analyze phase. transform_file_worker "
           "(stalker_lua_lint.py:110) calls transform_file with no timeout at all, "
           "so a file ALAO just declared too slow to analyze is still fully "
           "rewritten by --fix - exactly the file where a runaway transform is "
           "most likely.",
)
def test_a_timed_out_file_is_left_untouched_by_fix(mods_tree, run_cli):
    root = mods_tree({"ModSlow": {"heavy.script": _pathological_source()}})
    path = root / "ModSlow" / "gamedata" / "scripts" / "heavy.script"
    before = path.read_bytes()

    proc = run_cli(root, "--fix", "--timeout", "0.01", "--single-thread", "-q")

    assert proc.returncode == 0
    _assert_no_traceback(proc)
    assert path.read_bytes() == before


# ---------------------------------------------------------------------------
# encoding, end to end
# ---------------------------------------------------------------------------

CP1251_SCRIPT = (
    "-- Оптимизация\n"
    "function f(x)\n"
    "    return math.pow(x, 2)\n"
    "end\n"
)


def test_cp1251_scripts_survive_a_full_fix_revert_cycle(mods_tree, run_cli):
    root = mods_tree({"ModRu": {"ru.script": (CP1251_SCRIPT, "cp1251")}})
    path = root / "ModRu" / "gamedata" / "scripts" / "ru.script"
    before = path.read_bytes()

    run_cli(root, "--fix", "--single-thread", "-q", check=True)

    fixed = path.read_bytes()
    assert fixed != before
    text = fixed.decode("cp1251")
    assert "Оптимизация" in text
    assert "x*x" in text

    run_cli(root, "--revert", "-q", stdin="y\n", check=True)
    assert path.read_bytes() == before
