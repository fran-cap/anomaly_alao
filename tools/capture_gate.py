#!/usr/bin/env python3
"""G9: --fix must never introduce a binding that shadows a live outer name (I-046).

Text diffing cannot tell an INSERTED declaration from a MODIFIED one - both
repros of the I-040 bug come back as difflib 'replace' opcodes. So we ask the
transformer instead: run it on the ORIGINAL file, take every zero-width
insertion that actually landed, and test each name it declares against the
scope it lands in.

A capture is when the inserted `local X` sits between an outer binding of X
(an ancestor scope's local / parameter / upvalue, or a global) and a later read
of X in the same scope. That is a silent miscompile: the code still compiles
(G4) and is still idempotent (G5) and a differential run can even agree with
itself when the two values coincide - see tasks_fetch.script, where an inserted
`local actor = db.actor` shadowed a *parameter* named actor.

Use it as:

    from capture_gate import scan_paths
    res = scan_paths(originals, jobs=8, fix_debug=True, fix_nil=True)
    assert not res.captures

or from the command line via lab/tools/i021_capture_scan.py.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import os
import re
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ast_transformer import ASTTransformer, mask_lua_code  # noqa: E402

# `local a, b = ...` / `local a` at the start of any line of an inserted block.
# `local function f` declares a name too but nothing inserts one, so it is out.
_LOCAL_DECL = re.compile(
    r'^[ \t]*local[ \t]+(?!function\b)'
    r'([A-Za-z_]\w*(?:[ \t]*,[ \t]*[A-Za-z_]\w*)*)'
)

_BIG = 10 ** 9


# --------------------------------------------------------------- results

@dataclass
class Capture:
    """One inserted name that binds over a live outer name."""
    file: str
    name: str
    kind: str            # which _edit_* method emitted the insertion
    line: int            # 1-based line the declaration lands on
    scope: str           # scope it lands in
    reason: str          # 'ancestor-local' | 'free-name' | 'same-scope'
    read_line: Optional[int]   # first later read of the outer binding
    decl: str            # the declaration text we inserted

    def __str__(self):
        return ('%s:%d `%s` (%s) shadows %s, read again at line %s'
                % (self.file, self.line, self.name, self.kind, self.reason,
                   self.read_line))


@dataclass
class ScanResult:
    files: int = 0
    errors: List[str] = field(default_factory=list)
    insertions: int = 0
    names: int = 0
    by_kind: Dict[str, int] = field(default_factory=dict)
    captures: List[Capture] = field(default_factory=list)
    # shadowed but with no read of the outer binding after the insertion point:
    # legal, and usually just a cache name that repeats a name from another
    # branch of the scope tree. Reported, not gated.
    advisories: List[Capture] = field(default_factory=list)

    def merge(self, other: 'ScanResult'):
        self.files += other.files
        self.errors += other.errors
        self.insertions += other.insertions
        self.names += other.names
        for k, v in other.by_kind.items():
            self.by_kind[k] = self.by_kind.get(k, 0) + v
        self.captures += other.captures
        self.advisories += other.advisories
        return self

    def as_dict(self):
        return {
            'files': self.files,
            'errors': self.errors,
            'insertions': self.insertions,
            'declared_names': self.names,
            'insertions_by_kind': dict(sorted(self.by_kind.items())),
            'captures': [asdict(c) for c in self.captures],
            'advisories': [asdict(c) for c in self.advisories],
        }

    def summary(self) -> str:
        return ('scanned %d originals (%d errors), %d landed insertions '
                'declaring %d names -> %d captures, %d advisories'
                % (self.files, len(self.errors), self.insertions, self.names,
                   len(self.captures), len(self.advisories)))


# --------------------------------------------------------------- lua masking

# the masker lives in the transformer (it needs it too, for its global sweep)
mask_lua = mask_lua_code


def _name_lines(masked_lines: Sequence[str], name: str) -> List[int]:
    """1-based line numbers where `name` occurs as an identifier, in code."""
    pat = re.compile(r'(?<![\w.:])' + re.escape(name) + r'(?![\w])')
    return [i for i, line in enumerate(masked_lines, 1) if pat.search(line)]


# --------------------------------------------------------------- tagging transformer

def _tag(method_name: str, fn):
    kind = method_name.split('_', 2)[-1] if method_name.startswith(('_edit_', '_emit_')) else method_name

    def wrapper(self, finding=None, *a, **k):
        before = len(self.edits)
        try:
            return fn(self, finding, *a, **k)
        finally:
            label = kind
            pat = getattr(finding, 'pattern_name', None)
            if pat and kind in ('repeated_calls', 'dead_code'):
                label = '%s[%s]' % (kind, pat)
            for e in self.edits[before:]:
                self.edit_kinds.setdefault(id(e), label)
    return wrapper


def _make_tagging_class():
    ns = {}
    for nm in dir(ASTTransformer):
        if nm.startswith('_edit_') or nm.startswith('_emit_'):
            ns[nm] = _tag(nm, getattr(ASTTransformer, nm))

    def transform_file(self, *a, **k):
        self.edit_kinds = {}
        return ASTTransformer.transform_file(self, *a, **k)

    ns['transform_file'] = transform_file
    ns['edit_kinds'] = {}
    return type('TaggingTransformer', (ASTTransformer,), ns)


TaggingTransformer = _make_tagging_class()


# --------------------------------------------------------------- the check itself

def check_insertions(source: str, insertions, analyzer, file_label: str,
                     kinds: Optional[Dict[int, str]] = None) -> ScanResult:
    """The gate proper: `insertions` are the zero-width SourceEdits that landed.

    Split out from `scan_file` so a test can hand it a hand-built edit list and
    prove the gate has teeth without checking out an old transformer.
    """
    res = ScanResult(files=1)
    kinds = kinds or {}
    masked = mask_lua(source)
    masked_lines = masked.split('\n')
    line_of = _line_index(source)
    scopes = list(getattr(analyzer, 'scopes', None) or ())
    occ_cache: Dict[str, List[int]] = {}

    for edit in insertions:
        if edit.start_char != edit.end_char:
            continue
        names = []
        for raw in edit.replacement.split('\n'):
            m = _LOCAL_DECL.match(raw)
            if m:
                names.extend(n.strip() for n in m.group(1).split(','))
        if not names:
            continue
        res.insertions += 1
        kind = kinds.get(id(edit), 'unknown')
        res.by_kind[kind] = res.by_kind.get(kind, 0) + 1
        line = line_of(edit.start_char)

        scope = _scope_for(scopes, line)
        scope_name = getattr(scope, 'name', '<module>') or '<module>'
        scope_end = _scope_end(scope)
        ancestors: Set[str] = set()
        p = getattr(scope, 'parent', None)
        while p is not None:
            ancestors |= set(p.locals)
            p = p.parent
        own: Set[str] = set(getattr(scope, 'locals', ()) or ())

        for name in names:
            res.names += 1
            if name not in occ_cache:
                occ_cache[name] = _name_lines(masked_lines, name)
            later = [ln for ln in occ_cache[name] if line < ln <= scope_end]
            read_line = later[0] if later else None
            if name in ancestors:
                reason = 'ancestor-local'
            elif name in own:
                reason = 'same-scope'
            else:
                # not bound by any scope we can see from here, so a later
                # mention has to resolve to a global - which our local now
                # hides for the rest of the scope.
                reason = 'free-name'
            cap = Capture(file=file_label, name=name, kind=kind, line=line,
                          scope=scope_name, reason=reason, read_line=read_line,
                          decl=edit.replacement.strip().split('\n')[0][:120])
            if read_line is None:
                # nothing reads the name after us. A fresh name nobody mentions
                # again is the normal case and not worth a line; a name that
                # does exist in an ancestor scope is worth reporting, because it
                # only stays harmless while nothing new reads it.
                if reason == 'ancestor-local':
                    res.advisories.append(cap)
            elif reason == 'same-scope':
                # a second `local X` in the block that already declares X. Legal
                # and deliberate for the string_concat rewrite (the accumulator
                # keeps its name); the caching families never get here because
                # _resolve_cache_name renames away from the scope's own locals.
                res.advisories.append(cap)
            else:
                res.captures.append(cap)
    return res


def _line_index(source: str):
    starts = [0]
    for i, ch in enumerate(source):
        if ch == '\n':
            starts.append(i + 1)

    from bisect import bisect_right

    def line_of(pos: int) -> int:
        return bisect_right(starts, pos)
    return line_of


def _scope_end(scope) -> int:
    if scope is None:
        return _BIG
    end = getattr(scope, 'end_line', -1)
    return end if end and end > 0 else _BIG


def _scope_for(scopes, line: int):
    """Innermost scope the inserted declaration belongs to.

    A scope that *starts* on the insertion line does not contain it: the
    counter hoist (I-001) inserts its `local t_n = 0` at the start of the loop's
    own line, i.e. textually before the `for`, so it belongs to the loop's
    parent. Same story at the other end - the string_concat rewrite appends its
    `local s = table.concat(...)` to the loop's `end` line, which is already
    outside the loop.
    """
    best = None
    for s in scopes:
        if s.start_line <= line <= _scope_end(s):
            if best is None or s.start_line > best.start_line:
                best = s
    while best is not None and (best.start_line >= line
                                or (_scope_end(best) == line and best.start_line < line)):
        best = best.parent
    return best


# --------------------------------------------------------------- driving the transformer

def scan_file(path: Path, **flags) -> ScanResult:
    """Run the transformer on one ORIGINAL file and check what it would insert."""
    flags.setdefault('backup', False)
    flags.setdefault('dry_run', True)
    flags.setdefault('verify_compile', False)
    t = TaggingTransformer()
    try:
        modified, _content, _n = t.transform_file(Path(path), **flags)
    except Exception as e:
        return ScanResult(files=1, errors=['%s: %s: %s' % (path, type(e).__name__, e)])
    if not modified or t.analyzer is None:
        return ScanResult(files=1)
    landed = getattr(t, 'applied_edits', None)
    if landed is None:                      # ALAO older than I-046
        landed = t.edits
    return check_insertions(t.source, [e for e in landed if e.start_char == e.end_char],
                            t.analyzer, Path(path).name, t.edit_kinds)


def _scan_one(job):
    path, flags = job
    return scan_file(Path(path), **flags)


def scan_paths(paths, jobs: int = 1, **flags) -> ScanResult:
    """Scan many originals, optionally in a process pool. Returns one ScanResult."""
    paths = [Path(p) for p in paths]
    total = ScanResult()
    if jobs and jobs > 1 and len(paths) > 8:
        with concurrent.futures.ProcessPoolExecutor(max_workers=jobs) as ex:
            for r in ex.map(_scan_one, [(str(p), flags) for p in paths], chunksize=8):
                total.merge(r)
    else:
        for p in paths:
            total.merge(scan_file(p, **flags))
    return total


def originals_in(root: Path) -> List[Path]:
    """The pre-fix sources under `root`: the .alao-bak files if there are any.

    A fixed corpus tree keeps the original next to the rewrite; scanning the
    rewrite instead would ask the transformer what it would do to its own
    output, which is a different (and already-covered, see G5) question.
    """
    root = Path(root)
    if root.is_file():
        return [root]
    baks = sorted(root.rglob('*.alao-bak'))
    if baks:
        return baks
    out = sorted(root.rglob('*.script')) + sorted(root.rglob('*.lua'))
    return out


def scan_trees(roots, jobs: int = 1, **flags) -> ScanResult:
    paths: List[Path] = []
    for r in roots:
        paths.extend(originals_in(Path(r)))
    return scan_paths(paths, jobs=jobs, **flags)


FIX_FLAG_MAP = {
    '--fix-debug': 'fix_debug',
    '--fix-yellow': 'fix_yellow',
    '--fix-nil': 'fix_nil',
    '--experimental': 'experimental',
    '--remove-dead-code': 'remove_dead_code',
}


def flags_from_cli(fix_flags) -> Dict[str, bool]:
    """Turn ALAO's own `--fix --fix-debug ...` into transform_file kwargs."""
    if isinstance(fix_flags, str):
        fix_flags = fix_flags.split()
    out: Dict[str, bool] = {}
    for f in fix_flags or ():
        key = FIX_FLAG_MAP.get(f)
        if key:
            out[key] = True
    return out


# --------------------------------------------------------------- CLI

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('trees', nargs='+', type=Path, help='fixed work trees (or plain script dirs)')
    ap.add_argument('--fix-flags', default='--fix --fix-debug --fix-nil',
                    help='the fix flags the tree was produced with')
    ap.add_argument('--jobs', '-j', type=int, default=min(8, os.cpu_count() or 4))
    ap.add_argument('--show', type=int, default=40, help='how many captures to print')
    args = ap.parse_args(argv)

    res = scan_trees(args.trees, jobs=args.jobs, **flags_from_cli(args.fix_flags))
    print(res.summary())
    for kind, n in sorted(res.by_kind.items()):
        print('   %-36s %d' % (kind, n))
    print('CAPTURES: %d' % len(res.captures))
    for c in res.captures[:args.show]:
        print('   ' + str(c))
    if res.advisories:
        print('advisories (shadow with no later read): %d' % len(res.advisories))
        for c in res.advisories[:args.show]:
            print('   ' + str(c))
    for e in res.errors[:10]:
        print('   [err] ' + e)
    return 1 if res.captures else 0


if __name__ == '__main__':
    raise SystemExit(main())
