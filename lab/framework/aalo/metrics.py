"""Frame/resource sampling and the metric math behind ``metrics.json``.

Two backends:

* **presentmon** - Intel PresentMon CLI, when a ``PresentMon*.exe`` is on PATH
  or in a known install directory.  Gives true per-frame present times.
* **psutil** - fallback that polls CPU% and RSS of the game process at
  ``sample_hz``.  There is no frame data in this mode, so every sample carries
  ``fps = None`` and the fps metrics come out as ``None``.

Both produce the same :class:`Sample` rows, written to ``samples.csv`` as
``t_s,frametime_ms,fps`` per the contract.
"""

from __future__ import annotations

import csv
import json
import math
import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, asdict
from pathlib import Path

__all__ = [
    "Sample",
    "FrameSampler",
    "find_presentmon",
    "compute_metrics",
    "write_samples_csv",
    "read_samples_csv",
    "write_metrics_json",
    "percentile",
    "parse_presentmon_csv",
    "synthetic_samples",
]

SAMPLES_HEADER = ["t_s", "frametime_ms", "fps"]

# PresentMon column names differ across major versions; try each in order.
_FRAMETIME_COLUMNS = ("msbetweenpresents", "frametime", "mspresenttime", "msinpresentapi")
_TIME_COLUMNS = ("timeinseconds", "cpustarttime", "time")
# PresentMon 2.x writes milliseconds instead (TimeInMs, CPUStartTimeInMs)
_TIME_MS_COLUMNS = ("timeinms", "cpustarttimeinms")


@dataclass
class Sample:
    """One sample: a frame (presentmon) or a 1 Hz poll (psutil)."""

    t_s: float
    frametime_ms: float | None = None
    fps: float | None = None
    cpu_pct: float | None = None
    rss_mb: float | None = None

    def row(self) -> list:
        def fmt(v, nd):
            return "" if v is None else f"{v:.{nd}f}"

        return [f"{self.t_s:.4f}", fmt(self.frametime_ms, 3), fmt(self.fps, 2)]


# -- PresentMon discovery ---------------------------------------------------

_PRESENTMON_DIRS = [
    # the Intel MSI ships the CLI here; PresentMonApplication\PresentMon.exe next
    # door is the GUI and must never be picked (it ignores the CLI flags)
    r"C:\Program Files\Intel\PresentMon\PresentMonConsoleApplication",
    r"C:\Program Files\Intel\PresentMon",
    r"C:\Program Files\PresentMon",
    r"C:\Program Files (x86)\PresentMon",
]


def find_presentmon(extra_dirs=None) -> Path | None:
    """Locate a PresentMon CLI executable, or None."""
    for name in ("PresentMon.exe", "presentmon.exe", "PresentMon-2.exe", "PresentMon-1.10.0-x64.exe"):
        hit = shutil.which(name)
        if hit:
            return Path(hit)
    # the lab root and lab/tools are checked too, so a loose PresentMon-x.y.z-x64.exe
    # dropped next to the data folder works without touching PATH or PRESENTMON
    lab_root = Path(__file__).resolve().parents[2]
    dirs = [Path(d) for d in (list(_PRESENTMON_DIRS) + list(extra_dirs or []))]
    dirs += [lab_root, lab_root / "tools"]
    env = os.environ.get("PRESENTMON")
    if env and Path(env).is_file():
        return Path(env)
    for d in dirs:
        if not d.is_dir():
            continue
        for p in sorted(d.glob("PresentMon*.exe")):
            return p
    # Last resort: anything named PresentMon* on PATH directories.
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not entry:
            continue
        try:
            for p in sorted(Path(entry).glob("PresentMon*.exe")):
                return p
        except OSError:
            continue
    return None


def parse_presentmon_csv(path) -> list[Sample]:
    """Read a PresentMon output CSV into samples, tolerating version drift."""
    rows: list[Sample] = []
    p = Path(path)
    if not p.is_file():
        return rows
    with open(p, newline="", encoding="utf-8", errors="replace") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames:
            return rows
        lower = {name.lower().strip(): name for name in reader.fieldnames}
        ft_col = next((lower[c] for c in _FRAMETIME_COLUMNS if c in lower), None)
        t_col = next((lower[c] for c in _TIME_COLUMNS if c in lower), None)
        t_scale = 1.0
        if t_col is None:
            t_col = next((lower[c] for c in _TIME_MS_COLUMNS if c in lower), None)
            t_scale = 0.001
        t0 = None
        for i, row in enumerate(reader):
            try:
                ft = float(row[ft_col]) if ft_col and row.get(ft_col) not in (None, "", "NA") else None
            except ValueError:
                ft = None
            t = None
            if t_col:
                try:
                    t = float(row[t_col]) * t_scale
                except (TypeError, ValueError):
                    t = None
            if t is None:
                t = (rows[-1].t_s + (ft or 0) / 1000.0) if rows else 0.0
            elif t0 is None:
                t0 = t
                t = 0.0
            else:
                t = t - t0
            fps = (1000.0 / ft) if ft and ft > 0 else None
            rows.append(Sample(t_s=t, frametime_ms=ft, fps=fps))
    return rows


# -- sampling ---------------------------------------------------------------


class FrameSampler:
    """Collect samples for the lifetime of a run.

    Usage::

        s = FrameSampler(process_name="AnomalyDX11AVX.exe", out_dir=run_dir)
        s.start(); ...; s.stop()
        s.samples  ->  list[Sample]
    """

    def __init__(self, process_name: str, out_dir=None, sample_hz: float = 1.0, prefer: str | None = None):
        self.process_name = process_name
        self.out_dir = Path(out_dir) if out_dir else None
        self.sample_hz = max(0.1, float(sample_hz))
        self.samples: list[Sample] = []
        self.backend = "none"
        self._proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._t0: float | None = None
        self._csv_path: Path | None = None
        self._presentmon = None if prefer == "psutil" else find_presentmon()
        self._prefer = prefer

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> str:
        """Begin sampling.  Returns the backend actually used."""
        self._t0 = time.perf_counter()
        self._stop.clear()
        if self._presentmon and self._prefer != "psutil":
            if self._start_presentmon():
                self.backend = "presentmon"
                return self.backend
        if self._prefer != "presentmon":
            self._thread = threading.Thread(target=self._poll_loop, name="aalo-sampler", daemon=True)
            self._thread.start()
            self.backend = "psutil"
            return self.backend
        self.backend = "none"
        return self.backend

    def stop(self) -> list[Sample]:
        """Stop sampling and materialise :attr:`samples`."""
        self._stop.set()
        if self._proc is not None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=15)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            self._proc = None
            if self._csv_path:
                self.samples = parse_presentmon_csv(self._csv_path)
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        return self.samples

    # -- backends ----------------------------------------------------------
    def _start_presentmon(self) -> bool:
        out = (self.out_dir or Path.cwd()) / "presentmon.csv"
        out.parent.mkdir(parents=True, exist_ok=True)
        self._csv_path = out
        cmd = [
            str(self._presentmon),
            "--process_name",
            self.process_name,
            "--output_file",
            str(out),
            "--stop_existing_session",
            "--terminate_on_proc_exit",
            "--no_console_stats",  # 2.x name; 1.x called it --no_top
        ]
        try:
            self._proc = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL
            )
            return True
        except OSError:
            self._proc = None
            return False

    def _poll_loop(self) -> None:
        try:
            import psutil  # type: ignore
        except ImportError:
            return
        interval = 1.0 / self.sample_hz
        target = None
        while not self._stop.is_set():
            if target is None or not target.is_running():
                target = _find_process(psutil, self.process_name)
                if target is not None:
                    try:
                        target.cpu_percent(None)  # prime the counter
                    except Exception:
                        target = None
            t = time.perf_counter() - (self._t0 or time.perf_counter())
            cpu = rss = None
            if target is not None:
                try:
                    cpu = target.cpu_percent(None)
                    rss = target.memory_info().rss / (1024 * 1024)
                except Exception:
                    target = None
            self.samples.append(Sample(t_s=t, frametime_ms=None, fps=None, cpu_pct=cpu, rss_mb=rss))
            self._stop.wait(interval)


def _find_process(psutil_mod, name: str):
    n = name.lower()
    for p in psutil_mod.process_iter(["name"]):
        try:
            if (p.info.get("name") or "").lower() == n:
                return p
        except Exception:
            continue
    return None


# -- metric math ------------------------------------------------------------


def percentile(values, pct: float) -> float | None:
    """Linear-interpolated percentile; *pct* in 0..100."""
    data = sorted(v for v in values if v is not None and not math.isnan(v))
    if not data:
        return None
    if len(data) == 1:
        return float(data[0])
    k = (len(data) - 1) * (pct / 100.0)
    lo = math.floor(k)
    hi = math.ceil(k)
    if lo == hi:
        return float(data[int(k)])
    return float(data[lo] + (data[hi] - data[lo]) * (k - lo))


def _low_percent_mean(fps_values, pct: float = 1.0) -> float | None:
    """Mean of the slowest *pct* percent of frames - the usual "1% low"."""
    data = sorted(v for v in fps_values if v is not None and v > 0)
    if not data:
        return None
    n = max(1, int(len(data) * pct / 100.0))
    worst = data[:n]
    return sum(worst) / len(worst)


def compute_metrics(samples, duration_s: float | None = None, crashed: bool = False, load_time_s=None, extra=None) -> dict:
    """Build the contract's ``metrics.json`` dict from samples."""
    samples = list(samples)
    fps_values = [s.fps for s in samples if s.fps]
    frametimes = [s.frametime_ms for s in samples if s.frametime_ms]
    if not frametimes and fps_values:
        frametimes = [1000.0 / f for f in fps_values if f > 0]

    if duration_s is None:
        duration_s = (samples[-1].t_s - samples[0].t_s) if len(samples) > 1 else 0.0

    fps_avg = None
    if frametimes:
        mean_ft = sum(frametimes) / len(frametimes)
        fps_avg = 1000.0 / mean_ft if mean_ft > 0 else None
    elif fps_values:
        fps_avg = sum(fps_values) / len(fps_values)

    ram_peak = max((s.rss_mb for s in samples if s.rss_mb is not None), default=None)
    cpu_values = [s.cpu_pct for s in samples if s.cpu_pct is not None]

    ex = {
        "samples": len(samples),
        "frames": len(frametimes),
        "cpu_pct_avg": round(sum(cpu_values) / len(cpu_values), 2) if cpu_values else None,
        "cpu_pct_peak": round(max(cpu_values), 2) if cpu_values else None,
        "frametime_p95_ms": _round(percentile(frametimes, 95)),
        "frametime_median_ms": _round(percentile(frametimes, 50)),
        "fps_min": _round(min(fps_values)) if fps_values else None,
        "fps_max": _round(max(fps_values)) if fps_values else None,
    }
    if extra:
        ex.update(extra)

    return {
        "fps_avg": _round(fps_avg),
        "fps_1pct_low": _round(_low_percent_mean(fps_values, 1.0)),
        "frametime_p99_ms": _round(percentile(frametimes, 99)),
        "load_time_s": _round(load_time_s),
        "ram_peak_mb": _round(ram_peak),
        "vram_peak_mb": None,
        "crashed": bool(crashed),
        "duration_s": _round(duration_s) or 0.0,
        "extra": ex,
    }


def _round(v, nd: int = 2):
    return None if v is None else round(float(v), nd)


# -- io ---------------------------------------------------------------------


def write_samples_csv(path, samples) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(SAMPLES_HEADER)
        for s in samples:
            w.writerow(s.row())
    return p


def read_samples_csv(path) -> list[Sample]:
    out: list[Sample] = []
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            def num(key):
                v = (row.get(key) or "").strip()
                return float(v) if v else None

            out.append(Sample(t_s=num("t_s") or 0.0, frametime_ms=num("frametime_ms"), fps=num("fps")))
    return out


def write_metrics_json(path, metrics: dict) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    return p


def synthetic_samples(duration_s: float = 30.0, fps: float = 60.0, jitter: float = 0.15, seed: int = 0) -> list[Sample]:
    """Deterministic fake frame data for ``--dry-run`` and demo seeding."""
    import random

    rng = random.Random(seed)
    base_ft = 1000.0 / fps
    t = 0.0
    out: list[Sample] = []
    while t < duration_s:
        ft = base_ft * (1.0 + rng.uniform(-jitter, jitter))
        if rng.random() < 0.01:  # occasional stutter, so 1% lows mean something
            ft *= rng.uniform(2.0, 4.0)
        out.append(Sample(t_s=round(t, 4), frametime_ms=round(ft, 3), fps=round(1000.0 / ft, 2)))
        t += ft / 1000.0
    return out


def sample_from_dict(d: dict) -> Sample:
    return Sample(**{k: v for k, v in d.items() if k in Sample.__dataclass_fields__})


def sample_to_dict(s: Sample) -> dict:
    return asdict(s)
