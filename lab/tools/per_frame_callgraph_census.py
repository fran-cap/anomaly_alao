"""I-042: how much bigger does the per-frame set get if you follow the calls?

I-013/I-010 decide "this body runs per frame" from its NAME only: an engine
callback (`*_on_update`) or an `:update` / `:Update` method. I-041's vanilla
census showed that is the wrong set - 74 of 103 live per-frame bodies have zero
actionable findings, and the code that actually burns the frame sits one or two
hops out (`xr_logic.pick_section_from_condlist`, `axr_main.make_callback`).

So this walks the call graph instead. It is a LAB TOOL, not an analyzer pass,
on purpose: the per-file analysis runs in a multiprocessing pool with picklable
workers and a 10 s per-file timeout, and a cross-file pass cannot live there
without either serialising the pool or blowing G6. What the analyzer already
gives us per file (scopes, calls, jit_modes, findings) is enough to emit an
edge list; the propagation is a post-pass over the whole corpus, here.

"Live" means the copy of the script the game actually loads: highest-priority
enabled mod wins, and a vanilla `scripts.db0` file is live only when no enabled
mod ships that name. Same rule as lab/coord/build_overlay.py, which this reuses
for reading the modlist.

    py -3.12 lab/tools/per_frame_callgraph_census.py \
        --gamma C:\\...\\extracted\\gamma --vanilla C:\\...\\extracted\\vanilla_db \
        --json out.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "lab" / "coord"))

from build_overlay import read_modlist, DEFAULT_MODLIST  # noqa: E402

# `local foo = function(...)` / `foo = function(...)` / `m.foo = function(...)`
ANON_ASSIGN = re.compile(r'^\s*(?:local\s+)?([A-Za-z_][\w.]*)\s*=\s*function\s*\(')
LOCAL_DECL = re.compile(r'^\s*local\s+')

# I-042 seed extension: a function registered for one of these events runs per
# frame no matter what it is called. `_on_first_update` is deliberately out.
PER_FRAME_EVENTS = frozenset({
    'actor_on_update', 'npc_on_update', 'monster_on_update',
    'physic_object_on_update', 'squad_on_update', 'hud_update',
    'smart_terrain_on_update', 'generic_on_update', 'on_before_hit',
})

GREEN_DEBUG = ('GREEN', 'DEBUG')


# --------------------------------------------------------------------------
# per-file extraction (pool worker: takes a tuple, returns plain dicts)
# --------------------------------------------------------------------------

def extract_one(job):
    """Analyze one script; return its functions, call edges and findings."""
    rel, path_str, origin, mod, nyi_baseline = job
    import ast_analyzer
    if nyi_baseline:
        # I-042 deliverable 1: the classification delta from the two gaps
        ast_analyzer.ENGINE_NYI_METHODS = frozenset(
            ast_analyzer.ENGINE_NYI_METHODS - {'section_name', 'profile_name'})
    from ast_analyzer import ASTAnalyzer

    path = Path(path_str)
    analyzer = ASTAnalyzer()
    try:
        findings = analyzer.analyze_file(path)
    except Exception as exc:  # parse failure, encoding, luaparser blowup
        return {'rel': rel, 'error': '%s: %s' % (type(exc).__name__, exc)}

    try:
        lines = path.read_text(
            encoding=getattr(analyzer, '_file_encoding', None) or 'utf-8',
            errors='replace').splitlines()
    except Exception:
        lines = []

    module = Path(rel).stem.lower()

    # --- function bodies -------------------------------------------------
    funcs = [s for s in analyzer.scopes if s.scope_type == 'function']
    idx_of = {id(s): i for i, s in enumerate(funcs)}

    def enclosing_func_idx(scope):
        cur = scope
        while cur is not None:
            if cur.scope_type == 'function':
                return idx_of.get(id(cur))
            cur = cur.parent
        return None

    def func_depth(scope):
        d, cur = 0, scope.parent
        while cur is not None:
            if cur.scope_type == 'function':
                d += 1
            cur = cur.parent
        return d

    per_frame_ids = {id(cb.scope): cb.name for cb in analyzer.per_frame_callbacks}

    out_funcs = []
    exports = {}        # name reachable as `<module>.<name>` -> func idx
    file_locals = {}    # name callable bare inside this file -> func idx
    for i, s in enumerate(funcs):
        info = analyzer.jit_modes.get(id(s))
        name = s.name or '<anon>'
        top = func_depth(s) == 0
        # `function foo()` at top level defines the module export `<file>.foo`;
        # `local function foo` is file-private; `foo = function()` needs the
        # source line, luaparser gives the anon body no name at all.
        bare = name
        if name == '<anon>' and top and 0 < s.start_line <= len(lines):
            m = ANON_ASSIGN.match(lines[s.start_line - 1])
            if m:
                bare = m.group(1)
        decl = lines[s.start_line - 1] if 0 < s.start_line <= len(lines) else ''
        is_local = bool(LOCAL_DECL.match(decl))
        if top and bare != '<anon>' and is_local:
            # `local function foo` / `local foo = function` is file-private:
            # callable bare inside this file, invisible as `<module>.foo`
            if '.' not in bare:
                file_locals.setdefault(bare, i)
        elif top and bare != '<anon>':
            if '.' in bare:
                # `function cmd.alife()` is a field of a module table, not a
                # global and not `<module>.alife` - key it under its own path
                # only, or every such file donates junk to the global namespace
                exports.setdefault(bare, i)
            else:
                exports.setdefault(bare, i)
                file_locals.setdefault(bare, i)
        out_funcs.append({
            'idx': i,
            'name': name,
            'bare': bare,
            'module': module,
            'top_level': top,
            'start_line': s.start_line,
            'end_line': s.end_line,
            'size': max(s.end_line - s.start_line, 0),
            'jit_mode': info.mode if info else 'unknown',
            'abort_sites': len(info.sites) if info else 0,
            'abort_reasons': sorted({r for r in (info.reasons if info else [])}),
            'per_frame_by_name': id(s) in per_frame_ids,
            'display': per_frame_ids.get(id(s), name),
        })

    # --- call edges ------------------------------------------------------
    edges = defaultdict(Counter)   # caller idx -> Counter of target descriptors
    for c in analyzer.calls:
        src = enclosing_func_idx(c.scope) if c.scope else None
        if src is None:
            continue  # module-level call; runs once at load
        if ':' in (c.full_name or ''):
            tgt = 'method:' + (c.func or '')
        elif c.module:
            tgt = 'qual:%s.%s' % (c.module.lower(), c.func)
        else:
            tgt = 'bare:' + (c.full_name or '')
        edges[src][tgt] += 1

    # --- RegisterScriptCallback("event", fn) -----------------------------
    registrations = []
    for c in analyzer.calls:
        if c.full_name != 'RegisterScriptCallback' or len(c.args) < 2:
            continue
        ev = analyzer._node_to_string(c.args[0]) or ''
        ev = ev.strip().strip('"').strip("'")
        fn = analyzer._node_to_string(c.args[1]) or ''
        registrations.append({'event': ev, 'func': fn, 'line': c.line})

    # --- findings, attributed to the innermost function body -------------
    fnd = defaultdict(Counter)   # func idx -> Counter(pattern)
    file_findings = Counter()
    for f in findings:
        if f.severity not in GREEN_DEBUG:
            continue
        file_findings[(f.severity, f.pattern_name)] += 1
        best, best_span = None, None
        for i, s in enumerate(funcs):
            if not (s.start_line <= f.line_num <= s.end_line):
                continue
            span = s.end_line - s.start_line
            if best_span is None or span < best_span:
                best, best_span = i, span
        if best is not None:
            fnd[best]['%s|%s' % (f.severity, f.pattern_name)] += 1

    return {
        'rel': rel, 'module': module, 'origin': origin, 'mod': mod,
        'funcs': out_funcs,
        'exports': exports,
        'file_locals': file_locals,
        'edges': {str(k): dict(v) for k, v in edges.items()},
        'registrations': registrations,
        'findings': {str(k): dict(v) for k, v in fnd.items()},
        'file_findings': {'%s|%s' % k: v for k, v in file_findings.items()},
    }


# --------------------------------------------------------------------------
# live-winner resolution (same rule as lab/coord/build_overlay.py)
# --------------------------------------------------------------------------

def resolve_live(gamma_root, vanilla_root, modlist_path):
    order = read_modlist(modlist_path)
    rank = {n: i for i, n in enumerate(order)}
    winners = {}   # rel(lower) -> dict
    shadowed = Counter()
    not_in_modlist = set()

    if gamma_root:
        for mod_dir in sorted(p for p in gamma_root.iterdir() if p.is_dir()):
            gd = mod_dir / 'gamedata'
            if not gd.is_dir():
                continue
            if mod_dir.name not in rank:
                not_in_modlist.add(mod_dir.name)
                continue
            r = rank[mod_dir.name]
            for f in list(gd.rglob('*.script')) + list(gd.rglob('*.lua')):
                if f.name.endswith('.alao-bak'):
                    continue
                rel = f.relative_to(gd).as_posix().lower()
                cur = winners.get(rel)
                if cur is None or r < cur['rank']:
                    if cur is not None:
                        shadowed[cur['origin']] += 1
                    winners[rel] = {'rel': rel, 'path': str(f), 'rank': r,
                                    'origin': 'gamma', 'mod': mod_dir.name}
                else:
                    shadowed['gamma'] += 1

    if vanilla_root:
        # extracted/vanilla_db carries the same scripts twice (raw/ and
        # VANILLA_DB/gamedata/); the MO2-shaped copy is the one to walk.
        for tree in sorted(p for p in vanilla_root.iterdir() if p.is_dir()):
            gd = tree / 'gamedata'
            if not gd.is_dir():
                continue
            for f in list(gd.rglob('*.script')) + list(gd.rglob('*.lua')):
                if f.name.endswith('.alao-bak'):
                    continue
                rel = f.relative_to(gd).as_posix().lower()
                if rel in winners:
                    shadowed['vanilla'] += 1
                    continue
                winners[rel] = {'rel': rel, 'path': str(f), 'rank': 10 ** 6,
                                'origin': 'vanilla', 'mod': tree.name}
    return winners, shadowed, sorted(not_in_modlist)


# --------------------------------------------------------------------------
# propagation
# --------------------------------------------------------------------------

class Graph:
    """Resolved call graph over the live scripts. Node = (rel, func idx)."""

    def __init__(self, files):
        self.files = {f['rel']: f for f in files if 'error' not in f}
        self.by_module = {}
        for rel, f in self.files.items():
            self.by_module.setdefault(f['module'], rel)   # one file per module
        # global namespace: top-level `function foo()` is a real global in
        # Anomaly (that is how _g.script exports SendScriptCallback)
        # Several files can define the same bare global (`ph_car.script` has a
        # no-op `function printf() end`); at runtime the last loaded wins and we
        # cannot know the order, so prefer `_g.script`, which is where Anomaly
        # keeps the real ones, and count the ambiguity.
        defs = defaultdict(list)
        for rel, f in self.files.items():
            for name, i in f['exports'].items():
                if '.' not in name:
                    defs[name].append((rel, i))
        self.globals = {}
        self.ambiguous_globals = 0
        for name, cands in defs.items():
            if len(cands) > 1:
                self.ambiguous_globals += 1
            pick = next((c for c in cands if self.files[c[0]]['module'] == '_g'), None)
            self.globals[name] = pick or sorted(cands)[0]

    def node(self, rel, i):
        return (rel, i)

    def func(self, n):
        return self.files[n[0]]['funcs'][n[1]]

    def resolve(self, rel, tgt):
        """One call descriptor -> (node, edge_kind) or (None, kind_that_failed)."""
        f = self.files[rel]
        if tgt.startswith('bare:'):
            name = tgt[5:]
            i = f['file_locals'].get(name)
            if i is not None:
                return (rel, i), 'same-file'
            g = self.globals.get(name)
            if g is not None:
                return g, 'global'
            return None, 'unresolved-bare'
        if tgt.startswith('qual:'):
            mod, _, fn = tgt[5:].partition('.')
            other = self.by_module.get(mod)
            if other is None:
                return None, 'unresolved-module'
            i = self.files[other]['exports'].get(fn)
            if i is None:
                return None, 'unresolved-func'
            return (other, i), 'module.func'
        return None, 'method'

    def callees(self, node, kinds):
        rel, i = node
        out = []
        for tgt, n in self.files[rel]['edges'].get(str(i), {}).items():
            tnode, kind = self.resolve(rel, tgt)
            if tnode is None or kind not in kinds:
                continue
            out.append((tnode, kind, n))
        return out


def seed_set(graph, live_only=True):
    """Name-based per-frame bodies, plus the registration-based ones."""
    seeds, by_reg = set(), set()
    for rel, f in graph.files.items():
        for fn in f['funcs']:
            if fn['per_frame_by_name']:
                seeds.add((rel, fn['idx']))
    # RegisterScriptCallback("actor_on_update", fn): fn is per-frame whatever
    # it is called. `this.foo` / `foo` / `obj` forms.
    for rel, f in graph.files.items():
        for reg in f['registrations']:
            if reg['event'] not in PER_FRAME_EVENTS:
                continue
            name = reg['func'].split('.')[-1].split(':')[-1]
            i = f['file_locals'].get(name)
            if i is None:
                g = graph.globals.get(name)
                if g is not None and g[0] == rel:
                    i = g[1]
            if i is None:
                continue
            node = (rel, i)
            if node not in seeds:
                by_reg.add(node)
    return seeds, by_reg


def propagate(graph, seeds, kinds, hops):
    """Return {node: (hop, kind_of_first_edge)} for everything reached."""
    reached = {n: (0, 'seed') for n in seeds}
    frontier = list(seeds)
    for hop in range(1, hops + 1):
        nxt = []
        for n in frontier:
            for tnode, kind, _cnt in graph.callees(n, kinds):
                if tnode in reached:
                    continue
                reached[tnode] = (hop, kind)
                nxt.append(tnode)
        frontier = nxt
        if not frontier:
            break
    return reached


# --------------------------------------------------------------------------

def summarize(graph, nodes):
    modes = Counter()
    findings = Counter()
    for n in nodes:
        fn = graph.func(n)
        modes[fn['jit_mode']] += 1
        for key, cnt in graph.files[n[0]]['findings'].get(str(n[1]), {}).items():
            findings[key] += cnt
    return modes, findings


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--gamma', type=Path)
    ap.add_argument('--vanilla', type=Path)
    ap.add_argument('--modlist', type=Path, default=DEFAULT_MODLIST)
    ap.add_argument('--workers', type=int, default=8)
    ap.add_argument('--hops', type=int, default=2)
    ap.add_argument('--json', type=Path)
    ap.add_argument('--nyi-baseline', action='store_true',
                    help='drop section_name/profile_name from ENGINE_NYI_METHODS '
                         '(the pre-I-042 tables), for the classification delta')
    ap.add_argument('--top', type=int, default=40)
    a = ap.parse_args(argv)

    winners, shadowed, missing = resolve_live(a.gamma, a.vanilla, a.modlist)
    print('live winners: %d (%d gamma, %d vanilla); %d shadowed copies; '
          '%d tree mods not in the modlist'
          % (len(winners),
             sum(1 for w in winners.values() if w['origin'] == 'gamma'),
             sum(1 for w in winners.values() if w['origin'] == 'vanilla'),
             sum(shadowed.values()), len(missing)))

    jobs = [(w['rel'], w['path'], w['origin'], w['mod'], a.nyi_baseline)
            for w in winners.values()]
    files, errors = [], []
    with ProcessPoolExecutor(max_workers=a.workers) as pool:
        for res in pool.map(extract_one, jobs, chunksize=8):
            if 'error' in res:
                errors.append(res)
            else:
                files.append(res)
    print('analyzed %d files, %d failed' % (len(files), len(errors)))
    for e in errors[:10]:
        print('  FAIL %s  %s' % (e['rel'], e['error']))

    graph = Graph(files)
    total_funcs = sum(len(f['funcs']) for f in files)
    print('%d function bodies, %d module exports, %d bare globals '
          '(%d defined in more than one live file)'
          % (total_funcs, sum(len(f['exports']) for f in files),
             len(graph.globals), graph.ambiguous_globals))

    # how much of the graph we can actually see
    kinds = Counter()
    for rel, f in graph.files.items():
        for _src, tgts in f['edges'].items():
            for tgt, n in tgts.items():
                _node, kind = graph.resolve(rel, tgt)
                kinds[kind] += n
    tot = sum(kinds.values())
    print('call sites inside function bodies: %d; resolution: %s' % (
        tot, ', '.join('%s %d (%.0f%%)' % (k, v, 100.0 * v / max(tot, 1))
                       for k, v in kinds.most_common())))

    seeds, by_reg = seed_set(graph)
    print('\nseed set: %d name-based per-frame bodies + %d registration-only '
          '(RegisterScriptCallback to a per-frame event under a non-matching name)'
          % (len(seeds), len(by_reg)))

    # deliverable 3: what the NAME-based classifier misses about registrations
    reg_tot = reg_pf = reg_hit = reg_miss = reg_unres = 0
    reg_events = Counter()
    reg_kind = Counter()
    miss_examples = []
    for rel, f in graph.files.items():
        for reg in f['registrations']:
            reg_tot += 1
            if reg['event'] not in PER_FRAME_EVENTS:
                continue
            reg_pf += 1
            reg_events[reg['event']] += 1
            name = reg['func'].split('.')[-1].split(':')[-1]
            i = f['file_locals'].get(name)
            if i is None:
                g = graph.globals.get(name)
                i = g[1] if g is not None and g[0] == rel else None
            if i is None:
                reg_unres += 1
                continue
            if f['funcs'][i]['per_frame_by_name']:
                reg_hit += 1
            else:
                reg_miss += 1
                # two different causes hide in here: a handler whose NAME is a
                # per-frame callback but which is declared `local function`
                # (the visitor never builds a PerFrameCallbackInfo for those -
                # a plain classifier bug), and a handler under a name the rule
                # could never guess (`process_queue`, `batt_checker`).
                from ast_analyzer import _is_per_frame_callback_name
                reg_kind['local-decl of a per-frame name'
                         if _is_per_frame_callback_name(name)
                         else 'name the rule cannot guess'] += 1
                if len(miss_examples) < 15:
                    miss_examples.append('%s:%d %s -> %s (%s)' % (
                        rel, reg['line'], reg['event'], reg['func'],
                        f['funcs'][i]['jit_mode']))
    print('RegisterScriptCallback sites in live files: %d; to a per-frame event: %d (%s)'
          % (reg_tot, reg_pf, ', '.join('%s %d' % kv for kv in reg_events.most_common())))
    print('  handler already caught by the name rule: %d; MISSED by it: %d; '
          'handler not resolvable in this file: %d' % (reg_hit, reg_miss, reg_unres))
    for k, v in reg_kind.most_common():
        print('    missed because: %-34s %d' % (k, v))
    for ex in miss_examples:
        print('    miss: %s' % ex)

    all_seeds = seeds | by_reg
    strict = {'same-file', 'module.func'}
    wide = strict | {'global'}

    results = {}
    for label, kinds, hops in (('strict', strict, a.hops), ('with-globals', wide, a.hops)):
        reached = propagate(graph, all_seeds, kinds, hops)
        results[label] = reached

    def report(label, reached):
        print('\n=== %s edges (%s) ===' % (label, ', '.join(sorted(
            strict if label == 'strict' else wide))))
        for hop in range(0, a.hops + 1):
            nodes = [n for n, (h, _) in reached.items() if h == hop]
            if not nodes and hop:
                continue
            modes, fnds = summarize(graph, nodes)
            cum = [n for n, (h, _) in reached.items() if h <= hop]
            cmodes, cfnds = summarize(graph, cum)
            print('hop %d: +%d bodies (interp %d / mixed %d / compiled %d) | '
                  'cumulative %d (interp %d / mixed %d / compiled %d)'
                  % (hop, len(nodes), modes['interpreted'], modes['mixed'],
                     modes['compiled'], len(cum), cmodes['interpreted'],
                     cmodes['mixed'], cmodes['compiled']))
            if cfnds:
                top = ', '.join('%s %d' % (k, v) for k, v in cfnds.most_common(8))
                print('        cumulative GREEN/DEBUG inside the set: %s' % top)

        # origin split of the final set
        origins = Counter(graph.files[n[0]]['origin'] for n in reached)
        print('   origin split: ' + ', '.join('%s %d' % kv for kv in origins.most_common()))

    for label in ('strict', 'with-globals'):
        report(label, results[label])

    # hottest one-hop callees by number of distinct per-frame callers
    callers = Counter()
    caller_sets = defaultdict(set)
    for n in all_seeds:
        for tnode, kind, _ in graph.callees(n, wide):
            if tnode in all_seeds:
                continue
            caller_sets[tnode].add(n)
    for tnode, s in caller_sets.items():
        callers[tnode] = len(s)
    print('\n=== hottest one-hop callees (distinct per-frame callers) ===')
    print('| callee | callers | jit_mode | aborts | origin |')
    print('|---|---:|---|---:|---|')
    for tnode, c in callers.most_common(a.top):
        fn = graph.func(tnode)
        f = graph.files[tnode[0]]
        print('| `%s.%s` | %d | %s | %d | %s |'
              % (fn['module'], fn['bare'], c, fn['jit_mode'], fn['abort_sites'],
                 f['origin']))

    # known positives
    print('\n=== known positives ===')
    for want in ('axr_main.make_callback', 'xr_logic.pick_section_from_condlist'):
        mod, _, fname = want.partition('.')
        rel = graph.by_module.get(mod)
        status = 'MODULE NOT LIVE'
        if rel is not None:
            i = graph.files[rel]['exports'].get(fname)
            if i is None:
                status = 'not exported by %s' % rel
            else:
                node = (rel, i)
                bits = []
                for label in ('strict', 'with-globals'):
                    hopkind = results[label].get(node)
                    bits.append('%s=%s' % (label, 'hop %d via %s' % hopkind
                                           if hopkind else 'ABSENT'))
                fn = graph.func(node)
                status = '%s | %s, %d aborts, in %s' % (
                    ', '.join(bits), fn['jit_mode'], fn['abort_sites'],
                    graph.files[rel]['mod'])
        print('  %-44s %s' % (want, status))

    if a.json:
        payload = {
            'live_files': len(winners), 'analyzed': len(files),
            'errors': [e['rel'] for e in errors],
            'total_funcs': total_funcs,
            'seeds_name_based': len(seeds), 'seeds_registration_only': len(by_reg),
            'sets': {},
            'files': {rel: {'origin': f['origin'], 'mod': f['mod'],
                            'module': f['module'],
                            'funcs': f['funcs'], 'findings': f['findings'],
                            'file_findings': f['file_findings']}
                      for rel, f in graph.files.items()},
        }
        for label, reached in results.items():
            payload['sets'][label] = [
                {'rel': n[0], 'idx': n[1], 'hop': h, 'kind': k,
                 'module': graph.func(n)['module'], 'name': graph.func(n)['bare'],
                 'jit_mode': graph.func(n)['jit_mode'],
                 'aborts': graph.func(n)['abort_sites'],
                 'origin': graph.files[n[0]]['origin'],
                 'findings': graph.files[n[0]]['findings'].get(str(n[1]), {})}
                for n, (h, k) in reached.items()]
        a.json.parent.mkdir(parents=True, exist_ok=True)
        a.json.write_text(json.dumps(payload, indent=1), encoding='utf-8')
        print('\nwrote %s' % a.json)
    return 0


if __name__ == '__main__':
    sys.exit(main())
