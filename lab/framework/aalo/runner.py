"""Run harness: launch a measured session, or A/B a whole experiment.

A run owns ``data/runs/<run_id>/`` and writes exactly the four artefacts the
contract names: ``manifest.json``, ``metrics.json``, ``samples.csv`` and
``xray.log``.

Elevation caveat
----------------
The GOG build of G.A.M.M.A. launches Mod Organizer 2 with ``RUNASADMIN``, and an
elevated process cannot be started from a non-elevated one without a UAC prompt.
So the harness must itself run elevated (an elevated terminal, or a Scheduled
Task with "run with highest privileges"), otherwise the launch either raises a
prompt or fails outright.  ``--dry-run`` needs no elevation at all.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import time
import tomllib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import config as _config
from . import ltx as _ltx
from . import metrics as _metrics
from . import mo2 as _mo2
from . import snapshot as _snapshot
from . import xraylog as _xraylog

__all__ = ["Run", "Experiment", "run_once", "run_experiment", "load_experiment", "list_runs"]

DRY_RUN_DURATION_S = 30.0


def _iso(dt: datetime | None = None) -> str:
    return (dt or datetime.now(timezone.utc)).isoformat(timespec="seconds")


def _slugify(text: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", str(text or "run")).strip("-").lower()
    return s[:40] or "run"


@dataclass
class Run:
    """One measured session, backed by ``data/runs/<run_id>/``."""

    run_id: str
    dir: Path
    cfg: _config.Config
    idea_id: str | None = None
    profile: str | None = None
    exe: str = ""
    notes: str = ""
    config_diff: dict = field(default_factory=dict)
    manifest: dict = field(default_factory=dict)
    metrics: dict = field(default_factory=dict)

    # -- construction ------------------------------------------------------
    @classmethod
    def create(cls, cfg=None, idea_id=None, slug: str = "run", profile=None, notes: str = "", config_diff=None) -> "Run":
        cfg = cfg or _config.get()
        cfg.ensure_data_dirs()
        run_id = f"{_snapshot.timestamp()}-{_slugify(slug)}"
        d = cfg.runs_dir / run_id
        n = 1
        while d.exists():
            n += 1
            run_id = f"{_snapshot.timestamp()}-{_slugify(slug)}-{n}"
            d = cfg.runs_dir / run_id
        d.mkdir(parents=True)
        run = cls(
            run_id=run_id,
            dir=d,
            cfg=cfg,
            idea_id=idea_id,
            profile=profile or cfg.profile,
            exe=str(cfg.game_exe),
            notes=notes,
            config_diff=dict(config_diff or {}),
        )
        run.manifest = {
            "run_id": run_id,
            "idea_id": idea_id,
            "started": _iso(),
            "finished": None,
            "status": "planned",
            "exe": run.exe,
            "mo2_profile": run.profile,
            "config_diff": run.config_diff,
            "notes": notes,
        }
        run.write_manifest()
        return run

    # -- paths -------------------------------------------------------------
    @property
    def manifest_path(self) -> Path:
        return self.dir / "manifest.json"

    @property
    def metrics_path(self) -> Path:
        return self.dir / "metrics.json"

    @property
    def samples_path(self) -> Path:
        return self.dir / "samples.csv"

    @property
    def log_path(self) -> Path:
        return self.dir / "xray.log"

    def write_manifest(self) -> Path:
        self.manifest["config_diff"] = self.config_diff
        self.manifest_path.write_text(json.dumps(self.manifest, indent=2), encoding="utf-8")
        return self.manifest_path

    def set_status(self, status: str) -> None:
        self.manifest["status"] = status
        self.write_manifest()

    # -- execution ---------------------------------------------------------
    def execute(
        self,
        dry_run: bool = False,
        duration_s: float | None = None,
        timeout_s: float | None = None,
        user_ltx_changes=None,
        mod_toggles=None,
        sampler_backend=None,
        keep_changes: bool = False,
        warmup_s: float | None = None,
        autoload_save: str | None = None,
    ) -> dict:
        """Apply changes, launch (unless *dry_run*), measure, restore.

        Sampling starts only after the warm-up: see :meth:`_wait_for_warmup`.

        Returns the metrics dict.
        """
        cfg = self.cfg
        timeout_s = float(timeout_s if timeout_s is not None else cfg.timeout_s)
        warmup_s = float(warmup_s if warmup_s is not None else cfg.warmup_s)
        # None = use aalo.toml's autoload_save; "" = load by hand
        save = cfg.autoload_save if autoload_save is None else autoload_save
        game_args = None
        if save:
            game_args = autoload_args(save)  # raises on a name -start cannot carry
            if not dry_run and find_save(save, cfg) is None:
                raise FileNotFoundError(
                    f"save {save!r} not found in {cfg.appdata / 'savedgames'}; "
                    "the engine would fall back to the main menu and the run would stall"
                )
            self.manifest["autoload_save"] = save
            if cfg.skip_keypress:
                user_ltx_changes = dict(user_ltx_changes or {})
                user_ltx_changes.setdefault("keypress_on_start", "off")
        snap_dir = None
        work_profile = self.profile
        self.set_status("running")
        t_start = time.perf_counter()

        try:
            if user_ltx_changes or mod_toggles:
                # The snapshot dir already starts with a timestamp, so label it
                # with just the run's slug rather than the whole run_id.
                snap_dir = _snapshot.take(label=self.run_id.split("-", 2)[-1], profile=self.profile, cfg=cfg)
                self.manifest["snapshot"] = str(snap_dir)
            if mod_toggles:
                m = _mo2.MO2(cfg)
                work_profile = m.copy_profile(self.run_id, source=self.profile)
                self.config_diff.update(m.apply_mod_toggles(work_profile, mod_toggles))
                self.manifest["mo2_profile"] = work_profile
            if user_ltx_changes:
                self.config_diff.update(self._apply_user_ltx(user_ltx_changes, work_profile))
            self.write_manifest()

            if dry_run:
                # Seed from the run id so arms differ a little, like real runs.
                seed = int(hashlib.sha256(self.run_id.encode()).hexdigest()[:8], 16)
                measured_s = duration_s or DRY_RUN_DURATION_S
                # Synthesise the warm-up too, then throw it away, so a dry run
                # exercises the same trimming a real run gets by starting the
                # sampler late.  No wall-clock time is actually spent waiting.
                raw = _metrics.synthetic_samples(measured_s + max(0.0, warmup_s), seed=seed)
                samples = _trim_warmup(raw, warmup_s)
                duration = samples[-1].t_s if samples else 0.0
                crashed = False
                log = None
                warmup = {
                    "warmup_s": warmup_s,
                    "warmup_waited": False,
                    "warmup_trimmed_samples": len(raw) - len(samples),
                    "level_marker_seen": None,
                }
                self.manifest["dry_run"] = True
            else:
                samples, duration, crashed, log, warmup = self._launch_and_sample(
                    work_profile, timeout_s, duration_s, sampler_backend, warmup_s, game_args=game_args
                )

            _metrics.write_samples_csv(self.samples_path, samples)
            sampler_label = "synthetic" if dry_run else self.manifest.get("sampler", sampler_backend or "none")
            self.metrics = _metrics.compute_metrics(
                samples,
                duration_s=duration,
                crashed=crashed,
                load_time_s=(log.load_time_s if log else None),
                extra={"log": log.summary() if log else None, "sampler": sampler_label, **warmup},
            )
            _metrics.write_metrics_json(self.metrics_path, self.metrics)
            self.manifest["status"] = "failed" if crashed else "done"
        except Exception as exc:  # keep the run dir usable for debugging
            self.manifest["status"] = "failed"
            self.manifest["error"] = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            self.manifest["finished"] = _iso()
            self.manifest.setdefault("wall_s", round(time.perf_counter() - t_start, 2))
            if snap_dir and not keep_changes:
                _snapshot.restore(snap_dir, cfg=cfg, kinds=("user_ltx",))
                self.manifest["restored_from"] = str(snap_dir)
            # Drop the throwaway profile copy, or the install accumulates one
            # directory per run.  keep_changes keeps it for debugging.
            if work_profile and work_profile.startswith(_mo2.AALO_PREFIX) and not keep_changes:
                try:
                    _mo2.MO2(cfg).delete_profile(work_profile)
                    self.manifest["profile_copy_removed"] = work_profile
                except (OSError, ValueError) as exc:
                    self.manifest["profile_copy_removed"] = f"failed: {exc}"
            self.write_manifest()
        return self.metrics

    # -- internals ---------------------------------------------------------
    def _apply_user_ltx(self, changes: dict, profile: str | None) -> dict:
        target = self.cfg.effective_user_ltx(profile)
        doc = _ltx.load_user(target)
        before = doc.to_dict()
        for key, value in changes.items():
            doc.set(key, value)
        doc.save(target)
        after = doc.to_dict()
        return {k: [before.get(k), after.get(k)] for k in changes if before.get(k) != after.get(k)}

    def _wait_for_warmup(self, warmup_s: float, mtime_before: float, deadline_s: float) -> dict:
        """Hold off sampling until A-Life has settled after the level load.

        ``alife.ltx`` ships with ``auto_switch=true``: switch_distance starts at
        1250 m and collapses to 450 m roughly ten seconds after the level loads.
        Frames captured during that window measure the transient, not the
        configuration under test, so sampling waits for the level-load marker in
        the engine log and then for *warmup_s* more seconds.  If the marker never
        appears (a log that is off, or a build that does not print it) the wait
        still happens, measured from the launch instead.
        """
        info = {
            "warmup_s": warmup_s,
            "warmup_waited": True,
            "warmup_trimmed_samples": 0,  # a real run never records the warm-up
            "level_marker_seen": False,
        }
        if warmup_s <= 0:
            info["warmup_waited"] = False
            return info
        t_start = time.perf_counter()
        deadline = t_start + max(0.0, deadline_s)
        while time.perf_counter() < deadline:
            src = _xraylog.newest_log(cfg=self.cfg)
            if src is not None and src.stat().st_mtime > mtime_before:
                try:
                    parsed = _xraylog.load(src)
                    # GAMMA never prints "Loading level"; the engine's own
                    # "save loaded" / "new game created" line is the real signal
                    if parsed.world_loads or parsed.levels:
                        info["level_marker_seen"] = True
                        info["world_marker"] = (parsed.world_loads or parsed.levels)[-1]
                        break
                except OSError:
                    pass
            time.sleep(2.0)
        info["level_wait_s"] = round(time.perf_counter() - t_start, 2)
        time.sleep(warmup_s)
        return info

    def _launch_and_sample(self, profile, timeout_s, duration_s, sampler_backend, warmup_s: float = 0.0,
                           game_args: str | None = None):
        cfg = self.cfg
        m = _mo2.MO2(cfg)
        cmd = m.command_for(profile=profile if profile != m.selected_profile else None, game_args=game_args)
        self.manifest["command"] = cmd
        self.write_manifest()

        log_before = _xraylog.newest_log(cfg=cfg)
        mtime_before = log_before.stat().st_mtime if log_before else 0.0

        try:
            launcher = subprocess.Popen(cmd, cwd=str(cfg.mo2_root))
        except OSError as exc:
            raise RuntimeError(
                f"could not launch MO2 ({exc}). The GOG GAMMA shortcut needs elevation; "
                "run this harness from an elevated terminal."
            ) from exc

        proc_name = cfg.exe_name
        sampler = _metrics.FrameSampler(proc_name, out_dir=self.dir, sample_hz=cfg.sample_hz, prefer=sampler_backend)
        game = _wait_for_process(proc_name, cfg.launch_grace_s)

        # Warm up before the first sample, so A-Life's auto_switch transient and
        # the shader cache are not counted against the configuration under test.
        # Nothing auto-loads a save: the game boots to the main menu and the
        # player loads one by hand, which easily takes minutes on GAMMA. So wait
        # for the world marker for the whole run timeout, not just the launch
        # grace (60 s would start sampling the main menu). If it never shows,
        # the warm-up runs from the deadline instead.
        warmup = self._wait_for_warmup(warmup_s, mtime_before, deadline_s=float(timeout_s))
        self.manifest["warmup"] = warmup
        self.write_manifest()

        backend = sampler.start()
        self.manifest["sampler"] = backend
        t0 = time.perf_counter()

        deadline = t0 + (duration_s if duration_s else timeout_s)
        while time.perf_counter() < deadline:
            if game is not None and not _is_running(game):
                break
            time.sleep(0.5)
        duration = time.perf_counter() - t0

        if duration_s and game is not None and _is_running(game):
            _terminate(game)
        samples = sampler.stop()
        try:
            launcher.wait(timeout=30)
        except Exception:
            pass

        log = self._collect_log(mtime_before)
        crashed = bool(log and log.crashed)
        return samples, duration, crashed, log, warmup

    def _collect_log(self, mtime_before: float):
        src = _xraylog.newest_log(cfg=self.cfg)
        if src is None or src.stat().st_mtime <= mtime_before:
            return None
        shutil.copy2(src, self.log_path)
        self.manifest["log_source"] = str(src)
        return _xraylog.load(self.log_path)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<Run {self.run_id} {self.manifest.get('status')}>"


def _wait_for_process(name: str, grace_s: float):
    try:
        import psutil  # type: ignore
    except ImportError:
        return None
    deadline = time.perf_counter() + grace_s
    while time.perf_counter() < deadline:
        p = _metrics._find_process(psutil, name)
        if p is not None:
            return p
        time.sleep(1.0)
    return None


def _is_running(proc) -> bool:
    try:
        return proc.is_running() and proc.status() != "zombie"
    except Exception:
        return False


def _terminate(proc) -> None:
    try:
        proc.terminate()
        proc.wait(timeout=30)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


# -- experiments ------------------------------------------------------------


@dataclass
class Experiment:
    """An A/B plan loaded from ``framework/experiments/<name>.toml``."""

    name: str
    idea_id: str | None = None
    repeats: int = 3
    duration_s: float | None = None
    timeout_s: float | None = None
    warmup_s: float | None = None
    notes: str = ""
    profile: str | None = None
    save: str | None = None  # auto-load this save every run (None = aalo.toml default)
    arms: dict = field(default_factory=dict)  # {"baseline": {...}, "variant": {...}}
    path: Path | None = None

    @classmethod
    def from_dict(cls, data: dict, path=None) -> "Experiment":
        arms = {}
        for arm in ("baseline", "variant"):
            spec = data.get(arm) or {}
            arms[arm] = {
                "user_ltx": dict(spec.get("user_ltx") or {}),
                "mods": dict(spec.get("mods") or {}),
                "notes": spec.get("notes", ""),
            }
        for key, spec in data.items():
            if isinstance(spec, dict) and key not in ("baseline", "variant") and ("user_ltx" in spec or "mods" in spec):
                arms[key] = {
                    "user_ltx": dict(spec.get("user_ltx") or {}),
                    "mods": dict(spec.get("mods") or {}),
                    "notes": spec.get("notes", ""),
                }
        return cls(
            name=data.get("name") or (Path(path).stem if path else "experiment"),
            idea_id=data.get("idea_id"),
            repeats=int(data.get("repeats", 3)),
            duration_s=data.get("duration_s"),
            timeout_s=data.get("timeout_s"),
            warmup_s=data.get("warmup_s"),
            notes=data.get("notes", ""),
            profile=data.get("profile"),
            save=data.get("save"),
            arms=arms,
            path=Path(path) if path else None,
        )

    def order(self) -> list[str]:
        """Alternating A/B/A/B so drift affects both arms equally."""
        names = [a for a in ("baseline", "variant") if a in self.arms]
        names += [a for a in self.arms if a not in names]
        out = []
        for _ in range(self.repeats):
            out.extend(names)
        return out


def load_experiment(path, cfg=None) -> Experiment:
    """Load an experiment TOML by path or by bare name under experiments/."""
    cfg = cfg or _config.get()
    p = Path(path)
    if not p.is_file():
        candidate = cfg.experiments_dir / (p.name if p.suffix == ".toml" else f"{p.name}.toml")
        if candidate.is_file():
            p = candidate
        else:
            raise FileNotFoundError(f"experiment not found: {path}")
    with open(p, "rb") as fh:
        data = tomllib.load(fh)
    return Experiment.from_dict(data, p)


def run_once(cfg=None, idea_id=None, slug="run", dry_run=False, duration_s=None, timeout_s=None,
             user_ltx_changes=None, mod_toggles=None, notes="", profile=None, sampler_backend=None,
             warmup_s=None, config_diff=None, autoload_save=None) -> Run:
    """Create and execute a single run."""
    cfg = cfg or _config.get()
    run = Run.create(cfg=cfg, idea_id=idea_id, slug=slug, profile=profile, notes=notes, config_diff=config_diff)
    run.execute(
        dry_run=dry_run,
        duration_s=duration_s,
        timeout_s=timeout_s,
        warmup_s=warmup_s,
        user_ltx_changes=user_ltx_changes,
        mod_toggles=mod_toggles,
        sampler_backend=sampler_backend,
        autoload_save=autoload_save,
    )
    return run


def run_experiment(experiment: Experiment, cfg=None, dry_run: bool = False, repeats=None) -> list[Run]:
    """Execute every arm of *experiment*, alternating A/B for *repeats* rounds."""
    cfg = cfg or _config.get()
    if repeats is not None:
        experiment.repeats = int(repeats)
    runs: list[Run] = []
    for i, arm in enumerate(experiment.order()):
        spec = experiment.arms[arm]
        # The dashboard classifies a run by the first word of its notes: an A run
        # must start with "baseline" and a B run with "variant", and a baseline's
        # notes must never contain the word "variant".
        arm_notes = _arm_notes(arm, spec.get("notes", ""))
        run = Run.create(
            cfg=cfg,
            idea_id=experiment.idea_id,
            slug=f"{experiment.name}-{arm}",
            profile=experiment.profile,
            notes=f"{arm_notes} experiment={experiment.name} round={i // max(1, len(experiment.arms)) + 1}".strip(),
        )
        run.manifest["experiment"] = experiment.name
        run.manifest["arm"] = arm
        run.write_manifest()
        run.execute(
            dry_run=dry_run,
            duration_s=experiment.duration_s,
            timeout_s=experiment.timeout_s,
            warmup_s=experiment.warmup_s,
            user_ltx_changes=spec.get("user_ltx"),
            mod_toggles=spec.get("mods"),
            autoload_save=experiment.save,
        )
        runs.append(run)
    return runs


def autoload_args(save: str) -> str:
    """Engine command line that loads *save* straight away, skipping the menu.

    X-Ray parses ``-start server(<save>/single/alife/load) client(localhost)``;
    the save name sits inside ``(...)`` and ``/`` separates the fields, so
    names containing those characters cannot be expressed.
    """
    save = (save or "").strip()
    if save.lower().endswith(".scop"):
        save = save[:-5]
    bad = set('()/"') & set(save)
    if not save or bad:
        raise ValueError(f"save name {save!r} cannot be auto-loaded (empty or contains {''.join(sorted(bad))})")
    return f"-start server({save}/single/alife/load) client(localhost)"


def find_save(save: str, cfg=None) -> Path | None:
    """The ``.scop`` for *save*, from appdata or the MO2 profile's local saves."""
    cfg = cfg or _config.get()
    name = save[:-5] if save.lower().endswith(".scop") else save
    dirs = [cfg.appdata / "savedgames"]
    try:
        dirs.append(cfg.mo2_root / "profiles" / cfg.profile / "saves")
    except Exception:
        pass
    for d in dirs:
        p = d / f"{name}.scop"
        if p.is_file():
            return p
    return None


def _trim_warmup(samples, warmup_s: float):
    """Drop the first *warmup_s* of samples and rebase the clock to zero.

    A real run never records the warm-up at all, because the sampler starts
    after it.  A dry run generates it and trims it here, so both paths report a
    measurement window of exactly the requested duration.
    """
    if warmup_s <= 0 or not samples:
        return list(samples)
    kept = [s for s in samples if s.t_s >= warmup_s]
    if not kept:  # warm-up swallowed everything; keep the last sample
        kept = samples[-1:]
    offset = kept[0].t_s
    return [
        _metrics.Sample(
            t_s=round(s.t_s - offset, 4),
            frametime_ms=s.frametime_ms,
            fps=s.fps,
            cpu_pct=s.cpu_pct,
            rss_mb=s.rss_mb,
        )
        for s in kept
    ]


def _arm_notes(arm: str, spec_notes: str = "") -> str:
    """Notes text that the dashboard can classify as baseline or variant.

    A baseline run's notes must lead with "baseline" and must not contain the
    word "variant" anywhere, so an arm's own note text is scrubbed of it.
    """
    body = (spec_notes or "").strip()
    if arm == "baseline":
        body = re.sub(r"\bvariants?\b", "arm", body, flags=re.IGNORECASE)
    return f"{arm} {body}".strip()


def list_runs(cfg=None) -> list[dict]:
    """Manifests of all runs, newest first."""
    cfg = cfg or _config.get()
    if not cfg.runs_dir.is_dir():
        return []
    out = []
    for d in sorted(cfg.runs_dir.iterdir(), reverse=True):
        m = d / "manifest.json"
        if not m.is_file():
            continue
        try:
            data = json.loads(m.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        met = d / "metrics.json"
        if met.is_file():
            try:
                data["metrics"] = json.loads(met.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                pass
        out.append(data)
    return out
