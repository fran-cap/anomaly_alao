"""Round-trip parser/writer for X-Ray ``.ltx`` files and for ``user.ltx``.

Two dialects live in the engine:

* **Sectioned ltx** - ``[section]:parent`` headers, ``key = value`` entries,
  ``;`` comments, ``#include "other.ltx"``.  Entries may also appear before any
  header (``fsgame.ltx`` is written that way).
* **user.ltx** - a flat list of console commands, ``name value`` with no ``=``
  and no sections.  Some commands take no argument at all (``default_controls``)
  and ``bind`` lines repeat the same name many times.

Both parsers keep every byte they did not deliberately change: comments, blank
lines, ordering, indentation and inline trailing comments all survive a
parse/dump cycle, so a config edit shows up as a one-line diff.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

__all__ = [
    "LtxFile",
    "UserLtx",
    "parse",
    "parse_user",
    "parse_auto",
    "load",
    "load_user",
    "load_auto",
    "diff_user_ltx",
    "diff_ltx",
    "looks_like_user_ltx",
    "resolve_includes",
]

_SECTION_RE = re.compile(
    r"^\s*\[(?P<name>[^\]]*)\]\s*(?::\s*(?P<parent>[^;]*?))?\s*(?P<comment>;.*)?$"
)
_ENTRY_RE = re.compile(
    r"^(?P<indent>\s*)(?P<key>[^=;\s][^=;]*?)\s*=\s*(?P<value>[^;]*?)\s*(?P<comment>;.*)?$"
)
_BARE_RE = re.compile(r"^(?P<indent>\s*)(?P<key>[^=;\s][^=;]*?)\s*(?P<comment>;.*)?$")
_INCLUDE_RE = re.compile(r"^\s*#include\s+(?P<target>.+?)\s*(?P<comment>;.*)?$", re.IGNORECASE)


@dataclass
class Line:
    """One physical line, tagged with what it is."""

    kind: str  # blank | comment | include | section | entry | bare | raw
    text: str  # verbatim source text (authoritative unless dirty)
    # key/value are "" rather than None on lines that have none (blank,
    # comment, raw), so callers never have to guard before .strip()/.lower().
    key: str = ""
    value: str = ""
    section: str | None = None
    parent: str | None = None
    comment: str | None = None
    indent: str = ""
    dirty: bool = False

    def render(self) -> str:
        if not self.dirty:
            return self.text
        if self.kind == "entry":
            tail = f" {self.comment}" if self.comment else ""
            return f"{self.indent}{self.key} = {self.value}{tail}"
        if self.kind == "bare":
            val = f" {self.value}" if self.value else ""
            tail = f" {self.comment}" if self.comment else ""
            return f"{self.indent}{self.key}{val}{tail}"
        if self.kind == "section":
            head = f"[{self.section}]"
            if self.parent:
                head += f":{self.parent}"
            tail = f" {self.comment}" if self.comment else ""
            return f"{head}{tail}"
        return self.text


class LtxFile:
    """A sectioned ``.ltx`` document that remembers its own formatting."""

    def __init__(self, lines=None, path: Path | None = None, newline: str = "\n"):
        self.lines: list[Line] = lines or []
        self.path = path
        self.newline = newline

    @property
    def sections(self) -> list[str]:
        """Section names in file order.  Entries before the first header sit in
        the implicit section ``None``."""
        return [ln.section for ln in self.lines if ln.kind == "section" and ln.section is not None]

    @property
    def includes(self) -> list[str]:
        return [ln.value for ln in self.lines if ln.kind == "include"]

    def section_parent(self, section: str) -> str | None:
        for ln in self.lines:
            if ln.kind == "section" and ln.section == section:
                return ln.parent
        return None

    def items(self, section: str | None = None) -> list[tuple[str, str]]:
        """``(key, value)`` pairs of *section*; ``None`` means before any header."""
        return [
            (ln.key, ln.value)
            for ln in self.lines
            if ln.kind in ("entry", "bare") and ln.section == section
        ]

    def has_section(self, section: str) -> bool:
        return section in self.sections

    def get(self, section: str | None, key: str, default: str | None = None) -> str | None:
        ln = self._find(section, key)
        return default if ln is None else ln.value

    def _find(self, section: str | None, key: str) -> Line | None:
        lk = key.strip().lower()
        for ln in self.lines:
            if (
                ln.kind in ("entry", "bare")
                and ln.section == section
                and ln.key.strip().lower() == lk
            ):
                return ln
        return None

    def set(self, section: str | None, key: str, value) -> None:
        """Set *key* in *section*, creating the entry (and section) if needed."""
        value = str(value)
        ln = self._find(section, key)
        if ln is not None:
            if ln.value != value:
                ln.value = value
                if ln.kind == "bare":
                    ln.kind = "entry"
                ln.dirty = True
            return
        new = Line(kind="entry", text="", key=key, value=value, section=section, dirty=True)
        idx = self._section_end(section)
        if idx is None:
            if self.lines and self.lines[-1].text.strip():
                self.lines.append(Line("blank", ""))
            self.lines.append(Line("section", "", section=section, dirty=True))
            self.lines.append(new)
        else:
            self.lines.insert(idx, new)

    def delete(self, section: str | None, key: str) -> bool:
        ln = self._find(section, key)
        if ln is None:
            return False
        self.lines.remove(ln)
        return True

    def add_section(self, section: str, parent: str | None = None) -> None:
        if self.has_section(section):
            return
        if self.lines and self.lines[-1].text.strip():
            self.lines.append(Line("blank", ""))
        self.lines.append(Line("section", "", section=section, parent=parent, dirty=True))

    def _section_end(self, section: str | None) -> int | None:
        """Index just past the last non-blank line belonging to *section*."""
        if section is not None and section not in self.sections:
            return None
        last = None
        for i, ln in enumerate(self.lines):
            if ln.section == section and ln.kind != "blank":
                last = i
        if last is None:
            for i, ln in enumerate(self.lines):
                if ln.kind == "section" and ln.section == section:
                    return i + 1
            return 0 if section is None else None
        return last + 1

    def dumps(self) -> str:
        return self.newline.join(ln.render() for ln in self.lines)

    def save(self, path=None, encoding: str = "utf-8") -> Path:
        destination = path or self.path
        if destination is None:
            raise ValueError("no path to save to; pass one or load from a file")
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self.dumps(), encoding=encoding, newline="")
        return target

    def to_dict(self) -> dict:
        out: dict = {}
        for ln in self.lines:
            if ln.kind in ("entry", "bare"):
                out.setdefault(ln.section, {})[ln.key.strip()] = ln.value
        return out

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<LtxFile {self.path} sections={len(self.sections)} lines={len(self.lines)}>"


class UserLtx:
    """Flat ``user.ltx`` console-command list.

    ``bind`` commands legitimately repeat, so :meth:`get` returns the last value
    for a name and :meth:`get_all` returns every occurrence.
    """

    def __init__(self, lines=None, path: Path | None = None, newline: str = "\n"):
        self.lines: list[Line] = lines or []
        self.path = path
        self.newline = newline

    def commands(self) -> list[tuple[str, str]]:
        return [(ln.key, ln.value) for ln in self.lines if ln.kind == "bare"]

    def get_all(self, name: str) -> list[str]:
        n = name.strip().lower()
        return [ln.value for ln in self.lines if ln.kind == "bare" and ln.key.lower() == n]

    def get(self, name: str, default: str | None = None) -> str | None:
        vals = self.get_all(name)
        return vals[-1] if vals else default

    def set(self, name: str, value) -> None:
        """Set console command *name*; appends it when it is not already present."""
        value = "" if value is None else str(value)
        n = name.strip().lower()
        found = [ln for ln in self.lines if ln.kind == "bare" and ln.key.lower() == n]
        if found:
            ln = found[-1]
            if ln.value != value:
                ln.value = value
                ln.dirty = True
            return
        self.lines.append(Line("bare", "", key=name.strip(), value=value, dirty=True))

    def delete(self, name: str) -> bool:
        n = name.strip().lower()
        hit = [ln for ln in self.lines if ln.kind == "bare" and ln.key.lower() == n]
        for ln in hit:
            self.lines.remove(ln)
        return bool(hit)

    def to_dict(self) -> dict:
        """Last value wins; ``bind`` lines fold into one key per bound action."""
        out: dict = {}
        for k, v in self.commands():
            if k.lower() == "bind":
                parts = v.split()
                out["bind " + parts[0] if parts else "bind"] = " ".join(parts[1:])
            else:
                out[k] = v
        return out

    def dumps(self) -> str:
        return self.newline.join(ln.render() for ln in self.lines)

    def save(self, path=None, encoding: str = "utf-8") -> Path:
        destination = path or self.path
        if destination is None:
            raise ValueError("no path to save to; pass one or load from a file")
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self.dumps(), encoding=encoding, newline="")
        return target

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<UserLtx {self.path} commands={len(self.commands())}>"


def _split_newline(text: str):
    newline = "\r\n" if "\r\n" in text else "\n"
    return text.split(newline), newline


def parse(text: str, path=None) -> LtxFile:
    """Parse sectioned ltx text."""
    raw_lines, newline = _split_newline(text)
    lines: list[Line] = []
    current: str | None = None
    for raw in raw_lines:
        stripped = raw.strip()
        if not stripped:
            lines.append(Line("blank", raw, section=current))
            continue
        if stripped.startswith(";") or stripped.startswith("//"):
            lines.append(Line("comment", raw, section=current, comment=stripped))
            continue
        m = _INCLUDE_RE.match(raw)
        if m:
            target = (m.group("target") or "").strip().strip('"')
            lines.append(Line("include", raw, key="#include", value=target, section=current))
            continue
        m = _SECTION_RE.match(raw)
        if m and stripped.startswith("["):
            current = (m.group("name") or "").strip()
            parent = (m.group("parent") or "").strip() or None
            lines.append(
                Line("section", raw, section=current, parent=parent, comment=m.group("comment"))
            )
            continue
        m = _ENTRY_RE.match(raw)
        if m:
            lines.append(
                Line(
                    "entry",
                    raw,
                    key=(m.group("key") or "").strip(),
                    value=(m.group("value") or "").strip(),
                    section=current,
                    comment=(m.group("comment") or None),
                    indent=m.group("indent") or "",
                )
            )
            continue
        m = _BARE_RE.match(raw)
        if m:
            lines.append(
                Line(
                    "bare",
                    raw,
                    key=(m.group("key") or "").strip(),
                    value="",
                    section=current,
                    comment=(m.group("comment") or None),
                    indent=m.group("indent") or "",
                )
            )
            continue
        lines.append(Line("raw", raw, section=current))
    return LtxFile(lines, Path(path) if path else None, newline)


def parse_user(text: str, path=None) -> UserLtx:
    """Parse flat ``user.ltx`` console-command text."""
    raw_lines, newline = _split_newline(text)
    lines: list[Line] = []
    for raw in raw_lines:
        stripped = raw.strip()
        if not stripped:
            lines.append(Line("blank", raw))
            continue
        if stripped.startswith(";") or stripped.startswith("//"):
            lines.append(Line("comment", raw, comment=stripped))
            continue
        indent = raw[: len(raw) - len(raw.lstrip())]
        body, sep, comment = stripped.partition(";")
        body = body.rstrip()
        name, _, value = body.partition(" ")
        lines.append(
            Line(
                "bare",
                raw,
                key=name.strip(),
                value=value.strip(),
                comment=(sep + comment) if sep else None,
                indent=indent,
            )
        )
    return UserLtx(lines, Path(path) if path else None, newline)


def looks_like_user_ltx(text: str) -> bool:
    """True when *text* is a flat command list rather than a sectioned ltx."""
    has_section = False
    equals = 0
    bare = 0
    for raw in text.splitlines():
        s = raw.strip()
        if not s or s.startswith(";") or s.startswith("//") or s.startswith("#"):
            continue
        if s.startswith("["):
            has_section = True
        elif "=" in s:
            equals += 1
        else:
            bare += 1
    if has_section:
        return False
    return bare > equals


def parse_auto(text: str, path=None):
    """Parse with the dialect guessed from the content (and filename)."""
    name = Path(path).name.lower() if path else ""
    if name == "user.ltx" or looks_like_user_ltx(text):
        return parse_user(text, path)
    return parse(text, path)


def _read(path) -> str:
    data = Path(path).read_bytes()
    for enc in ("utf-8-sig", "utf-8", "cp1251", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1", errors="replace")


def load(path) -> LtxFile:
    return parse(_read(path), path)


def load_user(path) -> UserLtx:
    return parse_user(_read(path), path)


def load_auto(path):
    return parse_auto(_read(path), path)


def resolve_includes(path, _seen=None) -> Iterator:
    """Yield ``(path, LtxFile)`` for *path* and every ``#include`` beneath it."""
    p = Path(path).resolve()
    seen = _seen if _seen is not None else set()
    if p in seen or not p.is_file():
        return
    seen.add(p)
    doc = load(p)
    yield p, doc
    for inc in doc.includes:
        target = (p.parent / inc).resolve()
        yield from resolve_includes(target, seen)


def diff_user_ltx(before, after) -> dict:
    """Diff two user.ltx into the contract's ``config_diff`` shape.

    Returns ``{key: [old, new]}``; a side that lacks the key contributes None.
    """
    a = before if isinstance(before, UserLtx) else load_user(before)
    b = after if isinstance(after, UserLtx) else load_user(after)
    da, db = a.to_dict(), b.to_dict()
    out: dict = {}
    for key in sorted(set(da) | set(db)):
        old, new = da.get(key), db.get(key)
        if old != new:
            out[key] = [old, new]
    return out


def diff_ltx(before: LtxFile, after: LtxFile) -> dict:
    """Diff two sectioned ltx documents into ``{"section/key": [old, new]}``."""
    da, db = before.to_dict(), after.to_dict()
    keys = set()
    for d in (da, db):
        for sec, entries in d.items():
            for k in entries:
                keys.add((sec, k))
    out: dict = {}
    for sec, k in sorted(keys, key=lambda t: (t[0] or "", t[1])):
        old = da.get(sec, {}).get(k)
        new = db.get(sec, {}).get(k)
        if old != new:
            out[f"{sec}/{k}" if sec else k] = [old, new]
    return out
