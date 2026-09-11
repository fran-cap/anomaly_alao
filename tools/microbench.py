#!/usr/bin/env python3
"""
microbench.py - LuaJIT 2.0 paired-snippet benchmark harness for ALAO (idea I-003).

Why this exists: the corpus harness proves ALAO's rewrites *land* and *compile*.
Nothing proved they were *faster*. The first hand-rolled attempt at that skipped
GC control and declared ALAO's highest-volume fix a 23% regression, which was
wrong and survived for a while. So the protocol from beam-ideas.md section 2 is
baked in here and is NOT optional - there are no flags to turn any of it off.

The protocol, enforced:

  * VM              lupa.luajit20 (LuaJIT 2.0, the exact target VM). A fresh
                    LuaRuntime per (case, arm, mode) - "fresh per mode" is the
                    documented minimum, per-arm is strictly cleaner and free.
  * Modes           JIT on (default) and JIT off. JIT off is done with
                    jit.off(f, true) on the loaded chunk itself. A global
                    jit.off(true, true) does NOT affect already-loaded chunks
                    and silently measures JIT-on numbers - see --self-check,
                    which proves the two modes really differ before any case runs.
  * Chunk           loadstring("local N, D = ...\n" + setup + arm +
                    "\n_G.__sink = " + sink). D is a 64-element float table so
                    the JIT cannot constant-fold the body; __sink defeats DCE.
                    Without both, several cases optimize to nothing.
  * Warm-up         two calls at N=1000 before timing, so the trace is recorded.
  * Timing          time.perf_counter() around one f(N, D) call on the Python
                    side. os.clock() inside Lua has ~10 ms resolution on Windows.
  * GC              collectgarbage('collect') immediately before every timed run.
  * Reps            best of 9; the median is recorded too.
  * N               2e6 JIT on, 3e5 JIT off, unless the snippet overrides it.
  * VM fingerprint  jit.version / version_num / status() flags are recorded in
                    the JSON and warned about loudly if the optimization flag set
                    is not the one Anomaly's LuaJIT 2.0.4 build reports.

Loop-length sweeps: a snippet with `-- @iters 5 20 100 2000` is run once per
inner iteration count K, with the outer repetition count scaled so total work
stays roughly constant. Several rewrites flip sign with loop length - the
table.concat rewrite is 0.57x at 3 iterations and 8x at thousands - so a single
huge-N number is not a G2 verdict. The per-pattern summary then reads
"passes for K >= 100" instead of a bare pass/fail.

A snippet can also declare `-- @corpus_k 3-10:15 68:3` with `-- @corpus_src
<run id>`: where the pattern runs, as buckets of loop-length range to corpus site
count. Every bucket must contain a measured point (the file is rejected at parse
time otherwise), and the G2 verdict then leads by counting sites - "15 of 18
corpus sites fail G2, 3 of 18 pass" - because a transform that wins only at
K=2000 while most real sites are K=3 has been scored on a part of its own curve
the code does not visit. Buckets rather than one range because corpora are
bimodal and a range loses where the mass is. A speedup is a function; a scalar is
a claim that the function is constant, and that claim has to hold where the code
runs.

Usage:

    py -3.12 tools/microbench.py                          # every case in bench/
    py -3.12 tools/microbench.py --pattern table_insert_append math_pow_simple
    py -3.12 tools/microbench.py --shipped                # only patterns ALAO fixes
    py -3.12 tools/microbench.py --json out.json --markdown out.md
    py -3.12 tools/microbench.py --quick                  # tiny N, smoke only
    py -3.12 tools/microbench.py --self-check             # prove JIT off != JIT on

Gate G2 (beam-ideas.md section 1): a rewrite passes if speedup >= 1.15x in BOTH
modes and no mode is below 0.98x. speedup = t(original) / t(rewrite).
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

try:
    import lupa.luajit20 as luajit
except ImportError:  # pragma: no cover - environment problem, not a code path
    sys.stderr.write(
        "microbench needs lupa with the LuaJIT 2.0 backend:\n"
        "    py -3.12 -m pip install lupa\n"
    )
    raise

REPO_ROOT = Path(__file__).resolve().parent.parent
BENCH_DIR = REPO_ROOT / "bench"

# --- the protocol constants. Do not add flags to bypass these. ---------------
WARMUP_CALLS = 2
WARMUP_N = 1000
DEFAULT_REPS = 9
DEFAULT_N_JIT_ON = 2_000_000
DEFAULT_N_JIT_OFF = 300_000
D_SIZE = 64

MODES = ("jit_on", "jit_off")

# G2
G2_MIN_SPEEDUP = 1.15
G2_NO_MODE_BELOW = 0.98

# median/best above this means the machine was busy and the ratio is not trustworthy
JITTER_WARN = 1.25


# ---------------------------------------------------------------------------
# snippet files
# ---------------------------------------------------------------------------

DIRECTIVE_RE = re.compile(r"^\s*--\s*@(\w+)\s*(.*)$")
SECTIONS = ("setup", "original", "rewrite", "sink")
META_KEYS = ("pattern", "title", "status", "n", "doc", "notes", "iters", "doc_at",
             "corpus_k", "corpus_src")

# The VM we must be on. Anomaly ships LuaJIT 2.0.4; lupa.luajit20 is a 2.0-branch
# build (version_num 20099) with the same optimization flag set, which is what
# actually decides what the compiler does. The CPU-feature strings in
# jit.status() vary per machine and are recorded but not asserted on.
EXPECTED_JIT_OPT_FLAGS = ["fold", "cse", "dce", "fwd", "dse", "narrow",
                          "loop", "abc", "sink", "fuse"]
KNOWN_OPT_FLAGS = set(EXPECTED_JIT_OPT_FLAGS)
EXPECTED_JIT_MAJOR_MINOR = (2, 0)


@dataclass
class CorpusBucket:
    """`3-10:15` - a loop-length range and how many corpus sites sit in it."""

    lo: int
    hi: int
    sites: Optional[int] = None

    def contains(self, k: Optional[int]) -> bool:
        return k is not None and self.lo <= k <= self.hi

    @property
    def label(self) -> str:
        return f"K={self.lo}" if self.lo == self.hi else f"K={self.lo}-{self.hi}"


def parse_corpus_buckets(spec: str, where: str) -> List[CorpusBucket]:
    """`3-10:15 68:3` -> two buckets. The `:count` is optional but wanted."""
    buckets: List[CorpusBucket] = []
    for token in spec.replace(",", " ").split():
        rng, _, count = token.partition(":")
        lo, _, hi = rng.partition("-")
        try:
            lo_i = int(lo)
            hi_i = int(hi) if hi else lo_i
            sites = int(count) if count else None
        except ValueError:
            raise ValueError(
                f"{where}: bad @corpus_k token {token!r}; want <K>[-<K>][:<site count>]"
            ) from None
        if lo_i < 1 or hi_i < lo_i or (sites is not None and sites < 0):
            raise ValueError(f"{where}: bad @corpus_k token {token!r}")
        buckets.append(CorpusBucket(lo_i, hi_i, sites))
    if not buckets:
        raise ValueError(f"{where}: @corpus_k is empty")
    return buckets


@dataclass
class BenchCase:
    """One paired snippet: the same work written two ways."""

    pattern: str
    title: str
    status: str                  # 'shipped' | 'proposed' | 'retarget'
    path: Path
    setup: str
    original: str
    rewrite: str
    sink: str
    n_jit_on: int = DEFAULT_N_JIT_ON
    n_jit_off: int = DEFAULT_N_JIT_OFF
    doc: Dict[str, Optional[float]] = field(default_factory=dict)
    notes: str = ""
    iters: List[int] = field(default_factory=list)   # inner loop lengths to sweep
    doc_at: Optional[int] = None                     # which K the @doc figure refers to
    # Where this pattern actually runs, as buckets of (lo, hi, site_count). A
    # speedup is a function of K; a scalar is a claim the function is constant.
    # This is where the claim has to hold, and it is usually not where it is
    # easiest to measure a big number.
    #
    # Buckets rather than one lo-hi range because real corpora are bimodal: the
    # string_concat sites are 15 short UI builders at K=3-10 plus an isolated
    # spike of 3 literal `for i=1,68` loops, nothing in between. A range flattens
    # that to "3-68" and loses the fact that the mass is at the bottom, which is
    # the thing that actually decides a prune.
    corpus_k: Optional[List["CorpusBucket"]] = None
    corpus_src: str = ""             # run id / audit this breakdown came from

    def n_for(self, mode: str) -> int:
        """Total inner-iteration budget for this mode."""
        return self.n_jit_on if mode == "jit_on" else self.n_jit_off

    def outer_for(self, mode: str, k: Optional[int]) -> int:
        """Outer repetitions, so total work stays ~constant across the K sweep."""
        if not k:
            return self.n_for(mode)
        return max(1, self.n_for(mode) // k)

    @property
    def points(self) -> List[Optional[int]]:
        return list(self.iters) if self.iters else [None]

    def chunk(self, arm: str) -> str:
        """The chunk source: prelude, untimed @setup, then the timed body.

        One documented deviation from section 2: @setup runs *outside* the timed
        region, by returning the measured work as a closure. Section 2 timed the
        whole chunk, which is fine when setup is two locals and lethal when it is
        "build an N-element table" - the setup then dominates and squashes every
        ratio toward 1.00x. The closure is a sub-function of the chunk prototype,
        so jit.off(chunk, true) still covers it recursively (--self-check proves
        that empirically before any case runs). Everything else - the prelude,
        D, the __sink, the warm-up, the GC, best-of-9, N - is section 2 verbatim.
        """
        body = self.original if arm == "original" else self.rewrite
        return (
            "local N, D, K = ...\n"
            + self.setup.rstrip()
            + "\nreturn function()\n"
            + body.rstrip()
            + "\n_G.__sink = "
            + self.sink.strip()
            + "\nend\n"
        )


def parse_bench_file(path: Path) -> BenchCase:
    """Parse one bench/*.lua snippet. Format is documented in tools/README.md."""
    text = path.read_text(encoding="utf-8")
    meta: Dict[str, str] = {}
    sections: Dict[str, List[str]] = {}
    current: Optional[str] = None

    for lineno, line in enumerate(text.splitlines(), 1):
        m = DIRECTIVE_RE.match(line)
        if m:
            name, rest = m.group(1), m.group(2).strip()
            if name in SECTIONS:
                current = name
                sections.setdefault(name, [])
                if rest:
                    sections[name].append(rest)
                continue
            if name not in META_KEYS:
                raise ValueError(f"{path.name}:{lineno}: unknown directive @{name}")
            if name == "notes" and meta.get("notes"):
                meta["notes"] += " " + rest      # several @notes lines accumulate
            else:
                meta[name] = rest
            continue
        if current is not None:
            sections[current].append(line)

    missing = [s for s in ("original", "rewrite", "sink") if s not in sections]
    if missing:
        raise ValueError(
            f"{path.name}: missing section(s) {', '.join('@' + m for m in missing)}"
        )
    for key in ("pattern", "title"):
        if not meta.get(key):
            raise ValueError(f"{path.name}: missing @{key}")

    n_on, n_off = DEFAULT_N_JIT_ON, DEFAULT_N_JIT_OFF
    if meta.get("n"):
        parts = meta["n"].split()
        if len(parts) != 2:
            raise ValueError(f"{path.name}: @n takes two integers: <jit_on> <jit_off>")
        n_on, n_off = int(float(parts[0])), int(float(parts[1]))

    doc: Dict[str, Optional[float]] = {"jit_on": None, "jit_off": None}
    if meta.get("doc"):
        parts = meta["doc"].split()
        if len(parts) != 2:
            raise ValueError(f"{path.name}: @doc takes two numbers (or '-'): <jit_on> <jit_off>")
        doc["jit_on"] = None if parts[0] == "-" else float(parts[0])
        doc["jit_off"] = None if parts[1] == "-" else float(parts[1])

    iters = [int(x) for x in meta["iters"].replace(",", " ").split()] if meta.get("iters") else []
    if any(k <= 0 for k in iters):
        raise ValueError(f"{path.name}: @iters must be positive integers")
    doc_at = int(meta["doc_at"]) if meta.get("doc_at") else (iters[-1] if iters else None)
    if iters and doc_at not in iters:
        raise ValueError(f"{path.name}: @doc_at {doc_at} is not one of @iters {iters}")

    corpus_k = None
    corpus_src = meta.get("corpus_src", "")
    if meta.get("corpus_k"):
        corpus_k = parse_corpus_buckets(meta["corpus_k"], path.name)
        if not corpus_src:
            # Otherwise this is a hand-entered number carrying the same trust
            # problem as the scalar it replaces, and the rule in tools/README.md
            # applies to it too (agent-I039).
            raise ValueError(
                f"{path.name}: @corpus_k needs @corpus_src naming the run id or "
                "audit the site counts came from."
            )
        if iters:
            # The whole point of declaring where a pattern runs is that you
            # measured there. Every bucket must contain a measured point.
            blind = [b.label for b in corpus_k if not any(b.contains(k) for k in iters)]
            if blind:
                raise ValueError(
                    f"{path.name}: @corpus_k bucket(s) {', '.join(blind)} contain none "
                    f"of @iters {iters}. The sweep never measures loop lengths this "
                    "pattern actually has, so its G2 number would describe a region "
                    "where the transform never runs. Add a K inside each bucket."
                )

    return BenchCase(
        pattern=meta["pattern"],
        title=meta["title"],
        status=meta.get("status", "proposed"),
        path=path,
        setup="\n".join(sections.get("setup", [])),
        original="\n".join(sections["original"]),
        rewrite="\n".join(sections["rewrite"]),
        sink="\n".join(sections["sink"]),
        n_jit_on=n_on,
        n_jit_off=n_off,
        doc=doc,
        notes=meta.get("notes", ""),
        iters=iters,
        doc_at=doc_at,
        corpus_k=corpus_k,
        corpus_src=corpus_src,
    )


def load_cases(bench_dir: Path = BENCH_DIR) -> List[BenchCase]:
    cases = [parse_bench_file(p) for p in sorted(bench_dir.glob("*.lua"))]
    seen: Dict[str, Path] = {}
    for c in cases:
        if c.pattern in seen:
            raise ValueError(
                f"duplicate @pattern {c.pattern}: {seen[c.pattern].name} and {c.path.name}"
            )
        seen[c.pattern] = c.path
    return cases


# ---------------------------------------------------------------------------
# the timing core
# ---------------------------------------------------------------------------

def _new_runtime():
    """One fresh VM. Nothing is shared between arms, modes or cases."""
    return luajit.LuaRuntime(unpack_returned_tuples=False)


def _make_d(rt) -> Any:
    """The 64-element float table that stops the JIT constant-folding a body."""
    build = rt.eval(
        "function(n)\n"
        "  local d = {}\n"
        "  for i = 1, n do d[i] = (i * 0.6180339887498949) % 1.0 + 0.5 end\n"
        "  return d\n"
        "end"
    )
    return build(D_SIZE)


def vm_fingerprint() -> Dict[str, Any]:
    """Which VM are these numbers from, and is it the one Anomaly runs?"""
    rt = _new_runtime()
    status = list(rt.eval("{jit.status()}").values())
    enabled = bool(status[0])
    flags = [s for s in status[1:] if isinstance(s, str)]
    opt_flags = [f for f in flags if f in KNOWN_OPT_FLAGS]
    cpu_flags = [f for f in flags if f not in KNOWN_OPT_FLAGS]
    version_num = int(rt.eval("jit.version_num"))
    major, minor = version_num // 10000, (version_num // 100) % 100
    warnings: List[str] = []
    if opt_flags != EXPECTED_JIT_OPT_FLAGS:
        warnings.append(
            "jit.status() optimization flags differ from the Anomaly build: "
            f"got {opt_flags}, expected {EXPECTED_JIT_OPT_FLAGS}. "
            "Every number below is from a differently-optimizing compiler."
        )
    if (major, minor) != EXPECTED_JIT_MAJOR_MINOR:
        warnings.append(
            f"LuaJIT {major}.{minor} is not the 2.0 branch Anomaly ships "
            f"(jit.version_num={version_num}). Import lupa.luajit20, not lupa."
        )
    return {
        "lua_implementation": rt.lua_implementation,
        "jit_version": rt.eval("jit.version"),
        "jit_version_num": version_num,
        "jit_arch": rt.eval("jit.arch"),
        "jit_os": rt.eval("jit.os"),
        "jit_enabled": enabled,
        "jit_status_flags": flags,
        "jit_opt_flags": opt_flags,
        "jit_cpu_flags": cpu_flags,
        "expected_opt_flags": EXPECTED_JIT_OPT_FLAGS,
        "opt_flags_match": opt_flags == EXPECTED_JIT_OPT_FLAGS,
        "warnings": warnings,
    }


@dataclass
class ArmResult:
    best: float
    median: float
    runs: List[float]
    n: int
    k: Optional[int]
    jit_status: bool


def time_arm(case: BenchCase, arm: str, mode: str, reps: int,
             n: Optional[int] = None, k: Optional[int] = None) -> ArmResult:
    rt = _new_runtime()
    d = _make_d(rt)
    n = n if n is not None else case.outer_for(mode, k)

    src = case.chunk(arm)
    loadstring = rt.eval("loadstring")
    loaded = loadstring(src, f"{case.pattern}:{arm}")
    if isinstance(loaded, tuple):
        f, err = loaded[0], loaded[1] if len(loaded) > 1 else None
    else:
        f, err = loaded, None
    if f is None:
        raise RuntimeError(
            f"{case.pattern}/{arm}: LuaJIT refused the chunk: {err}\n--- chunk ---\n{src}"
        )

    if mode == "jit_off":
        # jit.off(f, true) - on the CHUNK. jit.off(true, true) globally does not
        # touch chunks that are already loaded, which is how you accidentally
        # publish JIT-on numbers labelled "interpreted".
        rt.eval("jit.off")(f, True)

    status = bool(rt.eval("jit.status")())
    collect = rt.eval("function() collectgarbage('collect') end")

    warm_n = max(1, WARMUP_N // k) if k else WARMUP_N
    for _ in range(WARMUP_CALLS):
        f(warm_n, d, k)()

    runs: List[float] = []
    for _ in range(reps):
        # setup runs here, untimed; every rep gets fresh state, so an append
        # benchmark never times a table left over from the previous rep.
        body = f(n, d, k)
        collect()
        t0 = time.perf_counter()
        body()
        t1 = time.perf_counter()
        runs.append(t1 - t0)

    return ArmResult(best=min(runs), median=statistics.median(runs), runs=runs,
                     n=n, k=k, jit_status=status)


@dataclass
class ModeResult:
    mode: str
    original: ArmResult
    rewrite: ArmResult

    @property
    def speedup(self) -> float:
        return self.original.best / self.rewrite.best if self.rewrite.best else float("inf")

    @property
    def speedup_median(self) -> float:
        return self.original.median / self.rewrite.median if self.rewrite.median else float("inf")

    @property
    def jitter(self) -> float:
        """max(median/best) over both arms. 1.00 is a quiet machine.

        Best-of-9 hides a loaded CPU rather than fixing it: a row that is really
        1.00x can print 1.4x if the *other* arm happened to get preempted. This
        is the number that says "do not trust that ratio, run it again".
        """
        return max(a.median / a.best if a.best else 1.0
                   for a in (self.original, self.rewrite))


@dataclass
class CaseResult:
    """One (case, inner-iteration-count) point. k is None for non-sweep cases."""

    case: BenchCase
    modes: Dict[str, ModeResult]
    k: Optional[int] = None
    error: Optional[str] = None

    @property
    def label(self) -> str:
        return f"{self.case.pattern}@K={self.k}" if self.k else self.case.pattern

    def speedup(self, mode: str) -> Optional[float]:
        mr = self.modes.get(mode)
        return mr.speedup if mr else None

    @property
    def g2(self) -> Optional[bool]:
        ups = [self.speedup(m) for m in MODES]
        if any(u is None for u in ups):
            return None
        return all(u >= G2_MIN_SPEEDUP for u in ups) and all(u >= G2_NO_MODE_BELOW for u in ups)

    def doc_delta(self, mode: str) -> Optional[float]:
        """Relative difference vs the beam-ideas section-2 figure, as a fraction.

        On a K sweep only the row the doc figure refers to (@doc_at, default the
        largest K) is compared - the doc has one number per transform, not a curve.
        """
        if self.k is not None and self.k != self.case.doc_at:
            return None
        want = self.case.doc.get(mode)
        got = self.speedup(mode)
        if want is None or got is None or want == 0:
            return None
        return (got - want) / want


def run_case(case: BenchCase, reps: int, modes: Sequence[str],
             n_override: Optional[int]) -> List[CaseResult]:
    results: List[CaseResult] = []
    for k in case.points:
        out: Dict[str, ModeResult] = {}
        try:
            for mode in modes:
                # n_override is a total inner-iteration budget, like @n, so a
                # sweep case does not multiply it by K and run for an hour.
                if n_override is None:
                    n = case.outer_for(mode, k)
                elif k:
                    n = max(1, n_override // k)
                else:
                    n = n_override
                orig = time_arm(case, "original", mode, reps, n, k)
                rewr = time_arm(case, "rewrite", mode, reps, n, k)
                out[mode] = ModeResult(mode=mode, original=orig, rewrite=rewr)
        except Exception as exc:  # a broken snippet must not kill the whole sweep
            results.append(CaseResult(case=case, modes=out, k=k,
                                      error=f"{type(exc).__name__}: {exc}"))
            continue
        results.append(CaseResult(case=case, modes=out, k=k))
    return results


def corpus_verdict(case: BenchCase, flags: Sequence[tuple]) -> Optional[str]:
    """G2 read out over the corpus's own distribution of loop lengths.

    `flags` is [(k, passed_g2), ...] from the sweep. Returns the sentence a
    prune decision can actually be made on - "15 of 18 sites fail" - rather
    than a range, or None if nothing was measured in any declared bucket.
    """
    parts: List[str] = []
    pass_sites = fail_sites = 0
    counted = True
    any_measured = False

    for b in case.corpus_k or []:
        inside = [(k, ok) for k, ok in flags if b.contains(k)]
        if not inside:
            continue
        any_measured = True
        ok_all = all(ok for _, ok in inside)
        ok_any = any(ok for _, ok in inside)
        state = "pass" if ok_all else "fail" if not ok_any else "mixed"
        if b.sites is None:
            counted = False
            parts.append(f"{b.label}: {state}")
        else:
            parts.append(f"{b.sites} at {b.label}: {state}")
            if ok_all:
                pass_sites += b.sites
            elif not ok_any:
                fail_sites += b.sites

    if not any_measured:
        return None

    src = f" [{case.corpus_src}]" if case.corpus_src else ""
    if counted:
        total = sum(b.sites or 0 for b in case.corpus_k or [])
        head = (f"{fail_sites} of {total} corpus sites fail G2" if fail_sites
                else f"all {total} corpus sites pass G2")
        if fail_sites and pass_sites:
            head += f", {pass_sites} of {total} pass"
        return f"{head}{src} ({'; '.join(parts)})"
    return f"at corpus sites{src}: {'; '.join(parts)}"


def g2_summary(results: Sequence[CaseResult]) -> Dict[str, str]:
    """Per pattern: the G2 verdict, qualified by inner loop length where it swings."""
    by_pattern: Dict[str, List[CaseResult]] = {}
    for r in results:
        by_pattern.setdefault(r.case.pattern, []).append(r)
    out: Dict[str, str] = {}
    for pattern, rows in by_pattern.items():
        rows = [r for r in rows if not r.error]
        if not rows:
            out[pattern] = "error"
            continue
        if len(rows) == 1 and rows[0].k is None:
            out[pattern] = "pass" if rows[0].g2 else "fail"
            continue
        rows.sort(key=lambda r: r.k or 0)
        flags = [(r.k, bool(r.g2)) for r in rows]

        # If the snippet declared where this pattern actually runs, that verdict
        # leads, and it counts SITES rather than bounding a range. A transform
        # scored on a region it never reaches is the failure I-039 hit; a range
        # alone would have flattened their bimodal corpus (15 sites at K=3-10,
        # 3 at K=68) into "3-68" and lost the fact that the mass is at the bottom,
        # which is the thing that decides a prune.
        case = rows[0].case
        if case.corpus_k:
            verdict = corpus_verdict(case, flags)
            if verdict:
                out[pattern] = verdict
                continue
        if all(f for _, f in flags):
            out[pattern] = "pass at every K measured"
        elif not any(f for _, f in flags):
            out[pattern] = "fail at every K measured"
        else:
            # find the smallest K from which it passes and never fails again
            threshold = None
            for i, (k, ok) in enumerate(flags):
                if ok and all(f for _, f in flags[i:]):
                    threshold = k
                    break
            if threshold is not None:
                losing = [k for k, ok in flags if not ok]
                out[pattern] = (f"passes for K >= {threshold}; "
                                f"fails at K in {losing}")
            else:
                out[pattern] = ("mixed: " +
                                ", ".join(f"K={k}:{'pass' if ok else 'fail'}" for k, ok in flags))
    return out


# ---------------------------------------------------------------------------
# self-check: prove the two modes are actually different
# ---------------------------------------------------------------------------

SELF_CHECK = BenchCase(
    pattern="__self_check",
    title="jit-off sanity: a trivially jittable arithmetic loop",
    status="internal",
    path=Path("<internal>"),
    setup="local s = 0.0",
    original="for i = 1, N do s = s + D[i % 64 + 1] * 1.0000001 end",
    rewrite="for i = 1, N do s = s + D[i % 64 + 1] * 1.0000001 end",
    sink="s",
    n_jit_on=2_000_000,
    n_jit_off=2_000_000,
)


def self_check(reps: int = 5, min_ratio: float = 3.0) -> Dict[str, Any]:
    """jit.off(f, true) must make a jittable loop dramatically slower.

    If it does not, the JIT-off column is a lie and every number under it is a
    duplicate of the JIT-on column. That is exactly the failure mode the doc
    warns about, so it is checked before any case runs.
    """
    on = time_arm(SELF_CHECK, "original", "jit_on", reps)
    off = time_arm(SELF_CHECK, "original", "jit_off", reps)
    ratio = off.best / on.best if on.best else float("inf")
    return {
        "jit_on_best_s": on.best,
        "jit_off_best_s": off.best,
        "ratio_off_over_on": ratio,
        "min_ratio": min_ratio,
        "ok": ratio >= min_ratio,
        "global_jit_status_seen": [on.jit_status, off.jit_status],
    }


# ---------------------------------------------------------------------------
# output
# ---------------------------------------------------------------------------

def _fmt_speedup(v: Optional[float]) -> str:
    if v is None:
        return "-"
    if v == float("inf"):
        return "inf"
    return f"{v:.2f}x"


def _fmt_delta(v: Optional[float]) -> str:
    if v is None:
        return "-"
    return f"{v * 100:+.0f}%"


def markdown_table(results: List[CaseResult], show_doc: bool = True) -> str:
    head = ["Pattern", "Transform", "Status", "JIT on", "JIT off", "G2"]
    if show_doc:
        head = ["Pattern", "Transform", "Status",
                "JIT on", "doc on", "d on",
                "JIT off", "doc off", "d off", "G2"]
    lines = ["| " + " | ".join(head) + " |",
             "|" + "|".join(["---"] * len(head)) + "|"]
    for r in results:
        if r.error:
            row = [r.label, r.case.title, r.case.status] + ["ERR"] * (len(head) - 3)
            lines.append("| " + " | ".join(row) + " |")
            continue
        g2 = r.g2
        g2s = "-" if g2 is None else ("PASS" if g2 else "fail")
        row = [r.label, r.case.title, r.case.status]
        if show_doc:
            row += [
                _fmt_speedup(r.speedup("jit_on")),
                _fmt_speedup(r.case.doc.get("jit_on")),
                _fmt_delta(r.doc_delta("jit_on")),
                _fmt_speedup(r.speedup("jit_off")),
                _fmt_speedup(r.case.doc.get("jit_off")),
                _fmt_delta(r.doc_delta("jit_off")),
                g2s,
            ]
        else:
            row += [_fmt_speedup(r.speedup("jit_on")),
                    _fmt_speedup(r.speedup("jit_off")), g2s]
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def results_to_json(results: List[CaseResult], meta: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "schema": "alao.microbench/1",
        "meta": meta,
        "g2_summary": g2_summary(results),
        "cases": [],
    }
    for r in results:
        entry: Dict[str, Any] = {
            "pattern": r.case.pattern,
            "label": r.label,
            "k": r.k,
            "title": r.case.title,
            "status": r.case.status,
            "file": r.case.path.name,
            "notes": r.case.notes,
            "doc": r.case.doc,
            "error": r.error,
            "g2_pass": r.g2,
            "modes": {},
        }
        for mode, mr in r.modes.items():
            entry["modes"][mode] = {
                "n_outer": mr.original.n,
                "k": mr.original.k,
                "n": mr.original.n * (mr.original.k or 1),
                "speedup_best": mr.speedup,
                "speedup_median": mr.speedup_median,
                "jitter": mr.jitter,
                "doc_delta": r.doc_delta(mode),
                "original": {"best_s": mr.original.best, "median_s": mr.original.median,
                             "runs_s": mr.original.runs},
                "rewrite": {"best_s": mr.rewrite.best, "median_s": mr.rewrite.median,
                            "runs_s": mr.rewrite.runs},
            }
        out["cases"].append(entry)
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Paired-snippet LuaJIT 2.0 microbenchmarks for ALAO patterns.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="The section-2 protocol (fresh VM, jit.off on the chunk, warm-up, "
               "collectgarbage before every timed run, best of 9) is mandatory and "
               "has no opt-out flag.",
    )
    ap.add_argument("--bench-dir", type=Path, default=BENCH_DIR)
    ap.add_argument("--pattern", nargs="+", metavar="NAME",
                    help="only these @pattern names (substring match allowed)")
    ap.add_argument("--shipped", action="store_true", help="only cases with @status shipped")
    ap.add_argument("--proposed", action="store_true", help="only cases with @status proposed")
    ap.add_argument("--reps", type=int, default=DEFAULT_REPS,
                    help=f"timed runs per arm (default {DEFAULT_REPS})")
    ap.add_argument("--n", type=int, help="override N for every mode (debugging only)")
    ap.add_argument("--mode", choices=MODES, action="append",
                    help="restrict to one mode (debugging only)")
    ap.add_argument("--quick", action="store_true",
                    help="tiny N and 3 reps - a smoke test, NOT a measurement")
    ap.add_argument("--json", type=Path, metavar="PATH", help="write the full result JSON here")
    ap.add_argument("--markdown", type=Path, metavar="PATH", help="write the markdown table here")
    ap.add_argument("--no-doc", action="store_true", help="hide the beam-ideas comparison columns")
    ap.add_argument("--self-check", action="store_true",
                    help="only run the jit-off sanity check and exit")
    ap.add_argument("--list", action="store_true", help="list bench cases and exit")
    args = ap.parse_args(argv)

    if args.self_check:
        sc = self_check()
        print(json.dumps(sc, indent=2))
        return 0 if sc["ok"] else 1

    try:
        cases = load_cases(args.bench_dir)
    except ValueError as exc:
        sys.stderr.write(f"bench snippet error: {exc}\n")
        return 2

    if args.pattern:
        wanted = args.pattern
        cases = [c for c in cases if any(w == c.pattern or w in c.pattern for w in wanted)]
    if args.shipped:
        cases = [c for c in cases if c.status == "shipped"]
    if args.proposed:
        cases = [c for c in cases if c.status == "proposed"]

    if args.list:
        for c in cases:
            iters = f" K={c.iters}" if c.iters else ""
            print(f"{c.pattern:28s} {c.status:9s} {c.title}{iters}")
        return 0

    if not cases:
        sys.stderr.write("no bench cases matched\n")
        return 2

    modes = tuple(dict.fromkeys(args.mode)) if args.mode else MODES
    reps = 3 if args.quick else args.reps
    n_override = 20_000 if args.quick else args.n

    vm = vm_fingerprint()
    print(f"# VM: {vm['jit_version']} ({vm['jit_version_num']}) {vm['jit_arch']}/{vm['jit_os']}, "
          f"opt flags {' '.join(vm['jit_opt_flags'])}")
    for w in vm["warnings"]:
        sys.stderr.write(f"WARNING: {w}\n")
        print(f"# WARNING: {w}")

    sc = None
    if not args.quick and n_override is None and "jit_off" in modes:
        sc = self_check()
        if not sc["ok"]:
            sys.stderr.write(
                f"JIT-off self-check FAILED: interpreted loop only {sc['ratio_off_over_on']:.2f}x "
                f"slower than compiled (need >= {sc['min_ratio']}x). jit.off(f, true) is not "
                "taking effect; the JIT-off column would be meaningless. Aborting.\n"
            )
            return 3
        print(f"# jit-off self-check ok: interpreted / compiled = "
              f"{sc['ratio_off_over_on']:.1f}x\n")

    t_start = time.time()
    results: List[CaseResult] = []
    for c in cases:
        for r in run_case(c, reps, modes, n_override):
            results.append(r)
            if r.error:
                sys.stderr.write(f"  {r.label} ... ERROR\n")
                sys.stderr.write(f"      {r.error.splitlines()[0]}\n")
            else:
                sys.stderr.write(
                    f"  {r.label} ... jit_on {_fmt_speedup(r.speedup('jit_on'))}  "
                    f"jit_off {_fmt_speedup(r.speedup('jit_off'))}\n"
                )
    elapsed = time.time() - t_start

    table = markdown_table(results, show_doc=not args.no_doc)
    print(table)
    print()
    print(f"speedup = t(original) / t(rewrite), best of {reps}. "
          f"G2 = >= {G2_MIN_SPEEDUP}x in both modes, no mode below {G2_NO_MODE_BELOW}x.")
    if n_override is not None:
        print(f"N forced to {n_override} - NOT a valid measurement.")
    print(f"total {elapsed:.1f} s")

    noisy = [(r, m, r.modes[m].jitter) for r in results for m in r.modes
             if r.modes[m].jitter > JITTER_WARN]
    if noisy:
        print(f"\n## Noisy rows (median/best > {JITTER_WARN:.2f}) - "
              "something else was using the CPU; re-run these before quoting them\n")
        for r, m, j in sorted(noisy, key=lambda x: -x[2]):
            print(f"- `{r.label}` {m}: jitter {j:.2f}, ratio {_fmt_speedup(r.speedup(m))}")

    summary = g2_summary(results)
    print("\n## G2 verdict per pattern\n")
    for pattern, verdict in summary.items():
        print(f"- `{pattern}`: {verdict}")

    drift = [(r, m) for r in results for m in MODES
             if r.doc_delta(m) is not None and abs(r.doc_delta(m)) > 0.10]
    if drift:
        print("\n## Rows more than 10% off the beam-ideas section-2 figure\n")
        for r, m in drift:
            print(f"- `{r.label}` {m}: measured {_fmt_speedup(r.speedup(m))}, "
                  f"doc {_fmt_speedup(r.case.doc.get(m))} ({_fmt_delta(r.doc_delta(m))})")

    meta = {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "vm": vm,
        "reps": reps,
        "warmup_calls": WARMUP_CALLS,
        "warmup_n": WARMUP_N,
        "d_size": D_SIZE,
        "n_jit_on_default": DEFAULT_N_JIT_ON,
        "n_jit_off_default": DEFAULT_N_JIT_OFF,
        "n_override": n_override,
        "modes": list(modes),
        "quick": args.quick,
        "self_check": sc,
        "elapsed_s": elapsed,
        "g2_min_speedup": G2_MIN_SPEEDUP,
        "g2_no_mode_below": G2_NO_MODE_BELOW,
    }
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(results_to_json(results, meta), indent=2),
                             encoding="utf-8")
        sys.stderr.write(f"wrote {args.json}\n")
    if args.markdown:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(table + "\n", encoding="utf-8")
        sys.stderr.write(f"wrote {args.markdown}\n")

    return 1 if any(r.error for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
