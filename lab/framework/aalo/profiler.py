"""Parser for the ALAO script-side profiler dumps (idea I-048).

The overlay mod in ``lab/profiler`` prints one block of ``ALAOPROF|`` lines into
the engine log every dump window.  The engine log is already copied into every
run directory as ``xray.log`` by :mod:`aalo.runner`, so parsing a profiled run
means reading that file - there is no second artefact to collect.

Line grammar (version 1), pipe separated, ``key=value`` after the first three
fields.  A line may carry an engine timestamp/sigil prefix, so we look for
``ALAOPROF|`` anywhere in the line rather than at column 0::

    ALAOPROF|1|hdr|ts=..|timer=profile_timer|units_per_ms=..|overhead_ns=..|..
    ALAOPROF|1|win|seq=1|t0=..|t1=..|span_ms=..|frames=..|total_units=..|..
    ALAOPROF|1|cb|seq=1|name=actor_on_update|calls=..|units=..|nested=..
    ALAOPROF|1|lst|seq=1|name=actor_on_update#foo.script:412|calls=..|units=..
    ALAOPROF|1|hit|seq=1|scope=cb|name=..|max=..|above=..|first_t=..|first_frame=..|first_units=..|floor=..|b=0,1,..
    ALAOPROF|1|eow|seq=1|frames_total=..
    ALAOPROF|1|err|install failed: ..

``hit`` lines (idea I-058, only from the hitch build of the overlay) are the odd
one out: everything else is per window and gets reset by the dump, while a
``hit`` line is a RUN-scoped running total.  A hitch is a rare event and the
interesting one - the first inventory open of the session - lives in the very
first window, which every report drops.  So the last ``hit`` line for a name is
the whole-run answer, and two windows' lines subtract to give one window's.
``b`` are the counts for buckets 1..N, bucket ``i`` covering
``[floor*2^(i-1), floor*2^i)`` in timer units, with the last bucket open-ended;
calls below the floor are not counted there at all and are recovered as
``calls - above`` from the per-window ``cb``/``lst`` lines.

``units`` are whatever the engine's ``profile_timer`` counts in; the profiler
calibrates them against ``os.clock`` at startup and reports the ratio as
``units_per_ms``.  Everything here converts through that ratio and refuses to
report milliseconds when it is missing.

Three more line kinds come from the I-062 *walkout* build
(``lab/profiler-walkout``), which opens the doors into Lua that
``make_callback`` is not - object binders, engine-called globals, time-event
bodies - and adds a per-FRAME line::

    ALAOPROF|1|wdr|ts=..|walkout=bnd=140+update,eng=5,evt=3|binder_update=true|frames=on|frame_floor_ms=12|..
    ALAOPROF|1|axs|seq=1|axis=bnd|name=xr_motivator.motivator_binder.update|calls=..|units=..|nested=..
    ALAOPROF|1|frm|n=1|frame=8123|t=..|ms=43|dt_dev=..|u_top=..|n_top=..|u_cb=..|u_bnd=..|u_eng=..|u_evt=..|spawn=..|destroy=..|gc0=..|gc1=..|after_log=0|top=a~u,b~u,c~u
    ALAOPROF|1|frs|seq=1|lines=..|suppressed=..|floor_ms=12|wrapped_bnd=..|..
    ALAOPROF|1|trace|n=1|name=..|units=..|frame=..|t=..|pre=..|post=..

``wdr`` is a SECOND header line rather than more fields on ``hdr``, so every
parser written against ``hdr`` keeps working.  ``axs`` is a ``cb`` line with an
axis tag: binder time is deliberately not mixed into the callback ranking,
because a binder ``:update`` contains the callbacks it fires.  ``frm`` is the
line that answers "script or engine" for one slow frame: ``u_top`` is the union
of the script regions that were top level in it (nothing double counted across
axes), so ``ms`` minus ``u_top`` in milliseconds is engine plus whatever Lua the
wrap lists do not reach.  ``trace`` (I-063) is one line per call of a named
listener with opaque before/after covariates.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "Header", "CallbackWindow", "Window", "HitchStat", "ProfileLog",
    "WalkoutHeader", "SlowFrame", "TraceCall",
    "parse", "load", "load_run", "spread", "compare_runs",
]

_LINE_RE = re.compile(r"ALAOPROF\|(?P<ver>\d+)\|(?P<kind>\w+)\|(?P<rest>.*)$")


def _kv(rest: str) -> dict:
    out = {}
    for part in rest.split("|"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def _num(d: dict, key, default=None):
    v = d.get(key)
    if v is None or v == "nil":
        return default
    try:
        f = float(v)
    except ValueError:
        return default
    return int(f) if f.is_integer() and "." not in v else f


@dataclass
class Header:
    timer: str = ""
    units_per_ms: float | None = None
    overhead_ns: float | None = None
    calib_ms: float | None = None
    calib_units: float | None = None
    make_callback: bool = False
    binders: str = "off"
    listeners: str = "off"
    hitch: str = "off"
    hitch_floor_ms: float | None = None
    hitch_buckets: int | None = None
    dump_ms: float | None = None
    ts: float | None = None
    raw: str = ""

    @property
    def usable(self) -> bool:
        """True when dumps can be converted to milliseconds at all."""
        return bool(self.units_per_ms) and self.make_callback


@dataclass
class CallbackWindow:
    name: str
    calls: int = 0
    units: float = 0.0
    nested: int = 0


@dataclass
class HitchStat:
    """One run-scoped ``hit`` line: the tail of one callback or one listener."""
    name: str
    scope: str = "cb"
    max_units: float = 0.0
    above: int = 0              # calls at or above the floor
    first_t: float = 0.0        # time_global() of the first slow call
    first_frame: int = 0
    first_units: float = 0.0
    floor_units: float = 0.0
    buckets: list = field(default_factory=list)   # counts for buckets 1..N

    def bucket_floor_units(self, i: int) -> float:
        """Lower edge of 1-based bucket *i*, in timer units."""
        return self.floor_units * (2 ** (i - 1))

    def percentile_units(self, pct: float, total_calls: int) -> tuple:
        """Coarse percentile as a ``(low, high)`` bracket in timer units.

        *total_calls* is every call of the name, fast ones included - the
        histogram only holds the slow tail, so the fast calls have to be handed
        in from the ``cb``/``lst`` lines.  ``high`` is ``None`` for the
        open-ended top bucket, and the bracket is ``(0, floor)`` whenever the
        percentile falls among the calls that never crossed the floor.
        """
        total = max(total_calls, self.above)
        if total <= 0:
            return (0.0, self.floor_units)
        want = total * (1.0 - pct / 100.0)   # how many calls may sit above it
        seen = 0.0
        for i in range(len(self.buckets), 0, -1):
            seen += self.buckets[i - 1]
            if seen >= want:
                lo = self.bucket_floor_units(i)
                hi = None if i == len(self.buckets) else lo * 2
                return (lo, hi)
        return (0.0, self.floor_units)


@dataclass
class WalkoutHeader:
    """The I-062 ``wdr`` line: which extra doors this build actually opened."""
    walkout: str = "off"
    binder_update: bool = False
    frames: str = "off"
    frame_floor_ms: float | None = None
    frame_max: int | None = None
    rescan_every: int | None = None
    pending: int = 0
    misses: str = ""
    ts: float | None = None
    raw: str = ""

    @property
    def wrapped(self) -> dict:
        """``{"bnd": 140, "eng": 5, "evt": 3}`` out of the ``walkout=`` field."""
        out = {}
        for part in (self.walkout or "").split(","):
            if "=" not in part:
                continue
            k, v = part.split("=", 1)
            v = v.split("+")[0]
            try:
                out[k.strip()] = int(v)
            except ValueError:
                out[k.strip()] = v
        return out


@dataclass
class SlowFrame:
    """One ``frm`` line: a frame that crossed the floor, and what was in it.

    ``ms`` is the ``time_global()`` delta and ``dt_dev`` is
    ``device().time_delta`` exactly as the engine handed it over - the overlay
    prints both because which of them is truthful at frame granularity is a
    question the first run answers, not one to assume.
    """
    n: int = 0
    frame: int = 0
    t: float = 0.0
    ms: float = 0.0
    dt_dev: float | None = None
    u_top: float = 0.0          # union of top-level script regions, timer units
    n_top: int = 0
    u_cb: float = 0.0
    u_bnd: float = 0.0
    u_eng: float = 0.0
    u_evt: float = 0.0
    spawn: int = 0
    destroy: int = 0
    gc0: float = 0.0            # collectgarbage("count") KB at the frame's start
    gc1: float = 0.0            # ... and at its end
    after_log: int = 0          # this frame paid for the previous frm printf
    top: list = field(default_factory=list)   # [(label, units), ...] worst first

    @property
    def gc_delta_kb(self) -> float:
        """Negative means the collector freed memory inside this frame."""
        return self.gc1 - self.gc0


@dataclass
class TraceCall:
    """One I-063 ``trace`` line: a single call of a named listener."""
    n: int = 0
    name: str = ""
    units: float = 0.0
    frame: int = 0
    t: float = 0.0
    pre: str = ""
    post: str = ""

    @staticmethod
    def _probe(s: str) -> dict:
        out = {}
        for part in (s or "").split(","):
            if "=" in part:
                k, v = part.split("=", 1)
                try:
                    out[k] = int(v)
                except ValueError:
                    out[k] = v
        return out

    @property
    def pre_kv(self) -> dict:
        return self._probe(self.pre)

    @property
    def post_kv(self) -> dict:
        return self._probe(self.post)

    def grew(self, key: str) -> int | None:
        """How much *key* grew across the call, or None if it is not numeric."""
        a, b = self.pre_kv.get(key), self.post_kv.get(key)
        if isinstance(a, int) and isinstance(b, int):
            return b - a
        return None


@dataclass
class Window:
    seq: int
    t0: float | None = None
    t1: float | None = None
    span_ms: float | None = None
    frames: int = 0
    total_units: float = 0.0
    calls: int = 0
    nested: int = 0
    names: int = 0
    entries: dict = field(default_factory=dict)
    # per-listener rows, only present when the overlay ran with WRAP_LISTENERS
    listeners: dict = field(default_factory=dict)
    # I-062: {axis tag: {name: CallbackWindow}} for the bnd / eng / evt axes
    axes: dict = field(default_factory=dict)
    complete: bool = False

    @property
    def sum_units(self) -> float:
        """Sum of the per-callback lines, which should match ``total_units``."""
        return sum(e.units for e in self.entries.values())


@dataclass
class ProfileLog:
    path: Path | None = None
    header: Header | None = None
    windows: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    # (scope, name) -> the LAST hit line seen, i.e. the whole-run totals
    hitches: dict = field(default_factory=dict)
    # I-062 / I-063, run scoped and in log order
    walkout: WalkoutHeader | None = None
    frames: list = field(default_factory=list)
    traces: list = field(default_factory=list)

    # -- conversions -------------------------------------------------------
    @property
    def units_per_ms(self) -> float | None:
        return self.header.units_per_ms if self.header else None

    def to_ms(self, units: float) -> float | None:
        upm = self.units_per_ms
        return (units / upm) if upm else None

    def good_windows(self, drop_first: int = 1, min_frames: int = 30) -> list:
        """Complete windows worth quoting.

        The first window straddles the level load and the warm-up, so it is
        dropped by default; a window with almost no frames in it is a truncated
        tail (the game was killed mid-window) and is dropped too.
        """
        ws = [w for w in self.windows if w.complete and w.frames >= min_frames]
        return ws[drop_first:] if drop_first else ws

    def window_ms_per_frame(self, drop_first: int = 1) -> list:
        out = []
        for w in self.good_windows(drop_first):
            ms = self.to_ms(w.total_units)
            if ms is not None and w.frames:
                out.append(ms / w.frames)
        return out

    def fps_from_windows(self, drop_first: int = 1) -> list:
        """Frames per second per window - a free sanity check on the hook."""
        out = []
        for w in self.good_windows(drop_first):
            if w.span_ms:
                out.append(w.frames * 1000.0 / w.span_ms)
        return out

    def ranking(self, top: int | None = 20, drop_first: int = 1, listeners: bool = False) -> list:
        """Per-callback ms/frame over the kept windows, biggest first.

        With *listeners* true, rank the individual subscribers instead of the
        callback names - only populated when the overlay ran WRAP_LISTENERS.
        """
        ws = self.good_windows(drop_first)
        frames = sum(w.frames for w in ws)
        if not frames:
            return []
        agg: dict = {}
        for w in ws:
            for name, e in (w.listeners if listeners else w.entries).items():
                a = agg.setdefault(name, {"units": 0.0, "calls": 0, "nested": 0})
                a["units"] += e.units
                a["calls"] += e.calls
                a["nested"] += e.nested
        total_units = sum(a["units"] for a in agg.values()) or 1.0
        rows = []
        for name, a in agg.items():
            rows.append({
                "name": name,
                "ms_per_frame": (self.to_ms(a["units"]) or 0.0) / frames,
                "calls_per_frame": a["calls"] / frames,
                "nested_per_frame": a["nested"] / frames,
                "share_pct": 100.0 * a["units"] / total_units,
                "us_per_call": ((self.to_ms(a["units"]) or 0.0) * 1000.0 / a["calls"]) if a["calls"] else None,
            })
        rows.sort(key=lambda r: r["ms_per_frame"], reverse=True)
        return rows[:top] if top else rows

    def hitch_ranking(self, scope: str | None = None, top: int | None = None,
                      pct: float = 99.0) -> list:
        """What the tail of each callback / listener looks like, worst first.

        Ranked by ``max_ms``, because a hitch is judged by its worst frame and
        not by its average.  Unlike :meth:`ranking` this ignores ``drop_first``
        entirely: ``hit`` lines are run-scoped and the first window is exactly
        where the first-open hitch lives.
        """
        if not self.hitches:
            return []
        # every call of the name, fast ones included, over the whole run
        totals: dict = {}
        for w in self.windows:
            for n, e in w.entries.items():
                totals[("cb", n)] = totals.get(("cb", n), 0) + e.calls
            for n, e in w.listeners.items():
                totals[("lst", n)] = totals.get(("lst", n), 0) + e.calls
        rows = []
        for (sc, name), h in self.hitches.items():
            if scope and sc != scope:
                continue
            calls = totals.get((sc, name), h.above)
            lo, hi = h.percentile_units(pct, calls)
            rows.append({
                "scope": sc,
                "name": name,
                "calls": calls,
                "above_floor": h.above,
                "max_ms": self.to_ms(h.max_units),
                "first_ms": self.to_ms(h.first_units),
                "first_t": h.first_t,
                "first_frame": h.first_frame,
                "floor_ms": self.to_ms(h.floor_units),
                "p_pct": pct,
                "p_lo_ms": self.to_ms(lo),
                "p_hi_ms": self.to_ms(hi) if hi is not None else None,
                "buckets": list(h.buckets),
            })
        rows.sort(key=lambda r: (r["max_ms"] or 0.0), reverse=True)
        return rows[:top] if top else rows

    # -- I-062 walkout ------------------------------------------------------
    def axis_ranking(self, axis: str, top: int | None = 20, drop_first: int = 1) -> list:
        """Per-name ms/frame on one of the extra axes (``bnd`` / ``eng`` / ``evt``).

        Deliberately a separate call from :meth:`ranking`: the axes overlap by
        construction (a binder ``:update`` contains the callbacks it fires), so
        adding them would double count.  ``frm`` lines are where the
        non-overlapping total lives.
        """
        ws = self.good_windows(drop_first)
        frames = sum(w.frames for w in ws)
        if not frames:
            return []
        agg: dict = {}
        for w in ws:
            for name, e in (w.axes.get(axis) or {}).items():
                a = agg.setdefault(name, {"units": 0.0, "calls": 0, "nested": 0})
                a["units"] += e.units
                a["calls"] += e.calls
                a["nested"] += e.nested
        rows = []
        for name, a in agg.items():
            rows.append({
                "axis": axis,
                "name": name,
                "ms_per_frame": (self.to_ms(a["units"]) or 0.0) / frames,
                "calls_per_frame": a["calls"] / frames,
                "nested_per_frame": a["nested"] / frames,
                "us_per_call": ((self.to_ms(a["units"]) or 0.0) * 1000.0 / a["calls"]) if a["calls"] else None,
            })
        rows.sort(key=lambda r: r["ms_per_frame"], reverse=True)
        return rows[:top] if top else rows

    def slow_frames(self, min_ms: float = 0.0, exclude_after_log: bool = True) -> list:
        """The ``frm`` rows worth reading.

        Frames flagged ``after_log`` paid for the previous frame's log write, so
        they are excluded by default - they are an artefact of the instrument
        and counting them would be the instrument measuring itself.
        """
        out = [f for f in self.frames if f.ms >= min_ms]
        if exclude_after_log:
            out = [f for f in out if not f.after_log]
        return out

    def frame_rows(self, min_ms: float = 0.0, exclude_after_log: bool = True) -> list:
        """Slow frames with the script share worked out, worst frame first."""
        rows = []
        for f in self.slow_frames(min_ms, exclude_after_log):
            script_ms = self.to_ms(f.u_top)
            rows.append({
                "n": f.n,
                "frame": f.frame,
                "t": f.t,
                "ms": f.ms,
                "dt_dev": f.dt_dev,
                "script_ms": script_ms,
                "script_pct": (100.0 * script_ms / f.ms) if (script_ms is not None and f.ms) else None,
                "invisible_ms": (f.ms - script_ms) if script_ms is not None else None,
                "cb_ms": self.to_ms(f.u_cb),
                "bnd_ms": self.to_ms(f.u_bnd),
                "eng_ms": self.to_ms(f.u_eng),
                "evt_ms": self.to_ms(f.u_evt),
                "spawn": f.spawn,
                "destroy": f.destroy,
                "gc_delta_kb": f.gc_delta_kb,
                "top": [(n, self.to_ms(u)) for n, u in f.top],
            })
        rows.sort(key=lambda r: r["ms"], reverse=True)
        return rows

    def frame_summary(self, min_ms: float = 0.0) -> dict:
        """n slow frames, median script share, and the scopes on top of them."""
        rows = self.frame_rows(min_ms)
        if not rows:
            return {"n": 0, "median_script_pct": None, "top_scopes": [],
                    "suppressed": None}
        shares = sorted(r["script_pct"] for r in rows if r["script_pct"] is not None)
        agg: dict = {}
        for r in rows:
            for name, ms in r["top"]:
                if name in ("-", "") or ms is None:
                    continue
                a = agg.setdefault(name, {"name": name, "n": 0, "ms": 0.0, "max_ms": 0.0})
                a["n"] += 1
                a["ms"] += ms
                a["max_ms"] = max(a["max_ms"], ms)
        top = sorted(agg.values(), key=lambda a: a["ms"], reverse=True)
        return {
            "n": len(rows),
            "worst_ms": rows[0]["ms"],
            "median_ms": statistics.median([r["ms"] for r in rows]),
            "median_script_pct": statistics.median(shares) if shares else None,
            "median_invisible_ms": statistics.median(
                [r["invisible_ms"] for r in rows if r["invisible_ms"] is not None]) or None,
            "spawns": sum(r["spawn"] for r in rows),
            "destroys": sum(r["destroy"] for r in rows),
            "gc_frames": sum(1 for r in rows if r["gc_delta_kb"] < -64),
            "top_scopes": top[:10],
        }

    def trace_rows(self, name_prefix: str | None = None) -> list:
        """I-063 ``trace`` lines as a table, in call order."""
        rows = []
        for tr in self.traces:
            if name_prefix and not tr.name.startswith(name_prefix):
                continue
            rows.append({
                "n": tr.n,
                "name": tr.name,
                "ms": self.to_ms(tr.units),
                "frame": tr.frame,
                "t": tr.t,
                "pre": tr.pre_kv,
                "post": tr.post_kv,
                "grew": {k: tr.grew(k) for k in ("cells", "grid")
                         if tr.grew(k) is not None},
            })
        return rows

    def overhead_ms_per_frame(self, drop_first: int = 1) -> float | None:
        """What the instrument itself costs per frame, from its own price tag."""
        if not self.header or self.header.overhead_ns is None:
            return None
        ws = self.good_windows(drop_first)
        frames = sum(w.frames for w in ws)
        if not frames:
            return None
        calls = sum(w.calls + w.nested for w in ws)
        return self.header.overhead_ns * calls / frames / 1e6

    def summary(self, drop_first: int = 1) -> dict:
        per_frame = self.window_ms_per_frame(drop_first)
        return {
            "path": str(self.path) if self.path else None,
            "timer": self.header.timer if self.header else None,
            "units_per_ms": self.units_per_ms,
            "usable": bool(self.header and self.header.usable),
            "windows_total": len(self.windows),
            "windows_used": len(self.good_windows(drop_first)),
            "frames": sum(w.frames for w in self.good_windows(drop_first)),
            "script_ms_per_frame": spread(per_frame),
            "fps_from_windows": spread(self.fps_from_windows(drop_first)),
            "overhead_ms_per_frame": self.overhead_ms_per_frame(drop_first),
            "walkout": self.walkout.walkout if self.walkout else None,
            "slow_frames": len(self.frames),
            "trace_calls": len(self.traces),
            "errors": self.errors[:10],
        }


def parse(text: str, path=None) -> ProfileLog:
    """Parse every ALAOPROF line in *text*; ignore everything else."""
    log = ProfileLog(path=Path(path) if path else None)
    by_seq: dict = {}
    for raw in text.splitlines():
        m = _LINE_RE.search(raw)
        if not m:
            continue
        kind, rest = m.group("kind"), m.group("rest")
        if kind == "err":
            log.errors.append(rest.strip())
            continue
        d = _kv(rest)
        if kind == "hdr":
            log.header = Header(
                timer=d.get("timer", ""),
                units_per_ms=_num(d, "units_per_ms"),
                overhead_ns=_num(d, "overhead_ns"),
                calib_ms=_num(d, "calib_ms"),
                calib_units=_num(d, "calib_units"),
                make_callback=d.get("make_callback") == "true",
                binders=d.get("binders", "off"),
                listeners=d.get("listeners", "off"),
                hitch=d.get("hitch", "off"),
                hitch_floor_ms=_num(d, "hitch_floor_ms"),
                hitch_buckets=_num(d, "hitch_buckets"),
                dump_ms=_num(d, "dump_ms"),
                ts=_num(d, "ts"),
                raw=raw.strip(),
            )
            continue
        if kind == "wdr":               # I-062, the second header line
            log.walkout = WalkoutHeader(
                walkout=d.get("walkout", "off"),
                binder_update=d.get("binder_update") == "true",
                frames=d.get("frames", "off"),
                frame_floor_ms=_num(d, "frame_floor_ms"),
                frame_max=_num(d, "frame_max"),
                rescan_every=_num(d, "rescan_every"),
                pending=int(_num(d, "pending", 0) or 0),
                misses=d.get("misses", ""),
                ts=_num(d, "ts"),
                raw=raw.strip(),
            )
            continue
        if kind == "frm":               # I-062, one slow frame
            top = []
            for part in (d.get("top") or "").split(","):
                if "~" in part:
                    label, u = part.rsplit("~", 1)
                    try:
                        top.append((label, float(u)))
                    except ValueError:
                        continue
            log.frames.append(SlowFrame(
                n=int(_num(d, "n", 0) or 0),
                frame=int(_num(d, "frame", 0) or 0),
                t=float(_num(d, "t", 0.0) or 0.0),
                ms=float(_num(d, "ms", 0.0) or 0.0),
                dt_dev=_num(d, "dt_dev"),
                u_top=float(_num(d, "u_top", 0.0) or 0.0),
                n_top=int(_num(d, "n_top", 0) or 0),
                u_cb=float(_num(d, "u_cb", 0.0) or 0.0),
                u_bnd=float(_num(d, "u_bnd", 0.0) or 0.0),
                u_eng=float(_num(d, "u_eng", 0.0) or 0.0),
                u_evt=float(_num(d, "u_evt", 0.0) or 0.0),
                spawn=int(_num(d, "spawn", 0) or 0),
                destroy=int(_num(d, "destroy", 0) or 0),
                gc0=float(_num(d, "gc0", 0.0) or 0.0),
                gc1=float(_num(d, "gc1", 0.0) or 0.0),
                after_log=int(_num(d, "after_log", 0) or 0),
                top=top,
            ))
            continue
        if kind == "trace":             # I-063, one call of a named listener
            log.traces.append(TraceCall(
                n=int(_num(d, "n", 0) or 0),
                name=d.get("name", "?"),
                units=float(_num(d, "units", 0.0) or 0.0),
                frame=int(_num(d, "frame", 0) or 0),
                t=float(_num(d, "t", 0.0) or 0.0),
                pre=d.get("pre", ""),
                post=d.get("post", ""),
            ))
            continue
        seq = _num(d, "seq")
        if seq is None:
            continue
        w = by_seq.get(seq)
        if w is None:
            w = Window(seq=int(seq))
            by_seq[seq] = w
            log.windows.append(w)
        if kind == "win":
            w.t0, w.t1 = _num(d, "t0"), _num(d, "t1")
            w.span_ms = _num(d, "span_ms")
            w.frames = int(_num(d, "frames", 0) or 0)
            w.total_units = float(_num(d, "total_units", 0.0) or 0.0)
            w.calls = int(_num(d, "calls", 0) or 0)
            w.nested = int(_num(d, "nested", 0) or 0)
            w.names = int(_num(d, "names", 0) or 0)
        elif kind == "cb":
            name = d.get("name", "?")
            w.entries[name] = CallbackWindow(
                name=name,
                calls=int(_num(d, "calls", 0) or 0),
                units=float(_num(d, "units", 0.0) or 0.0),
                nested=int(_num(d, "nested", 0) or 0),
            )
        elif kind == "lst":
            name = d.get("name", "?")
            w.listeners[name] = CallbackWindow(
                name=name,
                calls=int(_num(d, "calls", 0) or 0),
                units=float(_num(d, "units", 0.0) or 0.0),
            )
        elif kind == "axs":             # I-062, one extra-axis row
            name = d.get("name", "?")
            w.axes.setdefault(d.get("axis", "?"), {})[name] = CallbackWindow(
                name=name,
                calls=int(_num(d, "calls", 0) or 0),
                units=float(_num(d, "units", 0.0) or 0.0),
                nested=int(_num(d, "nested", 0) or 0),
            )
        elif kind == "hit":
            name = d.get("name", "?")
            scope = d.get("scope", "cb")
            try:
                buckets = [int(x) for x in (d.get("b") or "").split(",") if x != ""]
            except ValueError:
                buckets = []
            log.hitches[(scope, name)] = HitchStat(
                name=name,
                scope=scope,
                max_units=float(_num(d, "max", 0.0) or 0.0),
                above=int(_num(d, "above", 0) or 0),
                first_t=float(_num(d, "first_t", 0.0) or 0.0),
                first_frame=int(_num(d, "first_frame", 0) or 0),
                first_units=float(_num(d, "first_units", 0.0) or 0.0),
                floor_units=float(_num(d, "floor", 0.0) or 0.0),
                buckets=buckets,
            )
        elif kind == "eow":
            w.complete = True
    log.windows.sort(key=lambda w: w.seq)
    return log


def load(path) -> ProfileLog:
    """Parse an engine log, tolerating the encodings the engine mixes."""
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


def load_run(run_dir) -> ProfileLog | None:
    """Parse ``<run_dir>/xray.log``, the copy :mod:`aalo.runner` already made."""
    p = Path(run_dir) / "xray.log"
    return load(p) if p.is_file() else None


def spread(values) -> dict:
    """mean / stdev / coefficient of variation for a list of measurements.

    ``cv_pct`` is the number I-048 is judged on: the run-to-run spread of total
    script ms per frame, target < 5%.
    """
    vals = [float(v) for v in values if isinstance(v, (int, float))]
    if not vals:
        return {"n": 0, "mean": None, "stdev": None, "cv_pct": None, "min": None, "max": None}
    mean = statistics.fmean(vals)
    sd = statistics.stdev(vals) if len(vals) > 1 else 0.0
    return {
        "n": len(vals),
        "mean": mean,
        "stdev": sd,
        "cv_pct": (100.0 * sd / mean) if mean else None,
        "min": min(vals),
        "max": max(vals),
        "range_pct": (100.0 * (max(vals) - min(vals)) / mean) if mean else None,
    }


def compare_runs(run_dirs, drop_first: int = 1, drop_rounds: int = 0) -> dict:
    """Aggregate several runs of the SAME arm into one run-to-run spread report.

    Each run contributes one number - its mean script ms/frame - so ``spread``
    here is the between-run spread, which is what decides whether script-ms is
    an instrument.  ``within_run`` keeps the per-window spread for contrast.

    *drop_rounds* skips that many runs from the start (run dirs sort
    chronologically by name).  The A/A run
    ``20260919-184343-I-048-757367`` found the first round of each arm sitting
    ~10% high in script-ms while its fps was unremarkable - a script-side
    session warm-up the 30 s in-level warm-up does not cover.  Dropping it takes
    the run-to-run cv from 5.48% to 1.64%.
    """
    run_dirs = sorted(run_dirs, key=lambda d: Path(d).name)[drop_rounds:]
    per_run, within, logs = [], [], []
    for d in run_dirs:
        log = load_run(d)
        if log is None or not log.windows:
            continue
        vals = log.window_ms_per_frame(drop_first)
        if not vals:
            continue
        logs.append((str(d), log))
        per_run.append(statistics.fmean(vals))
        within.append(spread(vals))
    merged: dict = {}
    frames = 0
    for _, log in logs:
        frames += sum(w.frames for w in log.good_windows(drop_first))
        for row in log.ranking(top=None, drop_first=drop_first):
            m = merged.setdefault(row["name"], {"name": row["name"], "ms_per_frame": [], "calls_per_frame": []})
            m["ms_per_frame"].append(row["ms_per_frame"])
            m["calls_per_frame"].append(row["calls_per_frame"])
    ranking = []
    for m in merged.values():
        ranking.append({
            "name": m["name"],
            "ms_per_frame": statistics.fmean(m["ms_per_frame"]),
            "calls_per_frame": statistics.fmean(m["calls_per_frame"]),
            "cv_pct": spread(m["ms_per_frame"])["cv_pct"],
            "runs": len(m["ms_per_frame"]),
        })
    ranking.sort(key=lambda r: r["ms_per_frame"], reverse=True)
    return {
        "runs": [d for d, _ in logs],
        "n_runs": len(logs),
        "frames": frames,
        "script_ms_per_frame": spread(per_run),
        "per_run_means": per_run,
        "within_run": within,
        "ranking": ranking,
    }
