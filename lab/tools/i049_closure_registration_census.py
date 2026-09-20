"""I-049 side question: is "a callback closure registered from a per-object
init" detectable statically?

Grep-level census over the extracted GAMMA corpus. The shape is an inline
`function(` literal passed as the callback argument of a registration call,
inside a body that has a `self` (a binder method or a `:method`). It is NOT an
ALAO pattern and is not implemented in the analyzer; this only sizes the
candidate.
"""
import re, sys, collections, os
from pathlib import Path
sys.path.insert(0, r'C:\code\GIT\anomaly_alao')
from models import detect_file_encoding

root = Path(r'C:\code\GIT\anomaly_alao\extracted\gamma')
inline = re.compile(
    r'\b(RegisterScriptCallback|AddUniqueCall|register_callback|callback_set|CreateTimeEvent)'
    r'\s*\(\s*[^,()]*,\s*function\s*\(')
BINDER = re.compile(
    r'\bfunction\s+\w*binder\w*[:.]\w+|'
    r'\b(net_spawn|reinit|__init|net_destroy)\s*=\s*function|'
    r'\w+_binder\.\w+\s*=\s*function|'
    r'\bfunction\s+\w+:\w+')
tot = 0
hits = []
for p in root.rglob('*.script'):
    try:
        t = p.read_text(encoding=detect_file_encoding(p), errors='replace')
    except Exception:
        continue
    tot += 1
    lines = t.split('\n')
    for i, l in enumerate(lines):
        if inline.search(l):
            ctx = '\n'.join(lines[max(0, i - 150):i])
            fdefs = re.findall(r'function[^\n]*\(([^)]*)\)', ctx)
            selfish = any('self' in f for f in fdefs[-8:]) or 'self' in l \
                or BINDER.search(ctx) is not None
            hits.append((str(p.relative_to(root)), i + 1, l.strip()[:100], selfish))
print('scripts scanned', tot)
print('inline-closure registrations', len(hits))
print('...in a self/binder context  ', sum(1 for h in hits if h[3]))
c = collections.Counter(h[0].split(os.sep)[0] for h in hits if h[3])
for m, n in c.most_common(15):
    print(f'  {n:4d}  {m}')
print('--- per-frame callback names among the self/binder hits ---')
n2 = collections.Counter(re.findall(r'"([a-z_]+)"', h[2])[0]
                         for h in hits if h[3] and re.findall(r'"([a-z_]+)"', h[2]))
for k, v in n2.most_common(12):
    print(f'  {v:4d}  {k}')
