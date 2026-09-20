"""I-044: what `--fix-debug` actually does to every debug call in a corpus, and
which of those comment-outs look like a correctness risk.

Commenting a statement out does not just remove a log line - it removes the
evaluation of its arguments too. That is the whole performance point, and also
the whole risk: if an argument calls something that mutates state, the mutation
goes away with the log line.

Per site this reports:
  * commented / declined (by running the real ASTTransformer in dry-run with
    --fix --fix-debug and diffing the line)
  * side_effect_arg: an argument subtree contains a call that is not on the
    small pure-getter allow-list
  * sole_branch_stmt: the call is the ONLY statement of an if/elseif/else
    branch (commenting it leaves an empty branch - legal Lua, but if the
    transformer ever commented the `if` line too it would not be)
  * multiline: the call spans more than one source line

Read-only (dry_run=True, no backups, nothing written).

    py -3.12 lab/tools/i044_fixdebug_risk.py <corpus root> [--json out.json] [--self-test]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from ast_analyzer import ASTAnalyzer, DEBUG_FUNCTIONS  # noqa: E402
from ast_transformer import ASTTransformer  # noqa: E402

from luaparser import ast as lua_ast  # noqa: E402
from luaparser.astnodes import (  # noqa: E402
    Call, Invoke, If, ElseIf, Block, String, Number, Nil, Name,
)

# calls that cannot mutate anything: safe to lose with the log line
PURE_CALLS = {
    'tostring', 'tonumber', 'type', 'select', 'rawequal', 'rawlen',
    'string.format', 'string.sub', 'string.len', 'string.rep', 'string.lower',
    'string.upper', 'string.gsub', 'string.find', 'string.match', 'string.byte',
    'math.floor', 'math.ceil', 'math.abs', 'math.min', 'math.max', 'math.sqrt',
    'table.concat', 'os.clock', 'os.date', 'os.time', 'tostring_vec',
    'vec_to_str', 'game.translate_string', 'time_global', 'game.time',
    'game.get_game_time', 'level.name', 'device', 'db.actor',
}
# argument-less engine getters: reading state, not changing it
PURE_METHODS = {
    'name', 'id', 'section', 'section_name', 'clsid', 'position', 'health',
    'alive', 'level_vertex_id', 'game_vertex_id', 'story_id', 'count',
    'get_squad_community', 'character_community', 'profile_name', 'x', 'y', 'z',
    'get_remaining_uses', 'ammo_get_count', 'condition', 'direction',
}


def _src(node):
    try:
        return lua_ast.to_lua_source(node)
    except Exception:
        return '<?>'


def _callee(node):
    f = getattr(node, 'func', None)
    return _src(f) if f is not None else '<?>'


def _is_debug_call(node):
    if not isinstance(node, Call):
        return False
    nm = _callee(node)
    if nm.startswith('math.'):
        return False
    return nm.split('.')[-1].split(':')[-1] in DEBUG_FUNCTIONS


def _side_effect_calls(args):
    """Calls inside the argument list that are not obviously pure."""
    bad = []
    for a in args:
        for n in lua_ast.walk(a):
            if isinstance(n, Call):
                nm = _callee(n)
                if nm not in PURE_CALLS and nm.split('.')[-1] not in PURE_CALLS:
                    bad.append(nm)
            elif isinstance(n, Invoke):
                m = getattr(n.func, 'id', None) or _src(n.func)
                if m not in PURE_METHODS:
                    bad.append(':' + m)
    return bad


def _statements(block_or_node):
    if block_or_node is None:
        return []
    if isinstance(block_or_node, Block):
        return list(block_or_node.body or [])
    if isinstance(block_or_node, list):
        return list(block_or_node)
    return [block_or_node]


def _collect(tree):
    """Every debug call, with the flags we care about."""
    sole = set()          # id() of debug call nodes that are the only stmt of a branch
    for n in lua_ast.walk(tree):
        if isinstance(n, (If, ElseIf)):
            for branch in (getattr(n, 'body', None), getattr(n, 'orelse', None)):
                if isinstance(branch, (If, ElseIf)):
                    continue        # that is an elseif, handled on its own
                stmts = _statements(branch)
                if len(stmts) == 1 and _is_debug_call(stmts[0]):
                    sole.add(id(stmts[0]))
    out = []
    for n in lua_ast.walk(tree):
        if _is_debug_call(n):
            out.append((n, id(n) in sole))
    return out


def scan_file(fp: Path):
    tr = ASTTransformer()
    try:
        modified, new_content, _ = tr.transform_file(
            fp, backup=False, dry_run=True, fix_debug=True, verify_compile=False)
    except Exception as exc:
        return None, f'{type(exc).__name__}: {exc}'
    src = tr.source
    old_lines = src.splitlines()
    # Which lines --fix-debug commented out. Taken from the edit list, not from
    # a line-by-line diff of the output: other edits insert lines (cache decls),
    # which shifts every line number below them and made the first version of
    # this tool call half the corpus "declined".  priority=200 is used by
    # _edit_debug_statement and nothing else.
    commented_lines = set()
    for e in tr.edits:
        if e.priority == 200:
            commented_lines.add(src[:e.start_char].count('\n') + 1)
    try:
        tree = lua_ast.parse(src)
    except Exception as exc:
        return None, f'reparse: {type(exc).__name__}'
    recs = []
    for node, sole in _collect(tree):
        line = getattr(node, 'line', 0) or 0
        if not (0 < line <= len(old_lines)):
            continue
        old = old_lines[line - 1]
        commented = line in commented_lines
        se = _side_effect_calls(getattr(node, 'args', []) or [])
        end = getattr(node, 'stop_line', None) or line
        recs.append({
            'file': fp.name,
            'line': line,
            'callee': _callee(node),
            'commented': commented,
            'sole_branch_stmt': sole,
            'side_effect_calls': se[:4],
            'multiline': bool(end and end > line),
            'src': old.strip()[:160],
        })
    return recs, None


def self_test():
    """Known positives: the scanner must flag both shapes on a file that has them."""
    import tempfile
    lua = """
function f(t, o)
    printf("popped %s", table.remove(t))
    if o then
        printf("only statement")
    end
    printf("pure %s", o:name())
    local x = log("bound")
    return x
end
"""
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / 'known_positive.script'
        p.write_text(lua, encoding='utf-8')
        recs, err = scan_file(p)
        assert err is None, err
        by_line = {r['line']: r for r in recs}
        se = [r for r in recs if r['side_effect_calls']]
        sole = [r for r in recs if r['sole_branch_stmt']]
        pure = [r for r in recs if r['callee'] == 'printf' and not r['side_effect_calls']]
        ok = (len(se) == 1 and se[0]['side_effect_calls'][0].endswith('table.remove')
              and len(sole) == 1 and len(pure) >= 2)
        print('self-test sites:', json.dumps(recs, indent=1))
        print('self-test:', 'PASS' if ok else 'FAIL')
        return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('corpus', type=Path, nargs='?')
    ap.add_argument('--json', type=Path)
    ap.add_argument('--self-test', action='store_true')
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    files = sorted(a.corpus.rglob('*.script')) + sorted(a.corpus.rglob('*.lua'))
    all_recs, errors = [], Counter()
    for fp in files:
        recs, err = scan_file(fp)
        if err:
            errors[err.split(':')[0]] += 1
            continue
        all_recs.extend(recs)
    c = Counter()
    for r in all_recs:
        c['sites'] += 1
        c['commented' if r['commented'] else 'declined'] += 1
        if r['commented'] and r['side_effect_calls']:
            c['COMMENTED with non-pure call in args'] += 1
        if r['commented'] and r['sole_branch_stmt']:
            c['COMMENTED and sole statement of a branch'] += 1
        if r['commented'] and r['multiline']:
            c['COMMENTED multi-line statement'] += 1
    print(f'{a.corpus}: {len(files)} files, {sum(errors.values())} errors {dict(errors)}')
    for k, v in c.most_common():
        print(f'  {k:<42} {v:6d}')
    print('\n-- non-pure calls appearing in commented-out debug args --')
    risky = Counter()
    for r in all_recs:
        if r['commented']:
            for nm in r['side_effect_calls']:
                risky[nm] += 1
    for k, v in risky.most_common(25):
        print(f'  {k:<40} {v:5d}')
    if a.json:
        a.json.write_text(json.dumps(
            {'counts': dict(c), 'errors': dict(errors), 'sites': all_recs}, indent=1),
            encoding='utf-8')
        print(f'wrote {a.json}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
