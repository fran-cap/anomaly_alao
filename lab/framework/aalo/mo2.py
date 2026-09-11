"""Mod Organizer 2 integration: read the instance, copy profiles, toggle mods.

Safety rule enforced here: the live profile is never modified.  Every mutation
goes through :func:`copy_profile`, which clones ``profiles/<name>`` into
``profiles/aalo-<label>`` and edits the clone.
"""

from __future__ import annotations

import configparser
import shutil
from dataclasses import dataclass
from pathlib import Path

from . import config as _config

__all__ = [
    "ModEntry",
    "ModList",
    "MO2",
    "read_ini",
    "AALO_PREFIX",
]

AALO_PREFIX = "aalo-"

# modlist.txt line prefixes written by MO2.
ENABLED = "+"
DISABLED = "-"
SEPARATOR = "*"


@dataclass
class ModEntry:
    """One line of ``modlist.txt``."""

    prefix: str
    name: str

    @property
    def enabled(self) -> bool:
        return self.prefix == ENABLED

    @property
    def is_separator(self) -> bool:
        # MO2 marks separators either with the '*' prefix or a _separator suffix.
        return self.prefix == SEPARATOR or self.name.endswith("_separator")

    def render(self) -> str:
        return f"{self.prefix}{self.name}"


class ModList:
    """``modlist.txt`` with its header comments and load order preserved.

    MO2 stores the list in reverse priority order (the winning mod is first).
    """

    def __init__(self, entries=None, header=None, path: Path | None = None, newline: str = "\n"):
        self.entries: list[ModEntry] = entries or []
        self.header: list[str] = header or []
        self.path = path
        self.newline = newline

    @classmethod
    def parse(cls, text: str, path=None) -> "ModList":
        newline = "\r\n" if "\r\n" in text else "\n"
        header: list[str] = []
        entries: list[ModEntry] = []
        for raw in text.split(newline):
            line = raw.rstrip("\r")
            if not line.strip():
                continue
            if line.startswith("#"):
                header.append(line)
                continue
            prefix, name = line[0], line[1:]
            if prefix not in (ENABLED, DISABLED, SEPARATOR):
                prefix, name = ENABLED, line
            entries.append(ModEntry(prefix, name))
        return cls(entries, header, Path(path) if path else None, newline)

    @classmethod
    def load(cls, path) -> "ModList":
        return cls.parse(Path(path).read_text(encoding="utf-8-sig"), path)

    def dumps(self) -> str:
        lines = list(self.header) + [e.render() for e in self.entries]
        return self.newline.join(lines) + self.newline

    def save(self, path=None) -> Path:
        destination = path or self.path
        if destination is None:
            raise ValueError("no path to save to; pass one or load from a file")
        target = Path(destination)
        target.write_text(self.dumps(), encoding="utf-8", newline="")
        return target

    # -- queries -----------------------------------------------------------
    def find(self, name: str) -> ModEntry | None:
        n = name.strip().lower()
        for e in self.entries:
            if e.name.lower() == n:
                return e
        return None

    def search(self, needle: str) -> list[ModEntry]:
        n = needle.lower()
        return [e for e in self.entries if n in e.name.lower()]

    def mods(self, include_separators: bool = False) -> list[ModEntry]:
        return [e for e in self.entries if include_separators or not e.is_separator]

    def enabled_mods(self) -> list[str]:
        return [e.name for e in self.mods() if e.enabled]

    def disabled_mods(self) -> list[str]:
        return [e.name for e in self.mods() if not e.enabled]

    # -- mutation ----------------------------------------------------------
    def set_enabled(self, name: str, enabled: bool) -> bool:
        """Flip one mod.  Returns False when the mod is not in the list."""
        e = self.find(name)
        if e is None:
            return False
        e.prefix = ENABLED if enabled else DISABLED
        return True

    def __len__(self) -> int:
        return len(self.entries)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<ModList {self.path} mods={len(self.mods())} enabled={len(self.enabled_mods())}>"


def read_ini(path) -> configparser.ConfigParser:
    """Read a Qt-style INI (BOM, ``@ByteArray(...)`` values, ``%`` in values)."""
    cp = configparser.ConfigParser(interpolation=None, strict=False, delimiters=("=",))
    # MO2 keys are case sensitive, so defeat configparser's lowercasing.
    cp.optionxform = str  # type: ignore[assignment]
    cp.read_string(Path(path).read_text(encoding="utf-8-sig"))
    return cp


def _unwrap(value: str) -> str:
    """Strip a Qt ``@ByteArray(...)`` wrapper and surrounding quotes."""
    v = value.strip()
    if v.startswith("@ByteArray(") and v.endswith(")"):
        v = v[len("@ByteArray(") : -1]
    if len(v) >= 2 and v[0] == v[-1] == '"':
        v = v[1:-1]
    return v


@dataclass
class Executable:
    """An entry from the MO2 ``[customExecutables]`` table."""

    index: int
    title: str
    binary: str
    arguments: str = ""
    working_directory: str = ""


class MO2:
    """A Mod Organizer 2 portable instance."""

    def __init__(self, cfg=None):
        self.cfg = cfg or _config.get()
        self.root = self.cfg.mo2_root
        self._ini = None

    # -- instance ----------------------------------------------------------
    @property
    def ini(self) -> configparser.ConfigParser:
        if self._ini is None:
            self._ini = read_ini(self.cfg.mo2_ini)
        return self._ini

    @property
    def game_path(self) -> Path:
        return Path(_unwrap(self.ini.get("General", "gamePath", fallback=str(self.cfg.anomaly))))

    @property
    def selected_profile(self) -> str:
        return _unwrap(self.ini.get("General", "selected_profile", fallback=self.cfg.profile))

    @property
    def version(self) -> str:
        return self.ini.get("General", "version", fallback="unknown")

    def executables(self) -> list[Executable]:
        """Parse ``[customExecutables]`` (``N\\key=value`` rows)."""
        if not self.ini.has_section("customExecutables"):
            return []
        rows: dict = {}
        for key, value in self.ini.items("customExecutables"):
            if "\\" not in key:
                continue
            idx, _, field = key.partition("\\")
            if not idx.isdigit():
                continue
            rows.setdefault(int(idx), {})[field] = _unwrap(value)
        out = []
        for idx in sorted(rows):
            r = rows[idx]
            out.append(
                Executable(
                    index=idx,
                    title=r.get("title", ""),
                    binary=r.get("binary", ""),
                    arguments=r.get("arguments", ""),
                    working_directory=r.get("workingDirectory", ""),
                )
            )
        return out

    def find_executable(self, title: str) -> Executable | None:
        t = title.strip().lower()
        for e in self.executables():
            if e.title.strip().lower() == t:
                return e
        return None

    # -- profiles ----------------------------------------------------------
    def profiles(self) -> list[str]:
        d = self.cfg.profiles_dir
        if not d.is_dir():
            return []
        return sorted(p.name for p in d.iterdir() if p.is_dir())

    def profile_dir(self, profile: str | None = None) -> Path:
        return self.cfg.profile_dir(profile)

    def modlist_path(self, profile: str | None = None) -> Path:
        return self.profile_dir(profile) / "modlist.txt"

    def modlist(self, profile: str | None = None) -> ModList:
        return ModList.load(self.modlist_path(profile))

    def mods_installed(self) -> list[str]:
        d = self.cfg.mods_dir
        if not d.is_dir():
            return []
        return sorted(p.name for p in d.iterdir() if p.is_dir())

    def copy_profile(self, label: str, source: str | None = None, overwrite: bool = True) -> str:
        """Clone *source* into a working profile named ``aalo-<label>``.

        The live profile is left untouched; all experiment edits target the copy.
        """
        src_name = source or self.cfg.profile
        src = self.profile_dir(src_name)
        if not src.is_dir():
            raise FileNotFoundError(f"MO2 profile not found: {src}")
        name = label if label.startswith(AALO_PREFIX) else AALO_PREFIX + label
        dst = self.cfg.profiles_dir / name
        if dst.exists():
            if not overwrite:
                return name
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
        return name

    def delete_profile(self, name: str) -> bool:
        """Delete an ``aalo-`` working profile.  Refuses anything else."""
        if not name.startswith(AALO_PREFIX):
            raise ValueError(f"refusing to delete non-aalo profile: {name}")
        d = self.cfg.profiles_dir / name
        if not d.is_dir():
            return False
        shutil.rmtree(d)
        return True

    def set_mod_enabled(self, profile: str, mod: str, enabled: bool) -> bool:
        """Enable/disable *mod* in *profile*.  Only ``aalo-`` profiles may change."""
        if not profile.startswith(AALO_PREFIX):
            raise ValueError(
                f"refusing to edit live profile {profile!r}; copy it with copy_profile() first"
            )
        ml = self.modlist(profile)
        if not ml.set_enabled(mod, enabled):
            return False
        ml.save()
        return True

    def apply_mod_toggles(self, profile: str, toggles: dict) -> dict:
        """Apply ``{mod_name: bool}`` to *profile*; returns the applied diff.

        The diff uses the contract's ``config_diff`` shape, keyed ``mod/<name>``.
        """
        if not profile.startswith(AALO_PREFIX):
            raise ValueError(
                f"refusing to edit live profile {profile!r}; copy it with copy_profile() first"
            )
        ml = self.modlist(profile)
        diff: dict = {}
        for mod, want in toggles.items():
            e = ml.find(mod)
            if e is None:
                diff[f"mod/{mod}"] = [None, "missing"]
                continue
            old = "enabled" if e.enabled else "disabled"
            new = "enabled" if want else "disabled"
            if old != new:
                e.prefix = ENABLED if want else DISABLED
                diff[f"mod/{mod}"] = [old, new]
        ml.save()
        return diff

    # -- launching ---------------------------------------------------------
    def launch_command(self, shortcut: str | None = None) -> list[str]:
        """``ModOrganizer.exe "moshortcut://:<title>"`` - the documented launch."""
        title = shortcut or self.cfg.shortcut
        return [str(self.cfg.mo2_exe), f"moshortcut://:{title}"]

    def launch_command_run(self, exe=None, profile: str | None = None, game_args: str | None = None,
                           executable: str | None = None) -> list[str]:
        """``ModOrganizer.exe [-p <profile>] run [-a <args>] (-e <title> | <exe>)``.

        Used whenever the shortcut form cannot express the launch: a profile
        other than the selected one, or extra arguments for the game. ``-p`` is
        a global MO2 option and goes *before* ``run``; ``-a`` and ``-e`` belong
        to ``run``. With *executable* set, ``-e`` runs that configured MO2
        executable by title, so its working directory is inherited (the engine
        resolves gamedata from it); otherwise *exe* (default: the game binary)
        is run directly.
        """
        cmd = [str(self.cfg.mo2_exe)]
        if profile:
            cmd += ["-p", profile]
        cmd.append("run")
        if game_args:
            cmd += ["-a", game_args]
        if executable:
            cmd += ["-e", executable]
        else:
            cmd.append(str(exe or self.cfg.game_exe))
        return cmd

    def command_for(self, profile: str | None = None, shortcut: str | None = None,
                    game_args: str | None = None) -> list[str]:
        """Pick the launch form that can honour *profile* and *game_args*."""
        other_profile = bool(profile and profile != self.selected_profile)
        if other_profile or game_args:
            return self.launch_command_run(
                profile=profile if other_profile else None,
                game_args=game_args,
                executable=shortcut or self.cfg.shortcut,
            )
        return self.launch_command(shortcut)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<MO2 {self.root} profile={self.cfg.profile}>"
