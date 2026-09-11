"""A round pinned to a refresh rate must be flagged, real drift must not."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "framework"))

from aalo import metrics as m  # noqa: E402


def _samples(fps_per_window, window_s=30.0, hz=200):
    out = []
    t = 0.0
    for fps in fps_per_window:
        ft = 1000.0 / fps
        n = int(window_s * fps)
        for _ in range(n):
            out.append(m.Sample(t_s=t, frametime_ms=ft, fps=fps))
            t += ft / 1000.0
    return out


def test_flat_144_is_capped():
    cap = m.detect_frame_cap(_samples([144.0] * 10))
    assert cap["capped"] is True and cap["cap_fps"] == 144


def test_flat_60_is_capped():
    assert m.detect_frame_cap(_samples([60.2, 59.9, 60.0, 60.1, 60.0]))["capped"] is True


def test_real_drift_is_not_capped():
    cap = m.detect_frame_cap(_samples([215, 211, 211, 212, 212, 212, 212, 213, 213, 212]))
    assert cap["capped"] is False


def test_flat_but_not_a_known_cap_is_not_capped():
    assert m.detect_frame_cap(_samples([207.0] * 10))["capped"] is False


def test_too_short_to_judge():
    assert m.detect_frame_cap(_samples([144.0, 144.0]))["capped"] is False


def test_compute_metrics_carries_the_flag():
    met = m.compute_metrics(_samples([144.0] * 4))
    assert met["extra"]["capped"] is True
    assert met["extra"]["cap_fps"] == 144
    assert len(met["extra"]["window_fps"]) == 4
