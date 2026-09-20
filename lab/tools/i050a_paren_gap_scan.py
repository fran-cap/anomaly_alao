"""How often does _edit_repeated_calls decline because of `paren_depth > 0`?

I-050a's side find. `ASTTransformer._edit_repeated_calls` wants to insert
`local X = <expr>` on the line of the FIRST occurrence, so it gives up entirely
when that line sits inside an unbalanced `(` or `{` - a multi-line argument list
or table constructor. It already has the machinery for the analogous multi-line
CONDITION case, where it hoists the declaration to the line of the `if`.

Confirmed instance: demonized_ledge_grabbing.script's checkLedgeGrabbing, the
second-hottest listener in the game (142 us/frame). The analyzer reports
repeated_device x5 and repeated_db_actor x8 there and NEITHER edit lands,
because the first device() is inside

    local actorPos = vector():set(
        device().cam_pos.x,
        ...

Run this to size the gap before anyone fixes it; it re-runs the transformer's
own bail conditions rather than the whole edit pipeline, so it is an upper
bound on what a hoist-to-statement-start would recover (some of these would be
declined further down for other reasons).

    py -3.12 lab/tools/i050a_paren_gap_scan.py [corpus dir]

NOT run yet: the game lock was held for the whole of the I-050a session and a
corpus-wide analyze alongside a frametime capture wrecks both.
"""
import sys
import re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ast_analyzer import ASTAnalyzer
from ast_transformer import ASTTransformer

CORPUS = Path(sys.argv[1] if len(sys.argv) > 1 else r"C:\code\GIT\anomaly_alao\extracted\gamma")

tot = declined_paren = declined_brace = landed = other = 0
files_hit = {}

for f in sorted(CORPUS.rglob("*.script")):
    a = ASTAnalyzer()
    try:
        findings = a.analyze_file(f)
    except Exception:
        continue
    reps = [x for x in findings if x.pattern_name.startswith("repeated_")]
    if not reps:
        continue
    t = ASTTransformer()
    try:
        t.source = a.source
        t.source_lines = a.source_lines
        t._compute_line_offsets()
    except Exception:
        continue
    for fi in reps:
        tot += 1
        calls = fi.details.get("calls") or []
        scope = fi.details.get("scope")
        if not calls or not scope or getattr(scope, "scope_type", None) != "function":
            other += 1
            continue
        first = calls[0]
        brace = paren = 0
        for ln in range(scope.start_line, first.line + 1):
            ls, le = t._get_line_span(ln)
            if ls is None:
                continue
            txt = t.source[ls:le]
            if "--" in txt:
                txt = txt[: txt.find("--")]
            txt = re.sub(r'"(?:\\.|[^"\\])*"', "", txt)
            txt = re.sub(r"'(?:\\.|[^'\\])*'", "", txt)
            brace += txt.count("{") - txt.count("}")
            paren += txt.count("(") - txt.count(")")
        if brace > 0:
            declined_brace += 1
            files_hit.setdefault(f.name, []).append(("brace", fi.pattern_name, first.line))
        elif paren > 0:
            declined_paren += 1
            files_hit.setdefault(f.name, []).append(("paren", fi.pattern_name, first.line))
        else:
            landed += 1

print(f"repeated_* findings in a function scope : {tot}")
print(f"  declined: unbalanced (  (args)        : {declined_paren}")
print(f"  declined: unbalanced {{  (constructor) : {declined_brace}")
print(f"  reach the rest of the edit logic      : {landed}")
print(f"  no scope / no calls                   : {other}")
print()
for name, rows in sorted(files_hit.items(), key=lambda kv: -len(kv[1]))[:20]:
    print(f"  {name}: {rows[:4]}")
