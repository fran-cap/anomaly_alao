# Cached callback dispatch (`axr_main.make_callback`)

One script. It makes every scripted callback in the game cheaper by not re-sorting the
listener table on every dispatch. Measured saving on a GAMMA 0.9.4 install, standing
still on one save: **110 microseconds of script time per frame, 15.5% of all script
time**. It did **not** move the frame rate on that scene — see "What it does not buy".

Built by the ALAO lab (ideas I-043 / I-051). Everything below is reproducible from the
repo: `lab/reports/make-callback-dispatch.md` has the measurement, `tests/` has the
differential tests.

---

## What it changes

`_g.SendScriptCallback(name, ...)` is how every mod in the game fires a callback, and it
funnels into one function:

```lua
function make_callback(name,...)
    if (intercepts[name]) then
        for func_or_userdata, v in spairs(intercepts[name], sort_func_values_ascend) do
            ...
```

`intercepts[name]` maps each registered listener to its priority index (the Kutez
callback-priority system, [xray-monolith PR #339][pr339]). `spairs` sorts it. And in
GAMMA `spairs` is not the `table.sort` one from `_g.script` — the loose `_g_patches.script`
overwrites `_G.spairs`, and for any order function other than `sort_func_keys_ascend` it
returns `hspairs`, a min-heap. So **every single dispatch** allocates a K-element keys
array, a `safe_order` closure and an iterator closure, heapifies in O(K), and then pays an
O(log K) sift-down per listener where every comparison is two nested Lua calls — to walk
a table that only changes when somebody registers or unregisters.

`actor_on_update` has 73-125 listeners and fires once per frame. There are 7.5 dispatches
per frame in total.

This mod keeps the sorted order in an array and rebuilds it when the table changes.

## What it does **not** change

Priority order, arguments, table/userdata listeners called as `t[name](t, ...)`, the three
`printf` + `callstack()` error paths, and the fact that a listener which errors aborts the
whole pass (no `pcall` is added — that would be a second change).

The subtle one, which a naive "just cache the sorted array" patch gets wrong: `hspairs`
re-reads `t[key]` on every step and skips an entry whose value has gone nil, so **a
listener unregistered by an earlier listener in the same pass never fires**. The patch
reproduces that with a `t[f] ~= nil` guard.

## The one behaviour difference, stated plainly

`hspairs` drains a heap, and its comparator reads the table's *current* values. The moment
a listener registers or unregisters anything for the callback that is *currently
dispatching*, the heap invariant is broken and the order of the listeners that have not
run yet becomes an artifact of the heap rather than the priority order anybody declared.
This mod, walking a frozen sorted array, keeps the priority order. Because the order
changes *when* a listener is visited, and a listener unregistered mid-pass is skipped only
if that happened before its visit, the set of listeners that ran in that one pass can
differ too. From the next dispatch on the two agree again, and the log lines printed are
identical in every case.

Measured over 600 randomised scenarios per mode, built to churn mid-pass as hard as
possible — an upper bound on pathology, not a frequency in a real frame:

| listeners churn mid-pass | identical | reordered | different set |
|---|---|---|---|
| no mutation during the pass | **600** | 0 | 0 |
| unregister during the pass | 480 | 96 | 24 |
| register during the pass | 140 | 460 | 0 |
| both | 251 | 294 | 55 |

Matching the shipped order bit for bit would mean reimplementing the heap, i.e. keeping
the work this mod exists to remove. If you are on a setup where a listener's relative
position inside a single frame is load-bearing, do not use this.

## Measured

**Instrument:** a script-side profiler (ALAO idea I-048) that wraps `make_callback` and
reads the engine's `profile_timer`, so the number is script time, not frame time.

**Protocol:** 4 launches per arm, 120 s each, first round of each arm dropped (it reads
~10% high), standing still on a fixed save, arms differing in exactly one file. Queue item
`20260919-192703-I-043-3f2729`.

| | baseline | with the patch | delta |
|---|---|---|---|
| script time | 712.3 us/frame | 607.3 us/frame | **-110.4 us (-15.45%)** |
| `actor_on_update` | 667.9 us | 571.2 us | -96.7 us (-14.5%) |
| per-run means | 700 / 713 / 716 / 720 | 588 / 593 / 607 / 622 | no overlap |
| fps avg | 213.31 | 210.38 | -1.37% |
| fps 1% low | 163.71 | 169.30 | +3.41% |
| frametime p99 | 5.71 ms | 5.59 ms | -2.1% |

That run had the full ALAO rewrite set under both arms. A stock, non-ALAO baseline is
queued separately.

### What it does not buy

**The frame rate did not move.** -1.37% average, +3.41% on the 1% low, -2.1% p99 — all
inside the +-2% noise band of that harness, and the average and the low disagree in sign.
110 us off a ~4750 us frame is 2.3%, at the edge of what the harness can resolve. So: a
large, reproducible, mechanistically explained saving in script time, and no demonstrated
frame-rate win on that scene. Both halves of that sentence are the result. One save, one
spot, standing still; a busy scene with many NPCs online would change the mix.

## Install

Mod Organizer 2: drop this folder into `mods/` (or install the archive), enable it. There
are no load-order requirements — it replaces no file, so nothing can conflict with it on
priority. The only ordering that matters is *script* load order, and the `zzz_` prefix
puts it after everything that registers callbacks, which is what it wants.

Uninstall: disable or delete it. It touches nothing persistent — no save data, no ltx, no
engine settings.

**Verify it took.** The log gets one line at game start:

```
ALAO|callback_dispatch|v1.0|installed
```

Anything other than `installed` means it declined and left the game exactly as it was.
That is deliberate: it needs `debug.getupvalue` to reach `axr_main`'s file-local
`intercepts` table, and if it cannot, it does nothing rather than guess.

## Compatibility

* **Replaces no game file**, so it cannot conflict with any mod on file priority. That is
  the whole reason it is written as a monkey-patch instead of a modified `axr_main.script`.
* It swaps four fields on the `axr_main` module table: `make_callback`, `callback_add`,
  `callback_set`, `callback_unset`. A censusing of the 1350 script files a live GAMMA
  profile actually loads found **5 references** to those four functions — 4 in `_g.script`
  (`RegisterScriptCallback` / `UnregisterScriptCallback` / `SendScriptCallback` /
  `AddScriptCallback`) and 1 in a weapon mod — and **all 5 call them through the module
  table at call time**. Nothing caches one in a local, so the swap catches every caller.
* Another script that also replaces `axr_main.make_callback` will win or lose depending on
  which one's `on_game_start` runs last. If you run one, check the log line above.
* Anything that writes into `intercepts` **without** going through `callback_set` /
  `callback_unset` (only possible via `debug.getupvalue`, and the only thing known to do
  it is ALAO's own profiler in per-listener mode) must call
  `zzz_alao_callback_dispatch.invalidate()` afterwards, or the affected listeners go quiet
  until the next registration. Nothing crashes; they are simply skipped.
* Requires the `debug` library, which Anomaly ships.

## Tested

`tests/test_i051_dispatch_delivery.py` in the ALAO repo builds two `axr_main` module
tables from the **real** `Anomaly/gamedata/scripts/axr_main.script` in one LuaJIT 2.0
runtime, installs this exact script onto one of them, and replays the same scenario
through both: dispatch order and arguments, table listeners, listeners unregistering
themselves and each other, registration during a dispatch, re-entrant dispatch, the
`hspairs` one-listener special case, empty and unknown callbacks, an erroring listener,
the install-declines path, composition with the profiler, and 600-scenario randomised
differential fuzzes. `tests/test_make_callback_dispatch.py` covers the algorithm against a
transcription of `hspairs`. 72 tests.

## Licence / provenance

This mod contains no code from Anomaly or GAMMA. The lines it replaces at runtime come
from `axr_main.script`; the dispatcher it installs was written for this mod. The priority
system it has to stay compatible with is [xray-monolith PR #339][pr339] by Kutez.

[pr339]: https://github.com/themrdemonized/xray-monolith/pull/339
