"""Runner dry-runs, experiment plans, and snapshot safety.

Nothing here launches the game; the real install is only read.
"""

from __future__ import annotations

import csv
import json

import pytest

from aalo import ltx, metrics, runner, snapshot


def test_dry_run_writes_contract_artifacts(sandbox_cfg):
    run = runner.run_once(cfg=sandbox_cfg, idea_id="I-001", slug="unit", dry_run=True, duration_s=5.0)
    assert run.manifest_path.is_file()
    assert run.metrics_path.is_file()
    assert run.samples_path.is_file()

    manifest = json.loads(run.manifest_path.read_text(encoding="utf-8"))
    for key in (
        "run_id",
        "idea_id",
        "started",
        "finished",
        "status",
        "exe",
        "mo2_profile",
        "config_diff",
        "notes",
    ):
        assert key in manifest
    assert manifest["status"] == "done"
    assert manifest["idea_id"] == "I-001"
    assert manifest["finished"] is not None
    assert manifest["dry_run"] is True

    metrics = json.loads(run.metrics_path.read_text(encoding="utf-8"))
    assert metrics["fps_avg"] > 0
    assert metrics["crashed"] is False
    assert metrics["duration_s"] == pytest.approx(5.0, abs=0.5)


def test_run_id_matches_contract_pattern(sandbox_cfg):
    run = runner.run_once(cfg=sandbox_cfg, slug="my slug!", dry_run=True, duration_s=1.0)
    stamp, _, slug = run.run_id.partition("-")
    assert len(stamp) == 8 and stamp.isdigit()
    assert run.run_id.endswith("my-slug")


def test_samples_csv_header_and_rows(sandbox_cfg):
    run = runner.run_once(cfg=sandbox_cfg, slug="csv", dry_run=True, duration_s=3.0)
    with open(run.samples_path, newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    assert rows[0] == ["t_s", "frametime_ms", "fps"]
    assert len(rows) > 100
    assert float(rows[-1][0]) < 3.0


def test_dry_run_applies_then_restores_user_ltx(sandbox_cfg):
    target = sandbox_cfg.user_ltx
    before = target.read_bytes()
    run = runner.run_once(
        cfg=sandbox_cfg,
        slug="cfgdiff",
        dry_run=True,
        duration_s=1.0,
        user_ltx_changes={"r2_sun_quality": "st_opt_low"},
    )
    assert run.config_diff["r2_sun_quality"][1] == "st_opt_low"
    assert target.read_bytes() == before, "user.ltx must be restored after the run"
    assert "restored_from" in run.manifest


def test_keep_changes_leaves_the_edit_in_place(sandbox_cfg):
    run = runner.Run.create(cfg=sandbox_cfg, slug="keep")
    run.execute(dry_run=True, duration_s=1.0, user_ltx_changes={"r2_sun_quality": "st_opt_low"}, keep_changes=True)
    assert ltx.load_user(sandbox_cfg.user_ltx).get("r2_sun_quality") == "st_opt_low"


def test_snapshot_take_and_restore(sandbox_cfg):
    d = snapshot.take(label="unit", cfg=sandbox_cfg, include_profile=False)
    manifest = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["files"][0]["kind"] == "user_ltx"
    assert len(manifest["files"][0]["sha256"]) == 64

    doc = ltx.load_user(sandbox_cfg.user_ltx)
    doc.set("r2_sun_quality", "st_opt_high")
    doc.save(sandbox_cfg.user_ltx)
    assert ltx.load_user(sandbox_cfg.user_ltx).get("r2_sun_quality") == "st_opt_high"

    snapshot.restore(d, cfg=sandbox_cfg)
    assert ltx.load_user(sandbox_cfg.user_ltx).get("r2_sun_quality") != "st_opt_high"


def test_snapshot_diff_between_snapshots(sandbox_cfg):
    a = snapshot.take(label="a", cfg=sandbox_cfg, include_profile=False)
    doc = ltx.load_user(sandbox_cfg.user_ltx)
    doc.set("r2_sun_quality", "st_opt_low")
    doc.save(sandbox_cfg.user_ltx)
    b = snapshot.take(label="b", cfg=sandbox_cfg, include_profile=False)
    diff = snapshot.diff_user_ltx(a, b)
    assert diff["r2_sun_quality"][1] == "st_opt_low"


def test_list_runs_newest_first(sandbox_cfg):
    for i in range(2):
        runner.run_once(cfg=sandbox_cfg, slug=f"list{i}", dry_run=True, duration_s=1.0)
    rows = runner.list_runs(sandbox_cfg)
    assert len(rows) == 2
    assert rows[0]["run_id"] > rows[1]["run_id"]
    assert "metrics" in rows[0]


def test_experiment_loads_and_alternates_arms(real_cfg):
    exp = runner.load_experiment("example-sun-quality", real_cfg)
    assert exp.idea_id == "I-001"
    assert exp.arms["variant"]["user_ltx"]["r2_sun_quality"] == "st_opt_low"
    assert exp.order() == ["baseline", "variant"] * exp.repeats


def test_experiment_missing_file_raises(real_cfg):
    with pytest.raises(FileNotFoundError):
        runner.load_experiment("no-such-experiment", real_cfg)


def test_experiment_dry_run_produces_one_run_per_arm(sandbox_cfg, real_cfg):
    exp = runner.load_experiment("example-sun-quality", real_cfg)
    exp.repeats = 1
    exp.duration_s = 2.0
    runs = runner.run_experiment(exp, cfg=sandbox_cfg, dry_run=True)
    assert [r.manifest["arm"] for r in runs] == ["baseline", "variant"]
    assert all(r.manifest["status"] == "done" for r in runs)
    assert runs[0].metrics["fps_avg"] != runs[1].metrics["fps_avg"], "arms should not be identical noise"
    variant = runs[1].manifest["config_diff"]
    assert variant["r2_sun_quality"] == ["st_opt_medium", "st_opt_low"]


def is_baseline(manifest) -> bool:
    """The dashboard's classification rule, mirrored so the runner stays honest."""
    notes = (manifest.get("notes") or "").lower()
    if "variant" in notes:
        return False
    return manifest.get("idea_id") is None or notes.startswith("baseline") or "baseline=true" in notes


def test_experiment_notes_match_the_dashboard_convention(sandbox_cfg, real_cfg):
    exp = runner.load_experiment("example-sun-quality", real_cfg)
    exp.repeats = 1
    exp.duration_s = 2.0
    runs = runner.run_experiment(exp, cfg=sandbox_cfg, dry_run=True)
    base, var = runs[0].manifest, runs[1].manifest
    assert base["notes"].startswith("baseline")
    assert "variant" not in base["notes"].lower()
    assert var["notes"].startswith("variant")
    assert is_baseline(base) is True
    assert is_baseline(var) is False


def test_arm_notes_scrubs_variant_from_a_baseline():
    notes = runner._arm_notes("baseline", "same as the variant but stock")
    assert notes.startswith("baseline")
    assert "variant" not in notes.lower()
    assert runner._arm_notes("variant", "cheaper shadows") == "variant cheaper shadows"


def test_mod_toggle_run_cleans_up_its_profile_copy(sandbox_cfg, real_cfg):
    """A run must not leave aalo-* profile directories in the install."""
    from aalo import mo2

    before = set(mo2.MO2(sandbox_cfg).profiles())
    run = runner.run_once(
        cfg=sandbox_cfg,
        slug="profilecopy",
        dry_run=True,
        duration_s=1.0,
        mod_toggles={"Turn this on if you stutter": True},
    )
    assert run.manifest["mo2_profile"].startswith("aalo-")
    assert run.manifest["profile_copy_removed"] == run.manifest["mo2_profile"]
    assert set(mo2.MO2(sandbox_cfg).profiles()) == before
    assert run.config_diff["mod/Turn this on if you stutter"] == ["disabled", "enabled"]


def test_stutter_mod_experiment_is_a_mod_ab(real_cfg):
    exp = runner.load_experiment("alife-stutter-mod", real_cfg)
    assert exp.warmup_s == 30
    assert exp.arms["baseline"]["mods"]["Turn this on if you stutter"] is False
    assert exp.arms["variant"]["mods"]["Turn this on if you stutter"] is True
    # Both mod names must really exist in the live profile.
    from aalo import mo2

    ml = mo2.MO2(real_cfg).modlist()
    for name in exp.arms["variant"]["mods"]:
        assert ml.find(name) is not None, f"{name} is not in modlist.txt"


def test_dry_run_records_warmup_in_metrics_extra(sandbox_cfg):
    run = runner.run_once(cfg=sandbox_cfg, slug="warm", dry_run=True, duration_s=1.0, warmup_s=12.0)
    extra = run.metrics["extra"]
    assert extra["warmup_s"] == 12.0
    assert extra["warmup_waited"] is False  # a dry run never actually waits


def test_dry_run_trims_the_warmup_from_the_samples(sandbox_cfg):
    """The measured window is the requested duration, warm-up excluded."""
    warm = runner.run_once(cfg=sandbox_cfg, slug="trim", dry_run=True, duration_s=10.0, warmup_s=5.0)
    samples = metrics.read_samples_csv(warm.samples_path)

    # The clock is rebased, so the window is the duration and not duration+warmup.
    assert samples[0].t_s == 0.0
    assert warm.metrics["duration_s"] == pytest.approx(10.0, abs=0.5)
    assert warm.metrics["extra"]["warmup_trimmed_samples"] > 0

    # The trimmed frames are genuinely gone, not just re-labelled: after trimming, both runs
    # measure the same 10 s window, so their sample counts agree (within synthetic frame jitter)
    # even though the warm run synthesised warmup_s + duration_s worth of frames.
    untrimmed = runner.run_once(cfg=sandbox_cfg, slug="trim", dry_run=True, duration_s=10.0, warmup_s=0.0)
    kept = warm.metrics["extra"]["samples"]
    trimmed = warm.metrics["extra"]["warmup_trimmed_samples"]
    assert kept == pytest.approx(untrimmed.metrics["extra"]["samples"], rel=0.10)
    assert trimmed == pytest.approx(kept * 5.0 / 10.0, rel=0.25)  # 5 s of warm-up vs 10 s kept
    assert untrimmed.metrics["extra"]["warmup_trimmed_samples"] == 0


def test_trim_warmup_helper():
    S = metrics.Sample
    samples = [S(t_s=float(i), frametime_ms=16.0, fps=62.5) for i in range(10)]
    kept = runner._trim_warmup(samples, 4.0)
    assert len(kept) == 6
    assert kept[0].t_s == 0.0
    assert kept[-1].t_s == 5.0
    assert runner._trim_warmup(samples, 0.0) == samples
    # A warm-up longer than the run keeps one sample rather than nothing.
    assert len(runner._trim_warmup(samples, 99.0)) == 1


def test_warmup_defaults_from_config(sandbox_cfg):
    run = runner.run_once(cfg=sandbox_cfg, slug="warmdefault", dry_run=True, duration_s=1.0)
    assert run.metrics["extra"]["warmup_s"] == sandbox_cfg.warmup_s


def test_warmup_wait_is_skipped_when_zero(sandbox_cfg):
    import time

    run = runner.Run.create(cfg=sandbox_cfg, slug="warmzero")
    t0 = time.perf_counter()
    info = run._wait_for_warmup(0.0, mtime_before=0.0, deadline_s=5.0)
    assert time.perf_counter() - t0 < 1.0
    assert info["warmup_s"] == 0.0
    assert info["warmup_waited"] is False
    assert info["level_marker_seen"] is False
    assert info["warmup_trimmed_samples"] == 0


def test_warmup_detects_the_level_marker(sandbox_cfg):
    import time

    log = sandbox_cfg.logs_dir / "xray_test.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("* [x-ray]: Loading level [l02_garbage]\n", encoding="utf-8")
    run = runner.Run.create(cfg=sandbox_cfg, slug="warmmarker")
    t0 = time.perf_counter()
    info = run._wait_for_warmup(0.2, mtime_before=0.0, deadline_s=10.0)
    assert info["level_marker_seen"] is True
    assert info["warmup_waited"] is True
    assert time.perf_counter() - t0 < 5.0, "should stop waiting as soon as the level loads"


def test_user_ltx_resolves_to_the_profile_copy_when_present(tmp_path):
    """MO2 LocalSettings=true makes profiles/<profile>/user.ltx authoritative.

    Built entirely in tmp_path: nothing is created inside the game install.
    """
    from aalo import config as _config

    game_root = tmp_path / "game"
    (game_root / "Anomaly" / "appdata").mkdir(parents=True)
    appdata_ltx = game_root / "Anomaly" / "appdata" / "user.ltx"
    appdata_ltx.write_text("r2_sun_quality st_opt_medium\n", encoding="utf-8")
    toml = tmp_path / "aalo.toml"
    toml.write_text(
        f"[paths]\ngame_root = '{game_root}'\nlab = '{tmp_path}'\n\n[mo2]\nprofile = 'G.A.M.M.A'\n",
        encoding="utf-8",
    )
    cfg = _config.load(toml)

    # Before the first launch there is no profile copy, so appdata wins.
    assert cfg.user_ltx_is_profile_local() is False
    assert cfg.effective_user_ltx() == appdata_ltx

    profile_ltx = cfg.profile_dir() / "user.ltx"
    profile_ltx.parent.mkdir(parents=True)
    profile_ltx.write_text("r2_sun_quality st_opt_high\n", encoding="utf-8")
    assert cfg.user_ltx_is_profile_local() is True
    assert cfg.effective_user_ltx() == profile_ltx

    # An aalo- copy carries its own, and resolves independently.
    copy_ltx = cfg.profile_dir("aalo-x") / "user.ltx"
    copy_ltx.parent.mkdir(parents=True)
    copy_ltx.write_text("r2_sun_quality st_opt_low\n", encoding="utf-8")
    assert cfg.effective_user_ltx("aalo-x") == copy_ltx


def test_runner_edits_the_profile_local_user_ltx(tmp_path, real_cfg):
    """A run must write the profile copy, not the stale appdata file."""
    from aalo import config as _config

    game_root = tmp_path / "game"
    (game_root / "Anomaly" / "appdata").mkdir(parents=True)
    appdata_ltx = game_root / "Anomaly" / "appdata" / "user.ltx"
    appdata_ltx.write_text("r2_sun_quality st_opt_medium\n", encoding="utf-8")
    toml = tmp_path / "aalo.toml"
    toml.write_text(
        f"[paths]\ngame_root = '{game_root}'\nlab = '{tmp_path}'\n\n[mo2]\nprofile = 'G.A.M.M.A'\n",
        encoding="utf-8",
    )
    cfg = _config.load(toml)
    profile_ltx = cfg.profile_dir() / "user.ltx"
    profile_ltx.parent.mkdir(parents=True)
    profile_ltx.write_text("r2_sun_quality st_opt_medium\n", encoding="utf-8")

    run = runner.Run.create(cfg=cfg, slug="local")
    run.execute(
        dry_run=True,
        duration_s=1.0,
        user_ltx_changes={"r2_sun_quality": "st_opt_low"},
        keep_changes=True,
    )
    assert ltx.load_user(profile_ltx).get("r2_sun_quality") == "st_opt_low"
    assert ltx.load_user(appdata_ltx).get("r2_sun_quality") == "st_opt_medium"


def test_mod_toggles_refuse_to_touch_the_live_profile(real_cfg):
    from aalo import mo2

    m = mo2.MO2(real_cfg)
    with pytest.raises(ValueError):
        m.apply_mod_toggles(real_cfg.profile, {"anything": False})
    with pytest.raises(ValueError):
        m.set_mod_enabled(real_cfg.profile, "anything", False)
    with pytest.raises(ValueError):
        m.delete_profile(real_cfg.profile)
