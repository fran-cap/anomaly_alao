"""Metric math and samples.csv io."""

from __future__ import annotations

import csv

import pytest

from aalo import metrics
from aalo.metrics import Sample


def steady(fps: float, n: int = 100) -> list[Sample]:
    ft = 1000.0 / fps
    return [Sample(t_s=i * ft / 1000.0, frametime_ms=ft, fps=fps) for i in range(n)]


def test_percentile_interpolates():
    data = [1, 2, 3, 4]
    assert metrics.percentile(data, 0) == 1.0
    assert metrics.percentile(data, 100) == 4.0
    assert metrics.percentile(data, 50) == 2.5
    assert metrics.percentile([], 50) is None
    assert metrics.percentile([7], 99) == 7.0


def test_steady_frames_give_exact_fps():
    m = metrics.compute_metrics(steady(60.0))
    assert m["fps_avg"] == 60.0
    assert m["fps_1pct_low"] == 60.0
    assert m["frametime_p99_ms"] == pytest.approx(16.67, abs=0.01)
    assert m["crashed"] is False


def test_fps_avg_is_harmonic_not_arithmetic():
    """Averaging frametimes, not fps: 100 frames at 100fps + 100 at 20fps."""
    samples = steady(100.0, 100) + steady(20.0, 100)
    m = metrics.compute_metrics(samples)
    # mean frametime = (10 + 50) / 2 = 30 ms -> 33.3 fps, not (100+20)/2 = 60
    assert m["fps_avg"] == pytest.approx(33.33, abs=0.05)


def test_one_percent_low_uses_worst_frames():
    samples = steady(60.0, 99) + [Sample(t_s=99.0, frametime_ms=200.0, fps=5.0)]
    m = metrics.compute_metrics(samples)
    assert m["fps_1pct_low"] == 5.0
    assert m["fps_avg"] < 60.0
    assert m["frametime_p99_ms"] > 16.7


def test_psutil_style_samples_have_no_fps():
    samples = [Sample(t_s=float(i), cpu_pct=50.0 + i, rss_mb=1000.0 + i) for i in range(10)]
    m = metrics.compute_metrics(samples, duration_s=9.0)
    assert m["fps_avg"] is None
    assert m["fps_1pct_low"] is None
    assert m["frametime_p99_ms"] is None
    assert m["ram_peak_mb"] == 1009.0
    assert m["extra"]["cpu_pct_peak"] == 59.0
    assert m["duration_s"] == 9.0


def test_metrics_has_every_contract_key():
    m = metrics.compute_metrics(steady(60.0), crashed=True, load_time_s=12.0)
    for key in (
        "fps_avg",
        "fps_1pct_low",
        "frametime_p99_ms",
        "load_time_s",
        "ram_peak_mb",
        "vram_peak_mb",
        "crashed",
        "duration_s",
        "extra",
    ):
        assert key in m
    assert m["crashed"] is True
    assert m["load_time_s"] == 12.0


def test_empty_samples_do_not_crash():
    m = metrics.compute_metrics([])
    assert m["fps_avg"] is None
    assert m["duration_s"] == 0.0
    assert m["extra"]["samples"] == 0


def test_samples_csv_roundtrip(tmp_path):
    samples = steady(60.0, 5)
    p = metrics.write_samples_csv(tmp_path / "samples.csv", samples)
    with open(p, newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    assert rows[0] == metrics.SAMPLES_HEADER
    assert len(rows) == 6
    back = metrics.read_samples_csv(p)
    assert len(back) == 5
    assert back[0].fps == pytest.approx(60.0)


def test_csv_writes_blanks_for_missing_fps(tmp_path):
    p = metrics.write_samples_csv(tmp_path / "s.csv", [Sample(t_s=0.0, cpu_pct=5.0)])
    assert p.read_text(encoding="utf-8").splitlines()[1] == "0.0000,,"


def test_synthetic_samples_are_deterministic():
    a = metrics.synthetic_samples(5.0, seed=7)
    b = metrics.synthetic_samples(5.0, seed=7)
    c = metrics.synthetic_samples(5.0, seed=8)
    assert [s.frametime_ms for s in a] == [s.frametime_ms for s in b]
    assert [s.frametime_ms for s in a] != [s.frametime_ms for s in c]
    assert a[-1].t_s < 5.0
    m = metrics.compute_metrics(a)
    assert 40 < m["fps_avg"] < 70
    assert m["fps_1pct_low"] < m["fps_avg"]


def test_parse_presentmon_csv(tmp_path):
    p = tmp_path / "pm.csv"
    p.write_text(
        "Application,TimeInSeconds,msBetweenPresents\n"
        "Anomaly.exe,100.0,16.7\n"
        "Anomaly.exe,100.0167,16.6\n"
        "Anomaly.exe,100.0333,33.0\n",
        encoding="utf-8",
    )
    samples = metrics.parse_presentmon_csv(p)
    assert len(samples) == 3
    assert samples[0].t_s == 0.0
    assert samples[1].t_s == pytest.approx(0.0167)
    assert samples[0].fps == pytest.approx(1000 / 16.7)
    m = metrics.compute_metrics(samples)
    assert m["extra"]["frames"] == 3


def test_parse_presentmon_csv_missing_file(tmp_path):
    assert metrics.parse_presentmon_csv(tmp_path / "nope.csv") == []


def test_sampler_falls_back_to_psutil_for_absent_process(tmp_path):
    s = metrics.FrameSampler("definitely-not-running.exe", out_dir=tmp_path, sample_hz=8.0, prefer="psutil")
    assert s.start() == "psutil"
    import time

    time.sleep(0.4)
    samples = s.stop()
    assert samples, "sampler should record rows even with no target process"
    assert all(x.fps is None for x in samples)
