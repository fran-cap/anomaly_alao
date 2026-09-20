"""I-044 census: `debug_statement` findings inside LIVE per-frame bodies.

Same shape as I-041's vanilla per-frame census (lab/reports/vanilla-per-frame-census.md),
but aimed at one question: how much does an unguarded debug call on the frame path
actually cost, and how many of them are there in the live GAMMA modlist?

"Live" is resolved exactly the way lab/coord/build_overlay.py resolves it:
  * top corpus (extracted/gamma, MO2 layout): the winner of a gamedata-relative path
    is the copy shipped by the highest-priority enabled mod.
  * bottom corpus (extracted/vanilla_db): a vanilla file is live only when NO enabled
    mod ships the same path.

Per site we record: the callee, the jit_mode of the body, whether the call sits behind
a cheap static guard (`if debug then`, `if <flag> then`), and the shape of the
arguments (concat / string.format / engine call / plain literal), because Lua evaluates
arguments before the call even when the callee is a no-op.

Read-only. Usage:
    py -3.12 lab/tools/i044_debug_census.py --top   <extracted/gamma>
    py -3.12 lab/tools/i044_debug_census.py --bottom <extracted/vanilla_db>
    ... [--json out.json] [--all-bodies] [--extra-per-frame names.txt]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lab" / "coord"))

import ast_analyzer as A  # noqa: E402
from ast_analyzer import ASTAnalyzer, DEBUG_FUNCTIONS  # noqa: E402
from build_overlay import read_modlist, DEFAULT_MODLIST, DEFAULT_MODS_DIR  # noqa: E402

from luaparser import ast as lua_ast  # noqa: E402
from luaparser.astnodes import (  # noqa: E402
    Call, Invoke, Concat, String, Number, Name, Index, If, ElseIf, Method, Function,
    LocalFunction, AnonymousFunction, Nil,
)

# guard-ish condition names: a call under one of these is off unless the flag is on
GUARD_HINTS = ('debug', 'dbg', 'verbose', 'log', 'trace', 'dev', 'test')


def _name_of(node) -> str:
    try:
        return lua_ast.to_lua_source(node)
    except Exception:
        return '<?>'


def _callee_name(node) -> str:
    f = getattr(node, 'func', None)
    if f is None:
        return '<?>'
    return _name_of(f)


def _arg_shape(args) -> dict:
    """What the caller has to build before the call even happens."""
    shape = {
        'n': len(args),
        'concat': 0,        # BC_CAT, allocates a string
        'format': 0,        # string.format / strformat
        'call': 0,          # any other call/invoke evaluated as an argument
        'engine_getter': 0, # :name() :id() :section() ... inside the args
        'translate': 0,     # game.translate_string
        'literal_only': True,
    }
    for a in args:
        if not isinstance(a, (String, Number, Nil)):
            shape['literal_only'] = False
        for n in lua_ast.walk(a):
            if isinstance(n, Concat):
                shape['concat'] += 1
            elif isinstance(n, Call):
                nm = _callee_name(n)
                if nm.endswith('format'):
                    shape['format'] += 1
                elif 'translate_string' in nm:
                    shape['translate'] += 1
                else:
                    shape['call'] += 1
            elif isinstance(n, Invoke):
                m = getattr(n.func, 'id', None) or _name_of(n.func)
                shape['engine_getter'] += 1
                shape.setdefault('methods', []).append(m)
    return shape


class _Walker:
    """Collect debug calls with their enclosing function body and if-guards."""

    def __init__(self, per_frame_lines):
        self.per_frame = per_frame_lines   # list of (name, start, end, mode)
        self.sites = []

    def run(self, tree):
        self._walk(tree, guards=())

    def _walk(self, node, guards):
        if isinstance(node, If) or isinstance(node, ElseIf):
            cond = _name_of(node.test)
            g = guards + (cond,)
            self._walk_children(node.body, g)
            if getattr(node, 'orelse', None) is not None:
                # the else / elseif side is still conditional: it runs only when
                # `cond` was false, so carry a negated guard
                self._walk_children(node.orelse, guards + ('not (%s)' % cond,))
            return
        if isinstance(node, (Call, Invoke)):
            nm = _callee_name(node) if isinstance(node, Call) else _name_of(node.func)
            base = nm.split('.')[-1].split(':')[-1]
            if isinstance(node, Call) and base in DEBUG_FUNCTIONS and not nm.startswith('math.'):
                self.sites.append((node, guards, nm))
        self._walk_children(node, guards)

    def _walk_children(self, node, guards):
        if node is None:
            return
        if isinstance(node, list):
            for c in node:
                self._walk(c, guards)
            return
        if not hasattr(node, '__dict__'):
            return
        # a single child node (If.orelse is an ElseIf/Block node, not a list):
        # visit it through _walk so its own guard is picked up
        if isinstance(node, (If, ElseIf)):
            self._walk(node, guards)
            return
        for k, v in vars(node).items():
            if k.startswith('_'):
                continue
            if isinstance(v, list):
                for c in v:
                    if hasattr(c, '__dict__'):
                        self._walk(c, guards)
            elif hasattr(v, '__dict__') and not isinstance(v, (str, int, float)):
                self._walk(v, guards)


def _guard_kind(guards):
    if not guards:
        return 'unguarded'
    for g in guards:
        low = g.lower()
        if any(h in low for h in GUARD_HINTS):
            return 'flag-guard'
    return 'conditional'


def analyze(files, extra_pf=None):
    """files: iterable of (key, Path). Returns per-site records."""
    out = []
    stats = Counter()
    extra_pf = extra_pf or set()
    for key, fp in files:
        an = ASTAnalyzer()
        try:
            an.analyze_file(fp)
        except Exception:
            stats['errors'] += 1
            continue
        stats['files'] += 1
        bodies = []
        for cb in an.per_frame_callbacks:
            bodies.append((cb.name, cb.start_line, cb.end_line,
                           an._jit_mode_for_scope(cb.scope), 'name'))
        if extra_pf:
            # widen: any function scope whose name is in the extra set
            for sc in getattr(an, 'all_scopes', []) or []:
                pass
        if not bodies:
            continue
        try:
            tree = lua_ast.parse(an.source)
        except Exception:
            stats['reparse_errors'] += 1
            continue
        w = _Walker(bodies)
        w.run(tree)
        if not w.sites:
            continue
        for node, guards, nm in w.sites:
            line = getattr(node, 'line', None) or 0
            hit = None
            for (bname, s, e, mode, how) in bodies:
                if e <= 0:
                    e = s
                if s <= line <= e:
                    # innermost wins
                    if hit is None or s > hit[1]:
                        hit = (bname, s, e, mode, how)
            if hit is None:
                stats['sites_outside_pf'] += 1
                continue
            args = getattr(node, 'args', []) or []
            rec = {
                'key': key,
                'file': fp.name,
                'body': hit[0],
                'body_line': hit[1],
                'jit_mode': hit[3],
                'pf_source': hit[4],
                'line': line,
                'callee': nm,
                'guard': _guard_kind(guards),
                'guards': list(guards)[-2:],
                'shape': _arg_shape(args),
                'src': (an.source_lines[line - 1].strip()[:160]
                        if 0 < line <= len(an.source_lines) else ''),
            }
            out.append(rec)
            stats['sites_in_pf'] += 1
    return out, stats


def live_top(root: Path, modlist: Path):
    order = read_modlist(modlist)
    rank = {n: i for i, n in enumerate(order)}
    shipped = defaultdict(list)
    for mod in root.iterdir():
        if not mod.is_dir():
            continue
        gd = mod / 'gamedata'
        if not gd.is_dir():
            continue
        for f in gd.rglob('*.script'):
            shipped[f.relative_to(gd).as_posix().lower()].append((mod.name, f))
    files = []
    for rel, entries in sorted(shipped.items()):
        ranked = sorted((e for e in entries if e[0] in rank), key=lambda e: rank[e[0]])
        if ranked:
            files.append((f'{ranked[0][0]}/{rel}', ranked[0][1]))
    return files


def live_bottom(root: Path, modlist: Path, mods_dir: Path):
    order = read_modlist(modlist)
    shipped = set()
    for mod in order:
        gd = mods_dir / mod / 'gamedata'
        if gd.is_dir():
            for f in gd.rglob('*.script'):
                shipped.add(f.relative_to(gd).as_posix().lower())
    files, seen = [], set()
    for tree in root.iterdir():
        if not tree.is_dir() or not (tree / 'gamedata').is_dir():
            continue
        gd = tree / 'gamedata'
        for f in gd.rglob('*.script'):
            rel = f.relative_to(gd).as_posix().lower()
            if rel in shipped or rel in seen:
                continue
            seen.add(rel)
            files.append((rel, f))
    return files


def live_stack(gamma: Path, loose: Path, vanilla: Path, modlist: Path):
    """The real live set, three layers deep, highest priority first:

        1. the highest-priority enabled mod that ships the path
        2. else the loose patched script in <install>/Anomaly/gamedata/scripts
           (GAMMA patches the game in place; these beat the .db archives)
        3. else the vanilla db copy

    Returns [(key, path)] with one entry per gamedata-relative path.
    """
    order = read_modlist(modlist)
    rank = {n: i for i, n in enumerate(order)}
    best: dict[str, tuple[int, str, Path]] = {}

    def offer(rel, prio, tag, path):
        rel = rel.lower()
        cur = best.get(rel)
        if cur is None or prio < cur[0]:
            best[rel] = (prio, tag, path)

    if gamma and gamma.is_dir():
        for mod in gamma.iterdir():
            if not mod.is_dir() or mod.name not in rank:
                continue
            gd = mod / 'gamedata'
            if not gd.is_dir():
                continue
            for f in gd.rglob('*.script'):
                offer(f.relative_to(gd).as_posix(), rank[mod.name], 'mod:' + mod.name, f)
    LOOSE = len(rank) + 10
    if loose and loose.is_dir():
        for f in loose.rglob('*.script'):
            offer('scripts/' + f.relative_to(loose).as_posix(), LOOSE, 'loose', f)
    DB = LOOSE + 10
    if vanilla and vanilla.is_dir():
        seen = set()
        for tree in vanilla.iterdir():
            if not tree.is_dir() or not (tree / 'gamedata').is_dir():
                continue
            gd = tree / 'gamedata'
            for f in gd.rglob('*.script'):
                rel = f.relative_to(gd).as_posix().lower()
                if rel in seen:
                    continue
                seen.add(rel)
                offer(rel, DB, 'db', f)
    return [(f'{tag}|{rel}', path) for rel, (_, tag, path) in sorted(best.items())]


def report(recs, stats, label):
    print('=' * 78)
    print(f'{label}: {stats["files"]} live files analyzed, {stats.get("errors",0)} errors, '
          f'{stats.get("reparse_errors",0)} reparse errors')
    print(f'debug_statement sites inside per-frame bodies: {len(recs)}')
    print('\n-- callee --')
    for k, v in Counter(r['callee'] for r in recs).most_common(20):
        print(f'  {k:<32} {v:5d}')
    print('\n-- guard --')
    for k, v in Counter(r['guard'] for r in recs).most_common():
        print(f'  {k:<32} {v:5d}')
    print('\n-- jit_mode of the body --')
    for k, v in Counter(r['jit_mode'] for r in recs).most_common():
        print(f'  {k:<32} {v:5d}')
    print('\n-- argument shape (unguarded sites only) --')
    ug = [r for r in recs if r['guard'] == 'unguarded']
    sh = Counter()
    for r in ug:
        s = r['shape']
        sh['literal args only'] += 1 if s['literal_only'] else 0
        sh['has concat (..)'] += 1 if s['concat'] else 0
        sh['has string.format'] += 1 if s['format'] else 0
        sh['has engine method call'] += 1 if s['engine_getter'] else 0
        sh['has translate_string'] += 1 if s['translate'] else 0
        sh['has other call'] += 1 if s['call'] else 0
    print(f'  (of {len(ug)} unguarded sites)')
    for k, v in sh.most_common():
        print(f'  {k:<32} {v:5d}')
    print('\n-- top bodies --')
    for k, v in Counter((r['file'], r['body']) for r in recs).most_common(20):
        print(f'  {k[0]:<34} {k[1]:<34} {v:3d}')


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('corpus', type=Path, nargs='?')
    ap.add_argument('--bottom', action='store_true')
    ap.add_argument('--full-stack', action='store_true',
                    help='one live set over mods + loose Anomaly/gamedata + vanilla db')
    ap.add_argument('--gamma', type=Path, default=Path(r'C:\code\GIT\anomaly_alao\extracted\gamma'))
    ap.add_argument('--loose', type=Path,
                    default=Path(r'D:\GOG_Games\Gamma\S.T.A.L.K.E.R. GAMMA\Anomaly\gamedata\scripts'))
    ap.add_argument('--vanilla', type=Path,
                    default=Path(r'C:\code\GIT\anomaly_alao\extracted\vanilla_db'))
    ap.add_argument('--modlist', type=Path, default=DEFAULT_MODLIST)
    ap.add_argument('--mods-dir', type=Path, default=DEFAULT_MODS_DIR)
    ap.add_argument('--json', type=Path)
    ap.add_argument('--label', default=None)
    a = ap.parse_args(argv)
    if a.full_stack:
        files = live_stack(a.gamma, a.loose, a.vanilla, a.modlist)
    elif a.bottom:
        files = live_bottom(a.corpus, a.modlist, a.mods_dir)
    else:
        files = live_top(a.corpus, a.modlist)
    recs, stats = analyze(files)
    report(recs, stats, a.label or str(a.corpus))
    if a.json:
        a.json.write_text(json.dumps({'stats': dict(stats), 'sites': recs}, indent=1),
                          encoding='utf-8')
        print(f'\nwrote {a.json}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
