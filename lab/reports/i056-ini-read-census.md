# I-056: `ini_file_ex:r_value` never caches `false`

Agent `agent-I056`, generation 5, branch `agent/gen5-I056` off `main` 672d813.

**Verdict: does not clear the 25 us/frame bar. No FPS run. Worth an upstream note, and the
premise needs restating for this install.**

## 1. The defect, and why it is not the defect on this machine

The vanilla class (`extracted/vanilla_db/raw/scripts/_g.script:1596-1630`) is exactly as I-050b
described it:

```lua
function ini_file_ex:r_value(s,k,typ,def)
	local cache_result = self.cache[s.."&"..k]
	if (cache_result) then                 -- truthiness test, not presence
		return cache_result
	end
	if not (self.ini:section_exist(s) and self.ini:line_exist(s,k)) then
		return def                         -- absent key: returns BEFORE caching
	end
	...
	self.cache[s.."&"..k] = v
```

Value classes that miss the cache on every read:

| class | why | example on this install |
|---|---|---|
| `false` | cached, but `if (cache_result)` is falsy | `stealth/icon = false` |
| `nil` | `v == nil and def or ...` can store nil; a nil store is also no store | bool key present with empty value |
| absent key / absent section | early `return def` happens before the store | `control/general/aim_toggle` (not in the file) |
| `def` returned for an absent key | never cached even when `def` is truthy | as above |

Everything else (`true`, non-empty strings, non-zero numbers) hits. Note `0` and `""` are truthy
in Lua, so they are *not* in the hole — this is a `false`/`nil`/absent hole, not a falsy-value one.

**But that class is dead on this install.** GAMMA runs Modded Exes (`xray_uspc.log:11`,
`Modded Exes MT-TEST version 2026.05.15`), and the loose script
`Anomaly/gamedata/scripts/_g_patches.script` — which the modded exe loads itself, no Lua file
references it — throws the whole thing away:

```
426: -- Disable caching of ini values to utilize engine functions and reduce memory footprint
427: _G.USE_INI_MEMOIZE = false
...
549: -- ini_file_ex replacement that is inherited from ini_file
550: class "new_ini_file_ex" (ini_file)
645: _G.ini_file_ex = new_ini_file_ex
```

`new_ini_file_ex:r_value` has **no cache at all**: `line_exist` then `r_string`, two engine
crossings, on every read of every value class. The same commit empties `INISYS_CACHE` and
`ini_cache` and rewires `SYS_GetParam` straight through to `ini_sys`.

So on the live stack there is no false-hole to close; there is no cache to have a hole in. The
question becomes "is it worth putting one back", which is a different question from the one I-056
asked, and the one modded exes answered "no" to on memory-footprint grounds.

`axr_main.config` is `ini_file_ex("axr_options.ltx", true)`
(`Anomaly/gamedata/scripts/axr_main.script:15`), so it is a `new_ini_file_ex`. No mod in the live
tree re-patches `ini_file_ex.r_value`, `ui_mcm.get` or `ui_options.get`.

## 2. How MCM reaches it — no second cache layer

`ui_mcm.get(id)` (`109- MCM Mod Configuration Menu - RavenAscendant/ui_mcm.script:704`) and
`ui_options.get(id)` (`ui_options.script`) are the same three lines:

```lua
local value = axr_main.config:r_value(opt_section, id, opt_val[id])
if (value ~= nil) then return value end
```

`opt_val[id]` is the value *type* (0 string / 1 bool / 2 float), not a cached value. MCM's
`opt_temp` / `opt_backup` / `opt_index` / `opt_val` tables are the editor's pending-change state
and the path index; **none of them memoizes a read**. So MCM has no cache layer of its own, with
or without the hole — every `ui_mcm.get` is a full trip to the ini reader.

Two extra costs on that path that a cache would also remove: the accessors' `s.."&"..k` key
concat, and the per-mod wrappers' `"stealth/"..key` concat. `BC_CAT` is NYI in LuaJIT 2.0, so a
loop containing either never compiles — which the bench below confirms (JIT-on and JIT-off arms
are within noise of each other).

## 3. Census — `lab/tools/i056_ini_read_census.py`

Live-winner rule and call graph reused from `lab/tools/per_frame_callgraph_census.py`
(top enabled mod > loose `Anomaly/gamedata/scripts` > `scripts.db0`). 1350 live files
(gamma 974, vanilla 315, loose 61), 0 analysis failures, 2 hops from 311 name-based +
29 registration-only per-frame seeds.

```
ini/option read sites in live scripts: 4694 inside functions, 266 at module level
PER-FRAME REACHABLE: 288 read sites in 118 bodies (17 in a loop)
```

Top expressions in the per-frame reachable set:

| n | expression | goes through |
|---:|---|---|
| 54 | `SYS_GetParam` | `ini_sys` (INISYS_CACHE, also disabled) |
| 47 | `ui_options.get` | **`ini_file_ex:r_value`** |
| 44 | `ini_sys:r_string_ex` | `ini_sys` |
| 25 | `ini_sys:r_float_ex` | `ini_sys` |
| 12 | `ini_sys:r_bool_ex` | `ini_sys` |
| 8 | `ui_mcm.get` | **`ini_file_ex:r_value`** |
| 4 | `axr_main.config:r_value` | **`ini_file_ex:r_value`** |

59 of the 288 are on the `ini_file_ex:r_value` path. **Static reachability is not a per-frame
call count**, so I classified all 59 by hand. Almost all are event-gated:

- `dynamic_news_manager.update_settings` (25 sites) — an option-change handler.
- `drx_da_main.load_settings`, `liz_inertia_expanded.initialize` — load / option-change only.
- `surge_manager:update:698`, `psi_storm_manager:update:329` — inside `if (prev_sec ~= diff_sec)`,
  i.e. once per game second, not per frame.
- `task_manager CRandomTask:update:110` — inside a task-timeout branch.
- `warfare.actor_on_update:377` — inside `if (not initialized)`.
- `fakelens.updateScope:46` — only when the scope console var flips.
- `heli_alife.update:277`, `closecaption.sound:101`, `gameplay_silent_kills.*`,
  `gameplay_disguise.expose_actor`, `smart_terrain_warfare.set_max_population`, `_g.printe:640`,
  `_g.IsStoryMode` / `IsWarfare` — all event- or error-gated.

**What actually runs every frame, standing still:**

| site | key | live value |
|---|---|---|
| `light_gem_mcm.script:39` (`light_gem`, an `actor_on_update` listener) -> `stealth_mcm.get_config("icon")` | `stealth/icon` | **`false`** |
| `zzz_player_injuries.script:1519` (inside `actor_on_update`) -> `..._mcm.get_config("TEXT_BASED_PATCH")` | `body_health_system/TEXT_BASED_PATCH` | **`false`** |
| `fluid_aim.script:40` (inside `actor_on_update`) -> `ui_options.get` | `control/general/aim_toggle` | **absent from the file** |
| `ph_sound.script:48` (`snd_source:update`, per active scripted sound source) | `sound/radio/zone` | `true` (would hit even the vanilla cache) |

So **3 reads per frame + one per active `ph_sound` source**, and the three are precisely the
classes I-056 names — which is a real (if small) confirmation of the premise's spirit.

User's live settings file:
`GAMMA/mods/G.A.M.M.A. MCM values - Rename to keep your personal changes/gamedata/configs/axr_options.ltx`
(4035 lines, written by the game): 626 `false`, 719 `true`, 87 empty, 2585 other. 16% of all
option keys are false-valued, so the class is common — it just is not read per frame.

## 4. Bench — `lab/tools/i056_bench.py`

Protocol: beam-ideas s.2 with the gen-4 correction — fresh `LuaRuntime` per arm per mode,
`jit.off()` with no arguments before the chunk loads for the interpreted arm, self-check
(compiled 1.48 ms / interpreted 6.75 ms = 4.6x, the known scalar-loop figure),
`collectgarbage()` before every timed run, best of 9, **N = 300 000 reads per run**. Run
2026-09-20 16:29-16:31 UTC with **all four locks verified free immediately before and
immediately after** (`corpus`, `extract`, `game`, `ideas`).

Four arms, all driven through the real accessor chain (`ui_options.get` / `ui_mcm.get` ->
`cfg:r_value`): `live` = `new_ini_file_ex` (no cache), `vanilla` = `_g.script`'s class with the
false-hole, `fixed` = the same with a presence test + absent-key sentinel, `cached` = a perfect
memo (ceiling).

`stealth/icon` (false-valued, 2 crossings per read on the live class):

| arm | Lua us/read (JIT on) | Lua us/read (JIT off) | crossings | total us/read @0.25 | saved vs live |
|---|---:|---:|---:|---:|---:|
| live | 0.0849 | 0.0865 | 2.00 | 0.585 | — |
| vanilla | 0.1145 | 0.1133 | 2.00 | 0.615 | **-0.030** (slower) |
| fixed | 0.0376 | 0.0380 | 0.00 | 0.038 | **+0.547** |
| cached | 0.0363 | 0.0359 | 0.00 | 0.036 | +0.549 |

`body_health_system/TEXT_BASED_PATCH` (false-valued) reproduces it: live 0.591, vanilla 0.621,
fixed 0.040, **saved 0.550 us/read**.

`control/general/aim_toggle` (**absent key**, 1 crossing — `line_exist` returns false and
`r_string` is never reached): live 0.300, vanilla 0.316, fixed 0.037, **saved 0.262 us/read**.

Three things fall out of this:

1. **JIT on and JIT off are within noise of each other in every arm.** The accessor chain
   contains `s.."&"..k` and `"stealth/"..key`; `BC_CAT` is NYI in LuaJIT 2.0, so no loop over
   this path ever compiles, in the bench or in the game. The mode question does not arise here.
2. **The vanilla class is ~0.03 us/read *slower* than the live class**, because its cache probe
   is a concat plus a table lookup that can never hit for these keys. The false-hole does not
   merely fail to help, it is a small net cost.
3. **The whole saving is the two crossings**, 0.5 of the 0.55 us at the assumed price. The
   measured Lua term is 0.04-0.09 us — small enough that a 3x error in it moves nothing.

**What the bench cannot see.** `line_exist` and `r_string` are luabind calls into the engine's
`CInifile`; here they are Lua closures over a Lua table. The measured delta is the **Lua side
only**; the crossing price is an **assumption**. I price one crossing at **0.25 us**, the
I-050b pessimistic read for a trivial getter. `r_string` is heavier than a trivial getter (it
hashes a section and a key and returns a freshly allocated Lua string), so 0.25 us is
conservative *for the idea* — it understates what a cache would save. `--crossing-us` sweeps it.

## 5. Site arithmetic

Three reads per frame standing still: two false-valued (0.550 and 0.547 us saved each) and one
absent key (0.262 us saved), all at the 0.25 us/crossing assumption.

| assumed price of one crossing | saved per frame | vs the 25 us bar |
|---|---:|---|
| 0.25 us (I-050b pessimistic read, the assumption) | **1.36 us** | 5% of the bar |
| 1.00 us (4x) | **5.1 us** | 20% |
| 2.50 us (10x) | **12.6 us** | 50%, still below the 15 us "profiler can tell" band |
| 3.30 us (13x) | 16.5 us | first value that reaches 15 us |

Add `ph_sound`'s `sound/radio/zone` per active scripted sound source and it moves by one read per
source — and that key is `true`, so it hits even the vanilla cache and a fix buys nothing there.

Against a measured ~310 us/frame script budget in the gen-4 combined arm and a **25 us/frame**
bar. It does not clear, and it does not reach the 15 us "only the profiler can tell" band either
unless a single `r_string` crossing costs more than 3.3 us — 13x the number the team agreed to
price crossings at, and 4x what I-050b's *whole* 59-crossing removal implies (16 us / 59 =
0.27 us per crossing, measured in game). No FPS run requested.

## 6. What this is worth anyway

1. **Upstream note, yes.** The `if (cache_result)` test is a real bug in vanilla Anomaly 1.5.3's
   `_g.script`; on a non-modded-exes install every read of a false-valued or absent option is an
   uncached engine trip. It costs almost nothing per frame on the code that exists today, so it is
   a correctness/tidiness note, not a performance PR. Cheapest honest fix is a presence test
   (`if self.cache[key] ~= nil`) plus a sentinel for the absent-key early return. Anomaly's own
   `w_value` already writes the cache, so invalidation is unaffected; `remove_line` does **not**
   clear the cache, which is a second, smaller hole in the same function family and should go in
   the same note.
2. **The finding that matters more is the premise inversion:** on a modded-exes install (which is
   every GAMMA install) there is no ini memoization at all, by design. Any future idea that
   assumes `ini_file_ex`, `SYS_GetParam` or `ini_sys` memoizes is wrong on this stack.
3. **By-product, sized but not mine:** the `SYS_GetParam` / `ini_sys` path is ~10-19 reads per
   frame standing still (`weapon_cover_tilt.actor_on_update` ~7 with a weapon out,
   `fluid_aim` 2-4, `ssfx_weapons_dof` 1-4, `arti_jamming` up to 4), i.e. ~5-10 us/frame at the
   same assumption. Bigger than I-056, still under the bar, and the fix is "put back the cache
   modded exes removed", which has a memory cost the team has not priced.
4. **The one live case where MCM reads could be big, and it is invisible to the profiler:**
   `visual_memory_manager.get_visible_value` is an *engine*-invoked global (vanilla hook, no Lua
   caller anywhere in the live tree), and the Stealth Overhaul copy does **8**
   `stealth_mcm.get_config` calls per invocation — one per NPC visibility evaluation. Standing
   still with no NPCs that is zero; in a crowded scene it is 8 uncached reads per NPC-eval, and
   because it is not a `SendScriptCallback` listener the I-048 profiler never sees it. Sizing it
   needs I-053's busy-hub save and an instrument that covers engine-called globals. Only 2 of the
   8 keys (`michiko_patch`, `debugx`) are false-valued, so even under the vanilla class the
   false-hole would account for a quarter of it.
