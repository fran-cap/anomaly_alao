"""fps_runner's profiler-arm support (I-048).

The profiler overlay is the instrument, not the treatment: it is installed once,
at the top of the load order, and enabled in BOTH arms.  These tests pin the two
places that can go wrong - the experiment TOML (a mod that both arms enable must
not appear in either arm's disable list) and the post-run fold of the dumps out
of each run's xray.log.  Nothing here launches anything or writes into the game.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

LAB = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LAB / "framework"))
sys.path.insert(0, str(LAB / "coord"))

import fps_runner  # noqa: E402

from test_profiler import HAND_DUMP  # noqa: E402


class _Cfg:
    def __init__(self, root: Path):
        self.experiments_dir = root / "experiments"
        self.runs_dir = root / "runs"
        self.runs_dir.mkdir(parents=True, exist_ok=True)


def test_experiment_toml_enables_the_profiler_in_both_arms(tmp_path):
    cfg = _Cfg(tmp_path)
    arm_mods = {
        "baseline": ["aalo-rewrite-x-a", "aalo-rewrite-x-p"],
        "variant": ["aalo-rewrite-x-b", "aalo-rewrite-x-p"],
    }
    p = fps_runner.write_experiment(cfg, "x", {"label": "aa-profiler"}, "aalo-src-x", arm_mods)
    text = p.read_text(encoding="utf-8")
    base, var = text.split("[variant]")
    assert '"aalo-rewrite-x-p" = true' in base
    assert '"aalo-rewrite-x-p" = true' in var
    # the profiler must never be written false anywhere, or the arm that gets
    # the false line would be measured without the instrument
    assert '"aalo-rewrite-x-p" = false' not in text
    assert '"aalo-rewrite-x-b" = false' in base
    assert '"aalo-rewrite-x-a" = false' in var


def test_profiler_slot_is_declared_for_both_arms():
    slots = {k: arms for k, arms, _s, _p in _slots()}
    assert slots["profiler_overlay"] == ("baseline", "variant")
    assert slots["variant_overlay"] == ("variant",)


def _slots():
    """The slot table lives inside process(); read it out of the source once so
    the test breaks loudly if someone edits it into a different shape."""
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(fps_runner.process).lstrip())
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "slots":
            return ast.literal_eval(node.value)
    raise AssertionError("fps_runner.process no longer has a `slots` table")


def _make_run(cfg, name, arm, text):
    d = cfg.runs_dir / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.json").write_text(json.dumps({"arm": arm}), encoding="utf-8")
    (d / "xray.log").write_text(text, encoding="utf-8")
    return d


def test_summarize_profiler_folds_both_arms(tmp_path):
    cfg = _Cfg(tmp_path)
    per_arm = {"baseline": {"runs": []}, "variant": {"runs": []}}
    for i, scale in enumerate((1.0, 1.01, 0.995)):
        _make_run(cfg, f"b{i}", "baseline",
                  HAND_DUMP.replace("units_per_ms=2500.000000", f"units_per_ms={2500 / scale:.6f}"))
        per_arm["baseline"]["runs"].append(f"b{i}")
    for i, scale in enumerate((1.10, 1.11, 1.09)):
        _make_run(cfg, f"v{i}", "variant",
                  HAND_DUMP.replace("units_per_ms=2500.000000", f"units_per_ms={2500 / scale:.6f}"))
        per_arm["variant"]["runs"].append(f"v{i}")

    rep = fps_runner.summarize_profiler(cfg, per_arm)
    assert rep is not None
    assert rep["arms"]["baseline"]["n_runs"] == 3
    # both numbers are kept: all rounds, and with the session warm-up round out
    assert rep["arms"]["baseline"]["n_runs_warm"] == 2
    assert rep["arms"]["baseline"]["script_ms_per_frame_warm"]["n"] == 2
    assert rep["arms"]["baseline"]["script_ms_per_frame"]["cv_pct"] < 1.0
    # variant arm is 10% slower by construction
    assert rep["delta_pct"] == pytest.approx(10.0, abs=0.5)
    assert rep["arms"]["variant"]["ranking"][0]["name"] == "actor_on_update"
    # every run keeps its own parsed copy next to the frametimes
    saved = json.loads((cfg.runs_dir / "b0" / "profiler.json").read_text(encoding="utf-8"))
    assert saved["summary"]["usable"] is True
    assert saved["ranking"][0]["name"] == "actor_on_update"


def test_summarize_profiler_is_none_without_dumps(tmp_path):
    cfg = _Cfg(tmp_path)
    _make_run(cfg, "b0", "baseline", "* [x-ray]: nothing to see here\n")
    assert fps_runner.summarize_profiler(cfg, {"baseline": {"runs": ["b0"]}}) is None


def test_summarize_profiler_survives_a_failed_install(tmp_path):
    cfg = _Cfg(tmp_path)
    _make_run(cfg, "b0", "baseline", "ALAOPROF|1|err|install failed: nil value\n")
    # an err line is not a window, so there is still nothing to average
    assert fps_runner.summarize_profiler(cfg, {"baseline": {"runs": ["b0"]}}) is None
