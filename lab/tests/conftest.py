"""Shared fixtures.

Tests never launch the game and never write inside the game install: the only
real-install access is read-only parsing of fsgame.ltx and modlist.txt.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "framework"))

from aalo import config as _config  # noqa: E402

LAB = REPO


@pytest.fixture(scope="session")
def real_cfg():
    """Configuration pointing at the live install (read-only in tests)."""
    return _config.load(REPO / "framework" / "aalo.toml")


@pytest.fixture(scope="session")
def real_fsgame(real_cfg):
    p = real_cfg.fsgame_ltx
    if not p.is_file():
        pytest.skip(f"game install not present: {p}")
    return p


@pytest.fixture(scope="session")
def real_user_ltx(real_cfg):
    p = real_cfg.user_ltx
    if not p.is_file():
        pytest.skip(f"user.ltx not present: {p}")
    return p


@pytest.fixture(scope="session")
def real_modlist(real_cfg):
    p = real_cfg.profile_dir() / "modlist.txt"
    if not p.is_file():
        pytest.skip(f"MO2 profile not present: {p}")
    return p


@pytest.fixture
def sandbox_cfg(tmp_path, real_cfg):
    """A config whose data/ and user.ltx live in tmp_path.

    The game paths still point at the real install so path logic is exercised,
    but nothing in the install is ever written.
    """
    toml = tmp_path / "aalo.toml"
    user_ltx = tmp_path / "appdata" / "user.ltx"
    user_ltx.parent.mkdir(parents=True, exist_ok=True)
    if real_cfg.user_ltx.is_file():
        user_ltx.write_bytes(real_cfg.user_ltx.read_bytes())
    else:
        user_ltx.write_text("_preset Default\nr2_sun_quality st_opt_medium\n", encoding="utf-8")
    toml.write_text(
        "\n".join(
            [
                "[paths]",
                f"game_root = '{real_cfg.game_root}'",
                f"lab = '{tmp_path}'",
                f"user_ltx = '{user_ltx}'",
                f"appdata = '{user_ltx.parent}'",
                f"logs_dir = '{tmp_path / 'logs'}'",
                f"data = '{tmp_path / 'data'}'",
                "",
                "[mo2]",
                f"profile = '{real_cfg.profile}'",
                f"shortcut = '{real_cfg.shortcut}'",
                f"exe = '{real_cfg.exe_name}'",
                "",
                "[run]",
                "timeout_s = 5",
                "launch_grace_s = 1",
                "sample_hz = 4.0",
            ]
        ),
        encoding="utf-8",
    )
    cfg = _config.load(toml)
    cfg.ensure_data_dirs()
    return cfg
