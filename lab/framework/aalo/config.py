"""Path and settings resolution for the AALO lab.

Everything is loaded from ``framework/aalo.toml`` (tomllib, stdlib in 3.11+).
Nothing else in the package may hardcode a filesystem path.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CONFIG_NAME = "aalo.toml"

# Fallback defaults for this machine, used when aalo.toml is missing a key.
_FALLBACK = {
    "paths": {
        "game_root": r"D:\GOG_Games\Gamma\S.T.A.L.K.E.R. GAMMA",
        "lab": str(Path(__file__).resolve().parents[2]),  # <repo>/lab
    },
    "mo2": {
        "profile": "G.A.M.M.A",
        "shortcut": "Anomaly (DX11-AVX)",
        "exe": "AnomalyDX11AVX.exe",
    },
    "run": {"timeout_s": 900, "launch_grace_s": 60, "sample_hz": 1.0, "warmup_s": 30.0,
            "autoload_save": "", "skip_keypress": True},
}


def find_config_file(start: Path | None = None) -> Path | None:
    """Locate aalo.toml: $AALO_CONFIG, next to the package, or up from *start*."""
    env = os.environ.get("AALO_CONFIG")
    if env:
        p = Path(env)
        return p if p.is_file() else None
    here = Path(__file__).resolve().parent.parent  # framework/
    candidate = here / DEFAULT_CONFIG_NAME
    if candidate.is_file():
        return candidate
    cur = (start or Path.cwd()).resolve()
    for parent in [cur, *cur.parents]:
        c = parent / DEFAULT_CONFIG_NAME
        if c.is_file():
            return c
        c = parent / "framework" / DEFAULT_CONFIG_NAME
        if c.is_file():
            return c
    return None


@dataclass
class Config:
    """Resolved lab configuration.  Read-only by convention."""

    game_root: Path
    anomaly: Path
    mo2_root: Path
    lab: Path
    appdata: Path
    user_ltx: Path
    logs_dir: Path
    data: Path
    profile: str = "G.A.M.M.A"
    shortcut: str = "Anomaly (DX11-AVX)"
    exe_name: str = "AnomalyDX11AVX.exe"
    timeout_s: int = 900
    launch_grace_s: int = 60
    sample_hz: float = 1.0
    warmup_s: float = 30.0
    # save to load straight from the command line (no main menu); "" = load by hand
    autoload_save: str = ""
    # set keypress_on_start off for auto-loaded runs, so nobody has to press a key
    skip_keypress: bool = True
    source: Path | None = None
    raw: dict = field(default_factory=dict, repr=False)

    # -- derived locations -------------------------------------------------
    @property
    def mo2_ini(self) -> Path:
        return self.mo2_root / "ModOrganizer.ini"

    @property
    def mo2_exe(self) -> Path:
        return self.mo2_root / "ModOrganizer.exe"

    @property
    def profiles_dir(self) -> Path:
        return self.mo2_root / "profiles"

    @property
    def mods_dir(self) -> Path:
        return self.mo2_root / "mods"

    @property
    def game_exe(self) -> Path:
        return self.anomaly / "bin" / self.exe_name

    @property
    def fsgame_ltx(self) -> Path:
        return self.anomaly / "fsgame.ltx"

    @property
    def runs_dir(self) -> Path:
        return self.data / "runs"

    @property
    def snapshots_dir(self) -> Path:
        return self.data / "snapshots"

    @property
    def ideas_file(self) -> Path:
        return self.data / "ideas.json"

    @property
    def experiments_dir(self) -> Path:
        return Path(__file__).resolve().parent.parent / "experiments"

    def profile_dir(self, profile: str | None = None) -> Path:
        return self.profiles_dir / (profile or self.profile)

    def effective_user_ltx(self, profile: str | None = None) -> Path:
        """The user.ltx the engine actually reads, for *profile*.

        The G.A.M.M.A. profile sets ``LocalSettings=true``, so Mod Organizer 2
        shadows ``Anomaly/appdata`` with the profile directory: once the game has
        been launched at least once, ``profiles/<profile>/user.ltx`` is the
        authoritative file and the copy in appdata is stale.  The same holds for
        an ``aalo-`` profile copy, which carries its own user.ltx.

        Until that first launch the profile-local file does not exist yet, so
        this falls back to ``appdata/user.ltx``.  Always resolve through here
        rather than touching :attr:`user_ltx` directly.
        """
        local = self.profile_dir(profile) / "user.ltx"
        return local if local.is_file() else self.user_ltx

    def user_ltx_is_profile_local(self, profile: str | None = None) -> bool:
        """True when the profile shadows appdata with its own user.ltx."""
        return (self.profile_dir(profile) / "user.ltx").is_file()

    def as_dict(self) -> dict:
        out = {}
        for k, v in self.__dict__.items():
            if k == "raw":
                continue
            out[k] = str(v) if isinstance(v, Path) else v
        return out

    def ensure_data_dirs(self) -> None:
        for d in (self.data, self.runs_dir, self.snapshots_dir):
            d.mkdir(parents=True, exist_ok=True)


def _merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load(path: str | os.PathLike | None = None) -> Config:
    """Load configuration from *path* or the discovered aalo.toml."""
    cfg_path = Path(path) if path else find_config_file()
    raw: dict = {}
    if cfg_path and Path(cfg_path).is_file():
        with open(cfg_path, "rb") as fh:
            raw = tomllib.load(fh)
    merged = _merge(_FALLBACK, raw)
    p = merged.get("paths", {})

    game_root = Path(p["game_root"])
    anomaly = Path(p.get("anomaly") or game_root / "Anomaly")
    mo2_root = Path(p.get("mo2_root") or game_root / "GAMMA")
    lab = Path(p["lab"])
    appdata = Path(p.get("appdata") or anomaly / "appdata")
    user_ltx = Path(p.get("user_ltx") or appdata / "user.ltx")
    logs_dir = Path(p.get("logs_dir") or appdata / "logs")
    data = Path(p.get("data") or lab / "data")

    m = merged.get("mo2", {})
    r = merged.get("run", {})
    return Config(
        game_root=game_root,
        anomaly=anomaly,
        mo2_root=mo2_root,
        lab=lab,
        appdata=appdata,
        user_ltx=user_ltx,
        logs_dir=logs_dir,
        data=data,
        profile=m.get("profile", "G.A.M.M.A"),
        shortcut=m.get("shortcut", "Anomaly (DX11-AVX)"),
        exe_name=m.get("exe", "AnomalyDX11AVX.exe"),
        timeout_s=int(r.get("timeout_s", 900)),
        launch_grace_s=int(r.get("launch_grace_s", 60)),
        sample_hz=float(r.get("sample_hz", 1.0)),
        warmup_s=float(r.get("warmup_s", 30.0)),
        autoload_save=str(r.get("autoload_save", "") or ""),
        skip_keypress=bool(r.get("skip_keypress", True)),
        source=Path(cfg_path) if cfg_path else None,
        raw=merged,
    )


_cached: Config | None = None


def get(reload: bool = False) -> Config:
    """Process-wide cached configuration."""
    global _cached
    if _cached is None or reload:
        _cached = load()
    return _cached
