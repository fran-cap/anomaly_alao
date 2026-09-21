# alao-vmm-cache — Stealth Overhaul's MCM reads, cached (I-066)

One script, `gamedata/scripts/zzz_alao_vmm_cache.script`. It replaces no game file and
swaps exactly one function at `on_game_start`: `stealth_mcm.get_config`. (It also wraps
`ui_mcm.set`, only to notice writes to `stealth/...` ids; `HOOK_SET = false` turns that off.)

## What is slow

`visual_memory_manager.get_visible_value` is called by the **engine**, once per NPC
visibility evaluation. Stealth Overhaul's version asks MCM for its options on every call:

| read | when | used for |
|---|---|---|
| `michiko_patch` | always | night luminosity curve, night hours |
| `memory` | always | memory factor |
| `distance`, `velocity`, `weight`, `luminocity` | always | the four multipliers of the total |
| `debugx` | always | the debug printout gate |
| `crouch` **or** `low_crouch` | only while crouched (short-circuit) | crouch multiplier |

7 reads standing, 8 crouched. Each is `stealth_mcm.get_config(key)` -> `"stealth/"..key` ->
`ui_mcm.get(id)` -> `axr_main.config:r_value("mcm", id, typ)` -> `line_exist` + `r_string` in
the engine, then `tonumber` or four string compares. GAMMA's Modded Exes `_g_patches.script`
replaces `ini_file_ex` with an **uncached** class on purpose, so that is 14-16 engine
crossings and 7-8 fresh strings per NPC per check. `light_gem_mcm` reads `icon` the same
way once per frame.

Measured in game by the I-062 profiler (`eng` axis): 20.3 us per call, 1.68 calls per frame
standing still on `gammabaseline` = 34 us/frame. It is per NPC evaluation, so it grows with
the crowd.

## What this does

`get_config(key)` becomes one table lookup. A miss goes to the original and the answer is
remembered; `nil` never is, so a bad key still prints MCM's complaint every time, and the
original's return list is handed through untouched (a bad path returns *zero* values today,
and `tostring(get_config("typo"))` stays the error it is).

The cache is dropped, and refilled lazily through the original, on:

| event | why |
|---|---|
| `on_option_change` | MCM's Apply sends it right after writing `axr_options.ltx`, same call stack; the vanilla options menu sends it too |
| `ui_mcm.set("stealth/...", v)` | the only other writer of the `mcm` section. Nobody in the GAMMA modlist calls it with a `stealth/` id; MCM's savefile storage would if somebody registered `stealth` with it |
| `load_state` | belt and braces; the script VM is new on every load, so the table starts empty anyway |

So a changed option is seen by the very next `get_visible_value` call, same as today. Not
seen: a script writing `stealth/...` straight into `axr_main.config` with `w_value`. Nothing
in the modlist does.

## Why the accessor and not `get_visible_value` itself

The function reads eight file-locals of `visual_memory_manager.script` (`jacketmult`,
`pewpewmult`, `get_camo_k`, `vision_memory`, `marked`, `actor_marked`, `summ`, `is_r1`) that
no other script can reach, and a GAMMA modlist carries three different copies of the file
(`290- Atmospherics...` wins, `G.A.M.M.A. Stealth Crash Fix` and `45- Stealth Overhaul` are
shadowed). A re-implementation would have to fork all that state and would go stale against
whichever copy wins next update. Patching the accessor removes the same MCM cost, works for
all three copies, and makes the return value bit-identical by construction.

Does a monkey patch even take for an engine-called function? For *this* patch the question
does not arise: `get_visible_value` looks `stealth_mcm.get_config` up in the module table on
every call, and nobody holds it in a local (the overlay builder checks). For the record, a
patch of `get_visible_value` itself would take too: `CVisualMemoryManager::get_visible_value`
does `ai().script_engine().functor("visual_memory_manager.get_visible_value", funct)` on
every call, no static, and the I-062 profiler's wrapper (installed the same way) counted
1.68 calls a frame.

## What is left in the function (not done here, and why)

Per call, after this patch: ~27-32 engine crossings. About 11 of them are frame-invariant
(`level.get_time_hours/minutes`, two `weather.get_value_vector` each allocating a vector,
two `item_in_slot`, `db.actor:id()`, `get_total_weight` + `math.exp`, two `IsMoveState`) and
could be memoised per frame, but only calls 2..N of a frame would save, that is ~2 us/frame
standing and ~2.7 us per extra visible NPC, and it needs the fork described above. Left alone.

## Numbers

`py -3.12 lab/tools/i066_bench.py` (protocol in its docstring; crossings counted and priced
at 0.25 us, engine-side cost gets no microbench):

| | stock | patched | saved |
|---|---:|---:|---:|
| one `get_config`, interpreted | 0.60 us (2 crossings) | 0.017 us (0) | 0.58 us |
| one `get_visible_value`, interpreted, who = actor | 12.8 us (46 crossings) | 8.7 us (32) | **4.1 us** |
| one `get_visible_value`, interpreted, who = NPC | 11.5 us (41) | 7.4 us (27) | **4.1 us** |

The model prices the stock call at 12.8 us where the game measured 20.3, so crossings cost
more than 0.25 in there; scaled by that ratio the saving is ~6.5 us per call. Standing still
at 1.68 calls per frame: **7-11 us/frame, under the 25 us bar**. It crosses the bar at about
4-6 evaluations per frame. The run in `lab/coord/i066-request.json` measures it.

## Log lines

```
[alao_vmm_cache 1.0] patched stealth_mcm.get_config (read by visual_memory_manager.get_visible_value, which is present, and light_gem_mcm); ui_mcm.set wrapped for stealth/ ids
[alao_vmm_cache 1.0] cached stealth/michiko_patch = false          (one per key, at most ten per refresh)
[alao_vmm_cache 1.0] cache refresh #1 (on_option_change): dropped 8 values after 51234 hits / 8 misses, next reads go to ui_mcm.get
```

Also possible: `kill switch is off, nothing patched`, `stealth_mcm.get_config not found (no
Stealth Overhaul?), nothing patched`, `... already carries ..., nothing patched`.

## Switches

`ENABLED` (kill switch), `HOOK_SET`, `LOG_FILLS`, all constants at the top of the script.
Safe to remove at any time; nothing is saved.

Tests: `lab/tests/test_i066_vmm_cache.py` (diff-execution of a 1500-step scripted session
against all three shipped copies of `visual_memory_manager.script` plus the ALAO rewrite,
JIT on and off).
