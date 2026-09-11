"""Parser for X-Ray / Anomaly engine logs (``appdata/logs/xray_<user>.log``).

The engine writes one line per event with a severity sigil:

===========  ==========================================================
``*``        informational, usually ``* [x-ray]: ...`` or ``* phase time:``
``!``        warning or recoverable error
``~``        notice
``#``        internal diagnostic
(none)       free-form text, including FATAL ERROR blocks and stack traces
===========  ==========================================================

Some builds prefix a ``[HH:MM:SS]`` or ``[  1.234]`` timestamp.  All of that is
optional, so the parser treats every piece as best-effort and never raises on a
malformed line.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

__all__ = ["LogEntry", "XrayLog", "parse", "load", "newest_log"]

_TS_RE = re.compile(r"^\s*\[(?P<ts>[\d:.\s]+)\]\s?(?P<rest>.*)$")
_SIGIL_RE = re.compile(r"^(?P<sigil>[*!~#$])\s?(?P<rest>.*)$")
_TAG_RE = re.compile(r"^\[(?P<tag>[^\]]+)\]:\s*(?P<rest>.*)$")
_PHASE_RE = re.compile(r"phase time:\s*(?P<ms>\d+)\s*ms", re.IGNORECASE)
_LOADTIME_RE = re.compile(r"(?:level\s+)?load(?:ing)?\s*time[^\d]*(?P<v>[\d.]+)\s*(?P<unit>ms|s|sec)?", re.IGNORECASE)
_LEVEL_RE = re.compile(r"(?:Starting|Loading)\s+level\s*\[?(?P<level>[\w\-\\/. ]+?)\]?\s*$", re.IGNORECASE)
_ALIFE_RE = re.compile(r"\b(?:alife|a-life)\b", re.IGNORECASE)
_NUMKV_RE = re.compile(r"(?P<key>[A-Za-z][\w \-]*?)\s*[:=]\s*(?P<val>-?\d+(?:\.\d+)?)")
_XRAY_USER_RE = re.compile(r"^xray_(?P<user>.+)\.log$", re.IGNORECASE)


@dataclass
class LogEntry:
    """One parsed log line."""

    index: int
    raw: str
    sigil: str = ""
    tag: str = ""
    text: str = ""
    timestamp: str | None = None
    t_s: float | None = None

    @property
    def is_warning(self) -> bool:
        return self.sigil in ("!", "~")

    @property
    def is_info(self) -> bool:
        return self.sigil == "*"


@dataclass
class XrayLog:
    """A parsed engine log."""

    path: Path | None = None
    entries: list[LogEntry] = field(default_factory=list)
    warnings: list[LogEntry] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    stack_trace: list[str] = field(default_factory=list)
    levels: list[str] = field(default_factory=list)
    phase_times_ms: list[int] = field(default_factory=list)
    alife: dict = field(default_factory=dict)
    crashed: bool = False
    load_time_s: float | None = None
    build: str | None = None

    # -- convenience -------------------------------------------------------
    @property
    def error_count(self) -> int:
        return len(self.errors)

    @property
    def warning_count(self) -> int:
        return len(self.warnings)

    def warning_texts(self, limit: int | None = None) -> list[str]:
        texts = [e.text for e in self.warnings]
        return texts[:limit] if limit else texts

    def summary(self) -> dict:
        """The subset the runner folds into metrics.json / manifest notes."""
        return {
            "path": str(self.path) if self.path else None,
            "lines": len(self.entries),
            "load_time_s": self.load_time_s,
            "crashed": self.crashed,
            "errors": self.errors[:20],
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "levels": self.levels,
            "phase_time_total_ms": sum(self.phase_times_ms) if self.phase_times_ms else None,
            "alife": self.alife,
            "build": self.build,
        }


def _parse_timestamp(value: str):
    """Return seconds for ``[  12.345]``, or None for wall-clock stamps."""
    v = value.strip()
    try:
        return float(v)
    except ValueError:
        pass
    parts = v.split(":")
    if len(parts) == 3:
        try:
            h, m, s = (float(p) for p in parts)
            return h * 3600 + m * 60 + s
        except ValueError:
            return None
    return None


def parse(text: str, path=None) -> XrayLog:
    """Parse engine log *text*."""
    log = XrayLog(path=Path(path) if path else None)
    in_fatal = False
    in_stack = False

    for i, raw in enumerate(text.splitlines()):
        line = raw.rstrip()
        if not line.strip():
            continue
        body = line
        ts = None
        t_s = None
        m = _TS_RE.match(body)
        if m and any(ch.isdigit() for ch in m.group("ts")):
            ts = m.group("ts").strip()
            t_s = _parse_timestamp(ts)
            body = m.group("rest")

        sigil = ""
        m = _SIGIL_RE.match(body)
        if m:
            sigil = m.group("sigil")
            body = m.group("rest")

        tag = ""
        m = _TAG_RE.match(body)
        if m:
            tag = m.group("tag")
            body = m.group("rest")

        entry = LogEntry(index=i, raw=raw, sigil=sigil, tag=tag, text=body.strip(), timestamp=ts, t_s=t_s)
        log.entries.append(entry)

        upper = line.upper()
        if "FATAL ERROR" in upper:
            log.crashed = True
            in_fatal = True
            log.errors.append(entry.text or line.strip())
            continue
        if "STACK TRACE" in upper:
            in_stack = True
            continue
        if in_stack:
            if line.startswith((" ", "\t")) or re.match(r"^\w+\.(dll|exe)", line.strip(), re.IGNORECASE):
                log.stack_trace.append(line.strip())
                continue
            in_stack = False
        if in_fatal:
            if line.lstrip().startswith("[error]") or line.lstrip().startswith("["):
                log.errors.append(line.strip())
                continue
            if not line.strip():
                in_fatal = False

        if entry.is_warning:
            log.warnings.append(entry)

        if "error" in entry.text.lower() and entry.sigil == "!":
            log.errors.append(entry.text)

        m = _PHASE_RE.search(line)
        if m:
            log.phase_times_ms.append(int(m.group("ms")))

        m = _LEVEL_RE.search(entry.text)
        if m:
            level = m.group("level").strip()
            if level and level not in log.levels:
                log.levels.append(level)

        m = _LOADTIME_RE.search(entry.text)
        if m and log.load_time_s is None:
            val = float(m.group("v"))
            unit = (m.group("unit") or "s").lower()
            log.load_time_s = val / 1000.0 if unit == "ms" else val

        if _ALIFE_RE.search(entry.text):
            for km in _NUMKV_RE.finditer(entry.text):
                key = km.group("key").strip().lower().replace(" ", "_")
                if key:
                    log.alife[key] = float(km.group("val"))

        if log.build is None and ("build" in entry.text.lower()) and any(c.isdigit() for c in entry.text):
            log.build = entry.text.strip()

    if log.load_time_s is None and log.phase_times_ms:
        log.load_time_s = sum(log.phase_times_ms) / 1000.0

    return log


def load(path) -> XrayLog:
    """Parse the log at *path*, tolerating the engine's mixed encodings."""
    data = Path(path).read_bytes()
    for enc in ("utf-8-sig", "utf-8", "cp1251", "latin-1"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:  # pragma: no cover - latin-1 never fails
        text = data.decode("latin-1", errors="replace")
    return parse(text, path)


def newest_log(logs_dir=None, cfg=None) -> Path | None:
    """Most recently modified ``xray_*.log`` in the logs directory."""
    from . import config as _config

    d = Path(logs_dir) if logs_dir else (cfg or _config.get()).logs_dir
    if not d.is_dir():
        return None
    candidates = [p for p in d.glob("*.log") if _XRAY_USER_RE.match(p.name) or p.name.startswith("xray")]
    if not candidates:
        candidates = list(d.glob("*.log"))
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)
