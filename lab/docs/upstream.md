# Upstream: what came out of the lab that belongs to someone else

**Status of everything here: NOT PUBLISHED.** Nothing has been opened, pushed, forked, posted or
messaged anywhere. This file is the record of what could go upstream, to whom, and what evidence
backs it, so that publishing is a copy-paste job on the day the user decides to do it.

Written 2026-09-20 after the gen-4 run. Numbers are script us/frame from the I-048 profiler,
4 x 120 s per arm, round 1 of each arm dropped, save `gammabaseline`, **standing still in a quiet
spot** (single scene; a moving scene is I-053). Details in `beam-ideas.md` section 11.

| # | What | Whose code | Measured | Ready? |
|---|---|---|---:|---|
| 1 | `make_callback` cached dispatch order | modded exes (`themrdemonized/xray-monolith`), lines from Kutez' PR #339 | -89 (stock GAMMA), -105 (on full ALAO) | mod + PR draft ready |
| 2 | `drx_da_main.script` shared throttled dispatcher | Dynamic Anomalies Overhaul, Demonized | -222 | patcher + tests ready, no write-up yet |
| 3 | `demonized_ledge_grabbing.script` cold-camera guard | Ledge Grabbing, Demonized | -107 | patcher + tests ready, one open question |
| 4 | `zzz_player_injuries.script` crossing cut | Body Health System (Grokitach) / Voiced Actor Refined (SaloEater) copy | -16, inside noise | not worth sending alone |
| 5 | `ini_file_ex:r_value` never caches `false` | Anomaly `_g.script` | unmeasured | finding only |

All four patches together: 714.8 -> 309.5 us/frame (-405, -57%), fps avg +3.7%, 1% low +13.5%
(`20260919-232541-I-054-d26d45`).

## 1. `axr_main.make_callback`: cache the sorted dispatch order

- **Target:** `themrdemonized/xray-monolith`, default branch `all-in-one-vs2022-wpo`,
  `gamedata/scripts/axr_main.script`. The patched region is byte-identical between upstream and the loose
  GAMMA copy (sha256 `0ded82c3...`). Full homework, the diff and the PR text: **`lab/docs/i051-upstream-pr-draft.md`**.
- **What it does:** `make_callback` re-sorts the listener table through `spairs` (the `hspairs` min-heap from
  `_g_patches.script`) on every dispatch. The patch keeps a sorted array per callback name and invalidates it
  on register / unregister.
- **Evidence:** `20260919-211347-I-051-21408e` stock GAMMA 713.5 -> 624.4 (-89, -12.5%, no overlap);
  `20260919-192703-I-043-3f2729` on full ALAO 712.3 -> 607.3 (-105). 59 differential tests against the real
  `axr_main.script` (`tests/test_i051_dispatch_delivery.py`).
- **Say this honestly in the PR:** it is not semantically identical under mid-pass register/unregister. The
  shipped order in that window is pointer-order nondeterministic (14 distinct orders in 30 replays of one
  scenario); the patch gives the declared priority order every time. With no mutation mid-pass it is
  bit-identical (600/600). Live exposure found: one listener, two sites (`drx_da_main.script:1679/1697`).
- **Interim delivery:** `lab/mods/alao-make-callback-dispatch/`, a monkey-patch mod that replaces no file
  and redistributes no Anomaly code (A-vs-B bench 0.98-1.03x, so the conflict-free form costs nothing).
  Could go on ModDB as a standalone addon independent of the PR.
- **Open before sending:** ask the maintainers whether they want an `axr_main.script` edit or a restored
  `axr_main_patches.script` in the PR-#339 style. `lab/tools/i051_upstream_patch.py --src upstream --diff`
  regenerates the diff.

## 2. Dynamic Anomalies Overhaul: one `actor_on_update` listener instead of one per anomaly

- **Target:** Demonized, DAO (GAMMA ships `Dynamic_Anomalies_Overhaul.37`),
  https://www.moddb.com/mods/stalker-anomaly/addons/dynamic-anomalies-overhaul-dao-read-description-please .
  File `gamedata/scripts/drx_da_main.script`, `bind_anomaly_field.net_spawn` (the closure at :2734 in the ALAO copy).
- **What it does:** every anomaly binder registers its own `actor_on_update` closure whose steady state is one
  throttle compare; 265-353 of them are live on the test save. The patch keeps the binders in a
  registration-ordered array walked by a single listener, with the body lifted unchanged. Throttles are not
  re-phased; mid-pass register/unregister behaves as under `hspairs`. `register_callback` /
  `unregister_callback` / `unregister_callbacks()` keep working.
- **Evidence:** `20260919-210108-I-049-35365e` 742.2 -> 519.8 (-222, -30%, no overlap). Bench K=353:
  286.5 -> 33.3 us interpreted, 52.8 -> 7.9 compiled. 13 differential tests (`lab/tests/test_i049_dispatcher.py`).
- **Say this honestly:** the 353 listeners used to hold 353 slots in `actor_on_update`'s dispatch order and now
  share one. Each touches only its own binder, so nothing observable should depend on it.
- **Produce the file to send:** `py -3.12 lab/coord/i049_drx_da_patch.py --pristine <out>` patches the
  untouched mod copy (compiles, CRLF clean). The repo carries the patcher, never the mod's script.
- **Missing:** a short write-up / diff for the author. Not drafted yet.

## 3. Ledge Grabbing: skip the scan when nothing it reads has moved

- **Target:** Demonized, Ledge Grabbing (GAMMA ships `Ledge_Grabbing.11`),
  https://www.moddb.com/mods/stalker-anomaly/addons/modded-exes-ledge-grabbing-mantling .
  File `gamedata/scripts/demonized_ledge_grabbing.script`, `checkLedgeGrabbing`.
- **The finding, which is worth sending even without the patch:** the mod's own nothing-moved early-out is
  switched off by `alternativeClimbDetection`, which defaults to true, and `throttleCheck` defaults to 0. So on
  flat ground, standing still, it builds 15 `geometry_ray` objects and casts 15 rays every frame (~142 us).
- **What the patch does:** a cold-camera guard in front of the mod's own (camera pos/dir, actor y, settings
  generation, reset counter), plus one ray object per scan instead of 15.
- **Evidence:** `20260919-211043-I-050-025a5a` 710.7 -> 604.0 (-107, -15%, no overlap). 25 differential tests
  (`lab/tests/test_i050a_ledge_patch.py`). FDDA Redone's monkey-patch of `checkClimbPrecondition` survives.
- **Say this honestly:** the guard uses tolerances (1e-4 m, 1e-5 per direction component), so it is
  bounded-stale, not bit-exact; it assumes static geometry does not change within a level; the win is
  standing-still only (moving, only the ray hoist applies, unmeasured); the variant arm drifted 563 -> 611 over
  four rounds and that is unexplained. The author may prefer his own fix (e.g. include camera pitch in the
  existing guard).
- **Produce the file:** `py -3.12 lab/coord/i050a_ledge_patch.py --src <pristine mod file> <out>`.
- **Missing:** the moving-scene number (I-053) and a write-up.

## 4. Player injuries HUD: not worth sending alone

104 -> 45 Lua->C crossings per frame with an identical observable trace (27 tests), but -16 us with
overlapping arms. Two things in it are real bugs the authors might want regardless: a `hidehudonce` latch
declared `local` inside the per-frame function (it never latches, :1575 in the live copy), and a dead
`get_movement_speed()` read every frame. Report: `lab/reports/i050b-player-injuries.md`.

## 5. `_g.script` `ini_file_ex:r_value` never caches `false`

It caches with `if (cache_result) then`, so any option whose stored value is `false` (or nil) pays
`section_exist` + `line_exist` + `r_string` on every read, GAMMA-wide. Unmeasured; I-056 is the idea that
prices it. Same upstream home as item 1 if it turns out to matter.

## Rules for whoever publishes

- The user decides whether, where and under whose name. Agents prepare drafts only.
- Quote us/frame first, the protocol with it, and the single-scene caveat every time.
- Do not redistribute other authors' scripts; send diffs or patcher output to the author, or ship
  file-less monkey patches.
