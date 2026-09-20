"""Is the 110 us/frame drop the dispatcher, or is it contamination?

    py -3.12 lab/coord/coord.py queue show <id> > result.json
    py -3.12 lab/tools/i043_verify_dispatch_result.py result.json


My pre-registered rule said a drop > 10% in actor_on_update means something other
than the dispatcher moved. It fired (14.5%), so this checks the alternatives
before the result is accepted:

  1. scaling artifact  -> compare units_per_ms across all 8 runs
  2. denominator drift -> compare calls_per_frame per callback across arms
  3. contamination     -> the saving should track (calls/frame x per-dispatch
                          saving at that callback's listener count), for EVERY
                          callback, not just the ones I predicted
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from i043_callback_census import live_scripts, census  # noqa: E402

RESULT = Path(sys.argv[1] if len(sys.argv) > 1 else "result.json")
d = json.loads(RESULT.read_text(encoding="utf-8"))
prof = d["result"]["profiler"]["arms"]
b = {c["name"]: c for c in prof["baseline"]["ranking"]}
v = {c["name"]: c for c in prof["variant"]["ranking"]}

c = census(live_scripts())
reg, perm = c["register"], c["permanent"]

# interpreted per-dispatch saving from bench3 (us), K -> saved
BENCH = {4: 0.88, 12: 3.58, 20: 5.48, 60: 29.53, 73: 43.13, 125: 60.55}


def predict(K):
    ks = sorted(BENCH)
    if K <= ks[0]:
        return BENCH[ks[0]]
    if K >= ks[-1]:
        return BENCH[ks[-1]]
    for lo, hi in zip(ks, ks[1:]):
        if lo <= K <= hi:
            f = (K - lo) / (hi - lo)
            return BENCH[lo] + f * (BENCH[hi] - BENCH[lo])


print(f"{'callback':30}{'K perm':>7}{'K all':>6}{'cpf b':>8}{'cpf v':>8}"
      f"{'saved us':>10}{'per disp':>10}{'predicted':>10}")
tot_disp = tot_saved = 0.0
rows = []
for n in sorted(b, key=lambda n: -b[n]["ms_per_frame"]):
    bb, vv = b[n], v.get(n)
    if not vv:
        continue
    cpf_b, cpf_v = bb["calls_per_frame"], vv["calls_per_frame"]
    saved = (bb["ms_per_frame"] - vv["ms_per_frame"]) * 1000
    tot_disp += cpf_b
    tot_saved += saved
    if cpf_b < 0.01:
        continue
    per = saved / cpf_b
    K = perm.get(n, 0)
    rows.append((n, K, reg.get(n, 0), cpf_b, cpf_v, saved, per, predict(max(K, 1))))
for n, K, Ka, cb, cv, s, per, pr in rows:
    print(f"{n:30}{K:7d}{Ka:6d}{cb:8.3f}{cv:8.3f}{s:10.1f}{per:10.2f}{pr:10.2f}")

print(f"\ntotal make_callback dispatches per frame (baseline): {tot_disp:.2f}")
print(f"total saved per frame: {tot_saved:.1f} us")
print(f"drift in calls/frame across arms: "
      f"{max(abs(v[n]['calls_per_frame'] - b[n]['calls_per_frame']) / max(b[n]['calls_per_frame'], 1e-9) for n in b if n in v and b[n]['calls_per_frame'] > 0.1):.1%} (worst, cpf > 0.1)")
