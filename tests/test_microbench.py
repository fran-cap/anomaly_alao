"""
tools/microbench.py and the bench/ snippet corpus.

Two flavours here:

* fast, always on - the snippets parse, the chunks LuaJIT-compiles, and a tiny-N
  run of one pair actually produces numbers. Seconds, not minutes.
* slow, opt-in with `--bench` (or `-m slow`) - the coverage guard that fails when
  a GREEN pattern in ast_analyzer.py has no bench entry, and a real short run of
  the whole corpus. The coverage guard is slow-marked because it is a policy
  check on the beam, not a unit test of a module: it should gate "we added a
  pattern" reviews, not every `pytest -q`.
"""

from __future__ import annotations

import ast as pyast
import faulthandler
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = REPO_ROOT / "tools"
BENCH_DIR = REPO_ROOT / "bench"

if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

microbench = pytest.importorskip("microbench")


@pytest.fixture(autouse=True)
def _quiet_luajit_seh():
    """Silence faulthandler's first-chance chatter about LuaJIT's own unwinding.

    LuaJIT raises Windows SEH exception 0xe24c4a02 ('LJ\\x02') internally to
    unwind - the `pairs` bench trips it every time. It is caught inside the VM,
    the numbers are unaffected, and nothing propagates to Python, but pytest
    turns faulthandler on by default and prints a scary traceback for each one.
    """
    was_enabled = faulthandler.is_enabled()
    faulthandler.disable()
    try:
        yield
    finally:
        if was_enabled:
            faulthandler.enable()


# ---------------------------------------------------------------------------
# which analyzer patterns need a bench pair
# ---------------------------------------------------------------------------

# GREEN findings that describe *removing* code or *guarding* it rather than
# rewriting one construct into a faster one. There is no "original vs rewrite"
# pair to time: the rewrite is "nothing". Each one is listed with why.
EXEMPT_PREFIXES = {
    "dead_code_": "removal - the rewrite is the empty string, nothing to time",
}
EXEMPT_EXACT = {
    "debug_statement": "comment-out, not a construct swap",
    "potential_nil_access": "safety guard, gated by --fix-nil; it adds work by design",
    "global_write": "RED, report only",
    "per_frame_callback": "advisory, no rewrite",
    "constant_condition": "removal",
    "unnecessary_else": "removal",
    "unused_local_variable": "removal",
    "unused_local_function": "removal",
}

# A dynamic pattern family (pattern_name built with an f-string) that one bench
# pair stands in for.
FAMILY_REPRESENTATIVE = {
    "repeated_": "repeated_db_actor",
}

def green_pattern_names() -> list[str]:
    """Pattern names that a `--fix` run can rewrite, read statically out of
    ast_analyzer.py.

    Static, not by running the analyzer, because we want every emission site
    including the ones no fixture happens to hit. Sites whose `severity=` is a
    variable rather than a literal (repeated_*, vector_alloc_in_loop, ...) are
    included too: we cannot prove they are not GREEN, so they have to be either
    benched or explicitly exempted.

    An f-string pattern_name contributes its literal prefix, so the whole
    dead_code_after_* family shows up as `dead_code_after_`.
    """
    src = (REPO_ROOT / "ast_analyzer.py").read_text(encoding="utf-8")
    tree = pyast.parse(src)
    names: set[str] = set()
    for node in pyast.walk(tree):
        if not (isinstance(node, pyast.Call) and getattr(node.func, "id", None) == "Finding"):
            continue
        kw = {k.arg: k.value for k in node.keywords}
        sev, pat = kw.get("severity"), kw.get("pattern_name")
        literal_green = isinstance(sev, pyast.Constant) and sev.value == "GREEN"
        maybe_green = not isinstance(sev, pyast.Constant)
        if not (literal_green or maybe_green):
            continue
        if isinstance(pat, pyast.Constant):
            names.add(pat.value)
        elif isinstance(pat, pyast.JoinedStr) and pat.values:
            lead = pat.values[0]
            if isinstance(lead, pyast.Constant):
                names.add(lead.value)
    return sorted(names)


# ---------------------------------------------------------------------------
# fast tests
# ---------------------------------------------------------------------------

def test_every_bench_file_parses():
    cases = microbench.load_cases(BENCH_DIR)
    assert cases, "bench/ is empty"
    for c in cases:
        assert c.status in ("shipped", "proposed", "retarget"), c.pattern
        assert c.path.stem == c.pattern, f"{c.path.name} should be named {c.pattern}.lua"
        assert c.original.strip() and c.rewrite.strip() and c.sink.strip()
        assert c.original.strip() != c.rewrite.strip(), f"{c.pattern}: the two arms are identical"


def test_bad_directive_is_rejected(tmp_path):
    p = tmp_path / "x.lua"
    p.write_text("-- @pattern x\n-- @title x\n-- @nonsense 1\n-- @original\n-- @rewrite\n-- @sink\n1\n",
                 encoding="utf-8")
    with pytest.raises(ValueError, match="unknown directive"):
        microbench.parse_bench_file(p)


def test_missing_section_is_rejected(tmp_path):
    p = tmp_path / "x.lua"
    p.write_text("-- @pattern x\n-- @title x\n-- @original\nlocal a = 1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="missing section"):
        microbench.parse_bench_file(p)


def test_every_chunk_compiles_under_luajit():
    """Both arms of every pair must be valid LuaJIT 2.0."""
    lua = pytest.importorskip("lupa.luajit20")
    rt = lua.LuaRuntime(unpack_returned_tuples=False)
    loadstring = rt.eval("loadstring")
    for c in microbench.load_cases(BENCH_DIR):
        for arm in ("original", "rewrite"):
            loaded = loadstring(c.chunk(arm), f"{c.pattern}:{arm}")
            f = loaded[0] if isinstance(loaded, tuple) else loaded
            assert f is not None, f"{c.pattern}/{arm} does not compile: {loaded}"


def test_vm_fingerprint_matches_the_anomaly_build():
    vm = microbench.vm_fingerprint()
    assert vm["jit_opt_flags"] == microbench.EXPECTED_JIT_OPT_FLAGS, vm["warnings"]
    assert vm["jit_version_num"] // 10000 == 2
    assert (vm["jit_version_num"] // 100) % 100 == 0, "must be the LuaJIT 2.0 branch"


def test_tiny_run_produces_numbers():
    """The smoke test: one pair, absurdly small N, just prove the pipeline runs."""
    case = next(c for c in microbench.load_cases(BENCH_DIR) if c.pattern == "string_len")
    results = microbench.run_case(case, reps=2, modes=("jit_on", "jit_off"), n_override=2000)
    assert len(results) == 1
    r = results[0]
    assert r.error is None, r.error
    for mode in ("jit_on", "jit_off"):
        assert r.speedup(mode) > 0
        assert len(r.modes[mode].original.runs) == 2
    assert r.g2 in (True, False)


def test_sweep_case_yields_one_result_per_k():
    case = next(c for c in microbench.load_cases(BENCH_DIR) if c.pattern == "counter_append")
    assert case.iters, "counter_append should sweep K"
    results = microbench.run_case(case, reps=1, modes=("jit_on",), n_override=2000)
    assert [r.k for r in results] == case.iters
    # the outer count is scaled so total work stays ~constant
    assert results[0].modes["jit_on"].original.n > results[-1].modes["jit_on"].original.n


def test_json_shape():
    case = next(c for c in microbench.load_cases(BENCH_DIR) if c.pattern == "string_len")
    results = microbench.run_case(case, reps=1, modes=("jit_on",), n_override=2000)
    blob = microbench.results_to_json(results, {"generated": "now"})
    assert blob["schema"] == "alao.microbench/1"
    assert blob["g2_summary"]["string_len"] in ("pass", "fail")
    entry = blob["cases"][0]
    assert entry["pattern"] == "string_len"
    assert "speedup_best" in entry["modes"]["jit_on"]
    json.dumps(blob)  # must be serialisable


def test_corpus_k_must_be_inside_the_sweep(tmp_path):
    """Declaring where a pattern runs and not measuring there is the defect."""
    p = tmp_path / "x.lua"
    p.write_text(
        "-- @pattern x\n-- @title x\n-- @iters 100 2000\n-- @corpus_k 3 10\n"
        "-- @original\nlocal a = 1\n-- @rewrite\nlocal a = 2\n-- @sink\n1\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="never measures the loop lengths"):
        microbench.parse_bench_file(p)


def _fake_rows(case, flags):
    class FakeMode:
        def __init__(self, up):
            self.speedup = up

    return [microbench.CaseResult(
        case=case, k=k, modes={m: FakeMode(2.0 if ok else 1.0) for m in microbench.MODES},
    ) for k, ok in flags]


def test_corpus_k_verdict_leads_and_names_the_useless_wins():
    """A transform that only wins where it never runs must read as FAIL.

    This is the failure I-039 hit on string_concat_in_loop: 18 corpus sites at
    K=3-10, a headline figure measured in the low hundreds. Not a wrong number -
    a correct number about the wrong part of the curve.
    """
    case = microbench.BenchCase(
        pattern="fake", title="t", status="shipped", path=Path("fake.lua"),
        setup="", original="", rewrite="", sink="1",
        iters=[5, 100], corpus_k=[3, 10],
    )
    summary = microbench.g2_summary(_fake_rows(case, [(5, False), (100, True)]))
    assert summary["fake"].startswith("at corpus K 3-10: FAIL")
    assert "does not reach in the corpus" in summary["fake"]


def test_corpus_k_verdict_passes_when_the_win_is_where_the_code_is():
    case = microbench.BenchCase(
        pattern="fake", title="t", status="shipped", path=Path("fake.lua"),
        setup="", original="", rewrite="", sink="1",
        iters=[5, 100], corpus_k=[3, 10],
    )
    summary = microbench.g2_summary(_fake_rows(case, [(5, True), (100, False)]))
    assert summary["fake"].startswith("at corpus K 3-10: pass")


def test_g2_summary_reports_a_threshold():
    """A sweep that fails small and passes big must read as 'passes for K >= ...'."""
    case = microbench.BenchCase(
        pattern="fake", title="t", status="proposed", path=Path("fake.lua"),
        setup="", original="", rewrite="", sink="1", iters=[5, 100],
    )

    class FakeMode:
        def __init__(self, up):
            self.speedup = up

    def row(k, up):
        r = microbench.CaseResult(case=case, modes={m: FakeMode(up) for m in microbench.MODES}, k=k)
        return r

    summary = microbench.g2_summary([row(5, 1.0), row(100, 2.0)])
    assert summary["fake"] == "passes for K >= 100; fails at K in [5]"


# ---------------------------------------------------------------------------
# slow / opt-in
# ---------------------------------------------------------------------------

@pytest.mark.slow
@pytest.mark.bench
def test_every_green_pattern_has_a_bench_entry():
    """Adding a GREEN pattern without a benchmark is how ALAO ships a regression.

    GREEN means `--fix` rewrites it unasked on anyone's mod folder. Gate G2 says
    that rewrite must be >= 1.15x in both JIT modes. This test is the mechanical
    half of that gate: it does not check the number, it checks that a number can
    be produced at all.
    """
    covered = {c.pattern for c in microbench.load_cases(BENCH_DIR)}
    missing = []
    for name in green_pattern_names():
        if name in covered or name in EXEMPT_EXACT:
            continue
        if any(name.startswith(p) for p in EXEMPT_PREFIXES):
            continue
        rep = next((v for k, v in FAMILY_REPRESENTATIVE.items() if name.startswith(k)), None)
        if rep and rep in covered:
            continue
        missing.append(name)
    assert not missing, (
        "GREEN patterns with no bench/ pair: " + ", ".join(missing) +
        "\nAdd bench/<name>.lua (see tools/README.md) or, if the pattern removes "
        "code rather than rewriting a construct, add it to EXEMPT_EXACT with a reason."
    )


@pytest.mark.slow
@pytest.mark.bench
def test_full_bench_corpus_runs_end_to_end():
    """Every pair, short reps, small N. Catches a snippet that runs but throws."""
    errors = []
    for case in microbench.load_cases(BENCH_DIR):
        for r in microbench.run_case(case, reps=2, modes=microbench.MODES, n_override=5000):
            if r.error:
                errors.append(f"{r.label}: {r.error.splitlines()[0]}")
    assert not errors, "\n".join(errors)


@pytest.mark.slow
@pytest.mark.bench
def test_jit_off_is_really_off():
    """The protocol's load-bearing assumption, checked for real."""
    sc = microbench.self_check(reps=3)
    assert sc["ok"], (
        f"jit.off(f, true) did not slow the loop down ({sc['ratio_off_over_on']:.2f}x); "
        "the JIT-off column would be a duplicate of the JIT-on column"
    )
