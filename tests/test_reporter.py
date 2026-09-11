"""Reporter-side coverage for the I-031 global-write split.

The report is the whole point of the pattern - global writes are never
rewritten - so the txt/html/json paths and the impact table get their own
checks rather than only being exercised end-to-end through the CLI.
"""

from pathlib import Path

import pytest

from models import Finding
from reporter import PERFORMANCE_IMPACT, Reporter, highlight_code_match


def _finding(pattern, variant, line=3, count=2):
    return Finding(
        pattern_name=pattern,
        severity="RED",
        line_num=line,
        message=f"{variant} ({count} writes, lines 3, 7)",
        details={"variable": variant, "count": count, "lines": [3, 7],
                 "kind": "accidental" if pattern == "global_write" else "module"},
        source_line=f"    {variant} = 1",
    )


@pytest.fixture
def reporter():
    r = Reporter()
    r.add_finding("ModG", Path("g.script"), _finding("global_write", "leaked"))
    r.add_finding("ModG", Path("g.script"), _finding("module_global_write", "settings"))
    return r


def test_both_patterns_have_an_impact_rating():
    assert PERFORMANCE_IMPACT["global_write"] == "low"
    assert PERFORMANCE_IMPACT["module_global_write"] == "low"


def test_highlight_covers_both_patterns():
    for pattern in ("global_write", "module_global_write"):
        out = highlight_code_match("    leaked = 1", {"variable": "leaked"}, pattern)
        assert "<span" in out, pattern
        assert "leaked" in out


def test_counts_reconcile_across_the_two_rollups(reporter):
    by_pattern = reporter.counts_by_pattern()
    by_severity = reporter.counts_by_severity()
    assert by_pattern == {"global_write": 1, "module_global_write": 1}
    assert sum(by_pattern.values()) == sum(by_severity.values()) == reporter.total_findings()


def test_json_report_carries_the_grouping_details(reporter, tmp_path, read_json):
    out = tmp_path / "r.json"
    reporter.save(out)
    data = read_json(out)
    assert data["findings_by_pattern"]["module_global_write"] == 1
    entries = data["findings"]["ModG"]["g.script"]
    grouped = [e for e in entries if e["pattern"] == "global_write"][0]
    assert grouped["details"]["count"] == 2
    assert grouped["details"]["lines"] == [3, 7]


def test_txt_report_mentions_both_patterns(reporter, tmp_path):
    out = tmp_path / "r.txt"
    reporter.save(out)
    text = out.read_text(encoding="utf-8")
    assert "global_write" in text
    assert "module_global_write" in text


def test_html_report_renders(reporter, tmp_path):
    pytest.importorskip("jinja2")
    out = tmp_path / "r.html"
    reporter.save(out)
    html_text = out.read_text(encoding="utf-8")
    assert "global_write" in html_text
    assert "module_global_write" in html_text
