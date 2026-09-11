"""I-021 census: repeated engine reads/calls on the same receiver inside one body.

Read-only. Counts, per corpus:
  * every `db.<field>` dot read, including the ones the analyzer currently
    suppresses because they are the receiver of a method call
  * bodies with >= N reads of the same `db.<field>`
  * bodies with >= N argument-less calls of the same `recv:method()` where the
    receiver is never assigned inside that body
  * `level.object_by_id(<same local>)` repeats (the I-022 overlap)

Split by per-frame-ness and by the I-013 jit_mode classifier.
"""
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import ast_analyzer as A  # noqa: E402
from ast_analyzer import ASTAnalyzer, Invoke, Index, Name  # noqa: E402

REPEAT_MIN = 3

METHOD_CANDIDATES = {
    'position', 'id', 'section', 'clsid', 'story_id', 'name', 'direction',
    'level_vertex_id', 'game_vertex_id', 'health', 'parent', 'best_enemy',
    'best_danger', 'motivation_action_manager', 'alive', 'character_community',
    'profile_name', 'section_name', 'object', 'active_item', 'active_slot',
}


class _AllDbIndexes(frozenset):
    """Permissive stand-in for EXPENSIVE_INDEXES so the census sees every db.*"""

    def __contains__(self, item):
        return isinstance(item, str) and item.startswith('db.')


def census(files, unsuppress=True):
    out = {
        'db_reads': Counter(),
        'db_reads_as_receiver': Counter(),
        'db_bodies': Counter(),
        'db_bodies_pf': Counter(),
        'db_jit': Counter(),
        'm_sites': Counter(),
        'm_bodies': Counter(),
        'm_bodies_pf': Counter(),
        'm_jit': Counter(),
        'm_bodies_recv_assigned': Counter(),
        'obi_bodies': 0,
        'obi_bodies_pf': 0,
        'files': 0,
        'errors': 0,
    }
    saved = A.EXPENSIVE_INDEXES
    A.EXPENSIVE_INDEXES = _AllDbIndexes()
    try:
        for fp in files:
            an = ASTAnalyzer()
            try:
                an.analyze_file(fp)
            except Exception:
                out['errors'] += 1
                continue
            out['files'] += 1
            pf = {id(cb.scope) for cb in an.per_frame_callbacks}

            def fscope(sc):
                return an._find_function_scope(sc)

            def jmode(sc):
                return an._jit_mode_for_scope(sc)

            # db.<field> reads: recorded indexes plus suppressed invoke receivers
            db = defaultdict(list)
            for idx in an.indexes:
                fs = fscope(idx.scope)
                if fs and idx.module == 'db':
                    db[(id(fs), idx.full_name)].append(fs)
                    out['db_reads'][idx.full_name] += 1
            for call in an.calls:
                node = call.node
                if not (isinstance(node, Invoke) and isinstance(node.source, Index)):
                    continue
                src = node.source
                if not (isinstance(src.value, Name) and isinstance(src.idx, Name)):
                    continue
                if src.value.id != 'db':
                    continue
                full = 'db.%s' % src.idx.id
                out['db_reads'][full] += 1
                out['db_reads_as_receiver'][full] += 1
                fs = fscope(call.scope)
                if fs and unsuppress:
                    db[(id(fs), full)].append(fs)
            for (_, full), scopes in db.items():
                if len(scopes) >= REPEAT_MIN:
                    fs = scopes[0]
                    out['db_bodies'][full] += 1
                    if id(fs) in pf:
                        out['db_bodies_pf'][full] += 1
                    out['db_jit'][(full, jmode(fs))] += 1

            # repeated argument-less methods on one receiver
            buckets = defaultdict(list)
            for call in an.calls:
                if ':' not in call.full_name or call.args:
                    continue
                if call.func not in METHOD_CANDIDATES:
                    continue
                fs = fscope(call.scope)
                if not fs:
                    continue
                out['m_sites'][call.func] += 1
                buckets[(id(fs), call.full_name)].append((call, fs))
            for entries in buckets.values():
                if len(entries) < REPEAT_MIN:
                    continue
                call, fs = entries[0]
                if _receiver_written(an, call.module, fs):
                    out['m_bodies_recv_assigned'][call.func] += 1
                    continue
                out['m_bodies'][call.func] += 1
                if id(fs) in pf:
                    out['m_bodies_pf'][call.func] += 1
                out['m_jit'][(call.func, jmode(fs))] += 1

            # level.object_by_id(<same local>)
            obi = defaultdict(list)
            for call in an.calls:
                if call.full_name != 'level.object_by_id' or len(call.args) != 1:
                    continue
                a = call.args[0]
                if not isinstance(a, Name):
                    continue
                fs = fscope(call.scope)
                if fs:
                    obi[(id(fs), a.id)].append(fs)
            for scopes in obi.values():
                if len(scopes) >= 2:
                    out['obi_bodies'] += 1
                    if id(scopes[0]) in pf:
                        out['obi_bodies_pf'] += 1
    finally:
        A.EXPENSIVE_INDEXES = saved
    return out


def _receiver_written(an, recv, fs):
    """True if `recv` (or its first dotted component) is assigned inside fs."""
    base = recv.split('.')[0].split(':')[0].split('[')[0]
    for asg in an.assigns:
        tgt = asg.target or ''
        if tgt == recv or tgt == base or tgt.startswith(recv + '.'):
            if an._find_function_scope(asg.scope) is fs:
                return True
    return False


def main():
    for corpus in sys.argv[1:]:
        root = Path(corpus)
        files = sorted(list(root.rglob('*.script')) + list(root.rglob('*.lua')))
        r = census(files)
        print('=' * 72)
        print('%s  %d files, %d errors, repeat threshold %d'
              % (corpus, r['files'], r['errors'], REPEAT_MIN))
        print('\n-- db.<field> dot reads (total / as method receiver) --')
        for k, v in r['db_reads'].most_common(14):
            print('  %-26s %6d   receiver %5d' % (k, v, r['db_reads_as_receiver'][k]))
        print('\n-- bodies with >=%d reads of the same db.<field> --' % REPEAT_MIN)
        for k, v in r['db_bodies'].most_common(14):
            print('  %-26s %6d   per-frame %4d' % (k, v, r['db_bodies_pf'][k]))
        print('\n-- argument-less candidate method call sites --')
        for k, v in r['m_sites'].most_common(18):
            print('  :%-24s %6d' % (k + '()', v))
        print('\n-- bodies with >=%d same recv:method(), receiver not assigned --'
              % REPEAT_MIN)
        for k, v in r['m_bodies'].most_common(18):
            print('  :%-24s %6d   per-frame %4d   (skipped, recv assigned: %d)'
                  % (k + '()', v, r['m_bodies_pf'][k], r['m_bodies_recv_assigned'][k]))
        print('\n-- jit_mode of those method bodies --')
        for k, v in sorted(r['m_jit'].items(), key=lambda x: -x[1])[:18]:
            print('  %-22s %-12s %5d' % (k[0], k[1], v))
        print('\n-- jit_mode of those db.<field> bodies --')
        for k, v in sorted(r['db_jit'].items(), key=lambda x: -x[1])[:18]:
            print('  %-22s %-12s %5d' % (k[0], k[1], v))
        print('\nlevel.object_by_id(<same local>) repeat bodies: %d (per-frame %d)'
              % (r['obi_bodies'], r['obi_bodies_pf']))


if __name__ == '__main__':
    main()
