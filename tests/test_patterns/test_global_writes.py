"""I-031: global_write is split and grouped.

An Anomaly `.script` file is a module whose top-level names are meant to be
global, so module-level writes are the convention. Only the forgotten-`local`
case is RED by default, and it is grouped one finding per file+name.
"""

from conftest import find_one, findings_named, pattern_names


# the convention: module state / an export at the top of the file
MODULE_LEVEL = """
settings = {}
enabled = true

function f()
    return enabled
end
"""

# module state mutated from inside the file's own function - still deliberate
MODULE_LEVEL_MUTATED = """
enabled = true

function toggle()
    enabled = not enabled
end
"""

# the same, but the name is defined by a top-level `function`
FUNCTION_NAME_REASSIGNED = """
function handler()
    return 1
end

function install()
    handler = function() return 2 end
end
"""

# the real defect: a forgotten `local` inside a body
FORGOTTEN_LOCAL = """
function f(o)
    wpn = o:weapon()
    return wpn
end
"""

# the noise case that motivated the idea: one name written over and over
REPEATED = """
function f(n)
    wpn_name = "a"
    wpn_name = "b"
    wpn_name = "c"
    return wpn_name
end
"""

TWO_NAMES = """
function f()
    alpha = 1
    beta = 2
end
"""

# already-skipped shapes: leading underscore and ALL_CAPS constants
SKIPPED_SHAPES = """
function f()
    _scratch = 1
    CONSTANT = 2
end
"""


def test_module_level_global_is_not_red_by_default(analyze):
    assert "global_write" not in pattern_names(analyze(MODULE_LEVEL))
    assert "module_global_write" not in pattern_names(analyze(MODULE_LEVEL))


def test_module_level_global_is_reported_with_show_globals(analyze):
    findings = analyze(MODULE_LEVEL, show_globals=True)
    names = {f.details["variable"] for f in findings_named(findings, "module_global_write")}
    assert names == {"settings", "enabled"}
    assert "global_write" not in pattern_names(findings)


def test_module_state_mutated_from_a_function_is_not_accidental(analyze):
    assert "global_write" not in pattern_names(analyze(MODULE_LEVEL_MUTATED))
    finding = find_one(analyze(MODULE_LEVEL_MUTATED, show_globals=True),
                       "module_global_write")
    assert finding.details["variable"] == "enabled"
    assert finding.details["count"] == 2


def test_top_level_function_name_counts_as_module_level(analyze):
    assert "global_write" not in pattern_names(analyze(FUNCTION_NAME_REASSIGNED))


def test_forgotten_local_is_still_red(analyze):
    finding = find_one(analyze(FORGOTTEN_LOCAL), "global_write")
    assert finding.severity == "RED"
    assert finding.details["variable"] == "wpn"
    assert finding.details["kind"] == "accidental"
    assert finding.details["count"] == 1
    assert "local" in finding.message


def test_repeated_writes_collapse_into_one_finding(analyze):
    findings = findings_named(analyze(REPEATED), "global_write")
    assert len(findings) == 1
    finding = findings[0]
    assert finding.details["count"] == 3
    assert len(finding.details["lines"]) == 3
    assert finding.line_num == min(finding.details["lines"])
    assert "3 writes" in finding.message


def test_distinct_names_stay_distinct(analyze):
    findings = findings_named(analyze(TWO_NAMES), "global_write")
    assert {f.details["variable"] for f in findings} == {"alpha", "beta"}


def test_underscore_and_allcaps_are_skipped(analyze):
    findings = analyze(SKIPPED_SHAPES, show_globals=True)
    assert "global_write" not in pattern_names(findings)
    assert "module_global_write" not in pattern_names(findings)


def test_report_global_writes_off_suppresses_everything(analyze):
    findings = analyze(FORGOTTEN_LOCAL, show_globals=True, report_global_writes=False)
    assert "global_write" not in pattern_names(findings)
    assert "module_global_write" not in pattern_names(findings)
