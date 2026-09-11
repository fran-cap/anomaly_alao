"""Capture scan driven by the transformer's own edits, not by text diffing.

Text diffing cannot tell an INSERTED cache declaration from a MODIFIED local
declaration - both repros of agent-I040's bug come back as difflib 'replace'
opcodes. So ask the transformer: run it on the original, take the zero-width
insertion edits whose replacement is a cache declaration, and test each one's
name against the ancestor scopes of the function it lands in.
"""
import re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root: test the checked-out ALAO
from ast_analyzer import ASTAnalyzer
from ast_transformer import ASTTransformer

DECL = re.compile(r'^\s*local\s+([A-Za-z_]\w*)\s*=')

captures, inserts, files, errs = [], 0, 0, 0
for work in sys.argv[1:]:
    root = Path(work)
    originals = list(root.rglob('*.alao-bak')) or [
        p for p in root.rglob('*.script')] + [p for p in root.rglob('*.lua')]
    for src in originals:
        files += 1
        t = ASTTransformer()
        try:
            t.transform_file(src, backup=False, dry_run=True)
        except Exception:
            errs += 1
            continue
        an = t.analyzer
        if an is None:
            continue
        funcs = [s for s in an.scopes if s.scope_type == 'function']
        for e in t.edits:
            if e.start_char != e.end_char:
                continue                      # a replacement, not an insertion
            m = DECL.match(e.replacement)
            if not m:
                continue
            name = m.group(1)
            inserts += 1
            line = t.source.count('\n', 0, e.start_char) + 1
            best = None
            for s in funcs:
                end = s.end_line if s.end_line and s.end_line > 0 else 10 ** 9
                if s.start_line <= line <= end:
                    if best is None or s.start_line > best.start_line:
                        best = s
            if best is None:
                continue
            anc, p = set(), best.parent
            while p:
                anc |= set(p.locals)
                p = p.parent
            if name in anc and name not in set(best.locals):
                captures.append((src.name, name, best.name, line))
print('analyzed %d originals (%d errors), %d inserted declarations'
      % (files, errs, inserts))
print('CAPTURES of an ancestor-scope local: %d' % len(captures))
for c in captures[:40]:
    print('   %-44s `%s` into %s (line %d)' % c)
