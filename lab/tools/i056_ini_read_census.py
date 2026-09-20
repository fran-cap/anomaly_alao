"""I-056: how many ini / MCM option reads happen on the live per-frame stack?

Background. `ini_file_ex:r_value` in vanilla `_g.script` memoizes into
`self.cache[s.."&"..k]` and tests the hit with `if (cache_result) then`, so a
cached `false` (and an absent key, which is never cached at all) re-crosses into
the engine ini reader on EVERY read. That was the premise of I-056.

On THIS install the premise is different and worse: GAMMA runs Modded Exes, and
the loose `Anomaly/gamedata/scripts/_g_patches.script` throws the whole class
away (`_G.ini_file_ex = new_ini_file_ex`, "Disable caching of ini values to
utilize engine functions and reduce memory footprint"). The live `r_value` has
no cache at all, so every read of every value class is two engine crossings
(`line_exist` + `r_string`) plus Lua-side string compares.

This tool counts the read sites, attributed to the innermost enclosing function,
and intersects them with the per-frame reachable set from
`per_frame_callgraph_census.py` (same live-winner rule, same call graph).

    py -3.12 lab/tools/i056_ini_read_census.py \
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
sys.path.insert(0, str(REPO_ROOT / "lab" / "tools"))

from per_frame_callgraph_census import (  # noqa: E402
    ANON_ASSIGN, LOCAL_DECL, DEFAULT_LOOSE, Graph, propagate, resolve_live,
    seed_set)
from build_overlay import DEFAULT_MODLIST  # noqa: E402

# Methods on an ini_file_ex / ini_file that cross into the engine reader.
# `r_value` is the one I-056 is about; the rest are the same crossing by
# another name (on the live class they all funnel through r_string/line_exist).
INI_METHODS = frozenset({
    'r_value', 'r_bool_ex', 'r_float_ex', 'r_string_ex', 'r_sec_ex',
    'r_line_ex', 'r_string_to_condlist', 'r_list', 'r_mult',
    'collect_section', 'line_exist', 'section_exist',
    'r_string', 'r_float', 'r_bool', 'r_u32', 'r_s32', 'r_line', 'r_clsid',
    'line_count', 'section_for_each',
})
# Bare / qualified option readers.
BARE_READERS = frozenset({'SYS_GetParam', 'ini_file', 'create_ini_file'})
QUAL_READERS = {
    ('ui_mcm', 'get'),
    ('ui_mcm', 'get_opt_table'),
    ('ui_options', 'get'),
}


def extract_one(job):
    rel, path_str, origin, mod = job
    from ast_analyzer import ASTAnalyzer

    path = Path(path_str)
    analyzer = ASTAnalyzer()
    try:
        findings = analyzer.analyze_file(path)  # noqa: F841 - runs the walk
    except Exception as exc:
        return {'rel': rel, 'error': '%s: %s' % (type(exc).__name__, exc)}

    try:
        lines = path.read_text(
            encoding=getattr(analyzer, '_file_encoding', None) or 'utf-8',
            errors='replace').splitlines()
    except Exception:
        lines = []

    module = Path(rel).stem.lower()
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

    out_funcs, exports, file_locals = [], {}, {}
    for i, s in enumerate(funcs):
        name = s.name or '<anon>'
        top = func_depth(s) == 0
        bare = name
        if name == '<anon>' and top and 0 < s.start_line <= len(lines):
            m = ANON_ASSIGN.match(lines[s.start_line - 1])
            if m:
                bare = m.group(1)
        decl = lines[s.start_line - 1] if 0 < s.start_line <= len(lines) else ''
        is_local = bool(LOCAL_DECL.match(decl))
        if top and bare != '<anon>' and is_local:
            if '.' not in bare:
                file_locals.setdefault(bare, i)
        elif top and bare != '<anon>':
            exports.setdefault(bare, i)
            if '.' not in bare:
                file_locals.setdefault(bare, i)
        out_funcs.append({
            'idx': i, 'name': name, 'bare': bare, 'module': module,
            'top_level': top, 'start_line': s.start_line, 'end_line': s.end_line,
            'per_frame_by_name': id(s) in per_frame_ids,
            'display': per_frame_ids.get(id(s), name),
        })

    edges = defaultdict(Counter)
    reads = defaultdict(list)     # func idx -> list of read sites
    module_reads = []             # read sites at module level (load-time, free)

    for c in analyzer.calls:
        src = enclosing_func_idx(c.scope) if c.scope else None
        full = c.full_name or ''
        if ':' in full:
            tgt = 'method:' + (c.func or '')
        elif c.module:
            tgt = 'qual:%s.%s' % (c.module.lower(), c.func)
        else:
            tgt = 'bare:' + full
        if src is not None:
            edges[src][tgt] += 1

        kind = None
        if ':' in full and c.func in INI_METHODS:
            kind = 'method'
        elif c.module and (c.module.lower(), c.func) in QUAL_READERS:
            kind = 'qual'
        elif not c.module and full in BARE_READERS:
            kind = 'bare'
        if kind is None:
            continue
        site = {
            'name': full, 'func': c.func, 'kind': kind, 'line': c.line,
            'in_loop': bool(getattr(c, 'in_loop', False)),
            'src': (lines[c.line - 1].strip()[:160]
                    if 0 < c.line <= len(lines) else ''),
        }
        if src is None:
            module_reads.append(site)
        else:
            reads[src].append(site)

    registrations = []
    for c in analyzer.calls:
        if c.full_name != 'RegisterScriptCallback' or len(c.args) < 2:
            continue
        ev = (analyzer._node_to_string(c.args[0]) or '').strip().strip('"').strip("'")
        fn = analyzer._node_to_string(c.args[1]) or ''
        registrations.append({'event': ev, 'func': fn, 'line': c.line})

    return {
        'rel': rel, 'module': module, 'origin': origin, 'mod': mod,
        'funcs': out_funcs, 'exports': exports, 'file_locals': file_locals,
        'edges': {str(k): dict(v) for k, v in edges.items()},
        'registrations': registrations,
        'findings': {},
        'reads': {str(k): v for k, v in reads.items()},
        'module_reads': module_reads,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--gamma', type=Path)
    ap.add_argument('--vanilla', type=Path)
    ap.add_argument('--modlist', type=Path, default=DEFAULT_MODLIST)
    ap.add_argument('--loose', type=Path, default=DEFAULT_LOOSE)
    ap.add_argument('--workers', type=int, default=8)
    ap.add_argument('--hops', type=int, default=2)
    ap.add_argument('--top', type=int, default=30)
    ap.add_argument('--json', type=Path)
    a = ap.parse_args(argv)

    winners, shadowed, missing = resolve_live(a.gamma, a.vanilla, a.modlist, a.loose)
    origins = Counter(w['origin'] for w in winners.values())
    print('live winners: %d (%s)' % (
        len(winners), ', '.join('%s %d' % kv for kv in origins.most_common())))

    jobs = [(w['rel'], w['path'], w['origin'], w['mod']) for w in winners.values()]
    files, errors = [], []
    with ProcessPoolExecutor(max_workers=a.workers) as pool:
        for res in pool.map(extract_one, jobs, chunksize=8):
            (errors if 'error' in res else files).append(res)
    print('analyzed %d files, %d failed' % (len(files), len(errors)))

    graph = Graph(files)
    seeds, by_reg = seed_set(graph)
    all_seeds = seeds | by_reg
    reached = propagate(graph, all_seeds,
                        {'same-file', 'global', 'module.func'}, a.hops)
    print('seeds %d (+%d registration-only); reachable within %d hops: %d bodies'
          % (len(seeds), len(by_reg), a.hops, len(reached)))

    # ---- totals -----------------------------------------------------------
    tot_sites = 0
    tot_module = 0
    for f in files:
        tot_sites += sum(len(v) for v in f['reads'].values())
        tot_module += len(f['module_reads'])
    print('\nini/option read sites in live scripts: %d inside functions, '
          '%d at module level (load-time only)' % (tot_sites, tot_module))

    rows = []
    by_name = Counter()
    by_name_pf = Counter()
    for node, (hop, _kind) in sorted(reached.items()):
        rel, i = node
        sites = graph.files[rel]['reads'].get(str(i), [])
        if not sites:
            continue
        fn = graph.func(node)
        for s in sites:
            by_name_pf[s['name']] += 1
        rows.append({
            'rel': rel, 'mod': graph.files[rel]['mod'], 'func': fn['display'],
            'line': fn['start_line'], 'hop': hop, 'n': len(sites),
            'in_loop': sum(1 for s in sites if s['in_loop']),
            'sites': sites,
        })
    for f in files:
        for v in f['reads'].values():
            for s in v:
                by_name[s['name']] += 1

    pf_total = sum(r['n'] for r in rows)
    print('PER-FRAME REACHABLE: %d read sites in %d bodies (%d of them in a loop)'
          % (pf_total, len(rows), sum(r['in_loop'] for r in rows)))

    print('\ntop read expressions, per-frame reachable set:')
    for name, n in by_name_pf.most_common(a.top):
        print('  %5d  %s' % (n, name))

    print('\ntop per-frame bodies by read count:')
    for r in sorted(rows, key=lambda r: -r['n'])[:a.top]:
        print('  %4d sites  hop%d  %s:%d %s  [%s]'
              % (r['n'], r['hop'], r['rel'], r['line'], r['func'], r['mod']))

    if a.json:
        a.json.parent.mkdir(parents=True, exist_ok=True)
        a.json.write_text(json.dumps({
            'live_files': len(winners), 'analyzed': len(files),
            'errors': [e['rel'] for e in errors],
            'total_sites_in_functions': tot_sites,
            'total_sites_module_level': tot_module,
            'per_frame_bodies': len(rows), 'per_frame_sites': pf_total,
            'by_name_all': by_name.most_common(),
            'by_name_per_frame': by_name_pf.most_common(),
            'rows': rows,
        }, indent=1), encoding='utf-8')
        print('\nwrote %s' % a.json)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
