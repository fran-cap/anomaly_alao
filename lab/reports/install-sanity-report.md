# S.T.A.L.K.E.R. GAMMA — install sanity report

- Target: `D:\GOG_Games\Gamma\S.T.A.L.K.E.R. GAMMA` (GOG build, installed 2026-09-10)
- Inspected: 2026-09-10, read-only. Nothing written inside the install; game and ModOrganizer.exe were not launched.
- Author: sanity agent

---

## Verdict

**OK with caveats.** The install is complete and internally consistent. Nothing blocks running the game. Two items must be handled before automated benchmarks produce trustworthy numbers.

Concrete issues:

- **MSI Afterburner and RivaTuner Statistics Server are running right now** (`MSIAfterburner` pid 7644, `RTSS` pid 9828, `RTSSHooksLoader64` pid 11132, `EncoderServer` pid 11112). RTSS injects into the game and can cap or re-pace frames. It is a confound for every run. Decide deliberately: use RTSS as the frametime logger, or shut it down for clean runs. Do not leave it ambiguous.
- **No frame-capture tool suited to scripted benchmarking is installed.** PresentMon, OCAT and CapFrameX are all absent from Program Files, PATH and winget. Only RTSS and Afterburner are present. A PresentMon deployment is needed, or the harness must drive RTSS frametime logs.
- **Profile-local settings are enabled** (`LocalSettings=true` in `profiles/G.A.M.M.A/settings.ini`) but `profiles/G.A.M.M.A/user.ltx` does not exist yet. On first MO2 launch, MO2 copies `Anomaly/appdata/user.ltx` into the profile and serves that copy through the VFS. After the first launch, the file the harness must edit is `GAMMA/profiles/G.A.M.M.A/user.ltx`, not `Anomaly/appdata/user.ltx`. Editing the wrong one will silently do nothing.
- **`vid_mode` is 1920x1080 while the primary display is 2560x1440 at 144 Hz.** Not a fault, but every baseline number will be at 1080p unless changed. `rs_screenmode` is `fullscreen` (exclusive) and `rs_v_sync` is off.
- **`Anomaly/tools/checksums.md5` does not match the eight `Anomaly*.exe` files.** This is a stale baseline, not corruption: `VerifiedDX11.exe` and every DLL in `bin/` match that file exactly, and the eight engine exes carry PE link timestamps of 2026-05-15, long after the checksum list was written. GAMMA ships its own rebuilt engine binaries. Treat `checksums.md5` as unusable for engine verification.
- **ReShade is not actually installed.** `bin/` contains a dozen ReShade preset files and a stale `dxgi.log`, but no `dxgi.dll`. The log's own first line references `F:\Anomaly\Anomaly-1.5.1\bin\dxgi.dll`, a path from the packager's machine. The only `dxgi.dll` on disk belongs to the `Reshade Disabler` mod, which is disabled. Net effect: no ReShade overlay at runtime. Good for benchmark hygiene, worth knowing if someone expects the presets to apply.
- **Free space on D: is 595 GB of 931 GB.** Comfortable, but the install already occupies about 146 GB because the Grok installer keeps a second full copy of 419 mods under `.Grok's Modpack Installer` (27 GB) alongside `downloads` (26 GB).

Nothing else looked broken. Mod list and mods on disk match exactly, no empty mod directories, 20 of 20 sampled GOG-listed files present, no missing DLLs, `LongPathsEnabled` is 1.

---

## 1. Engine

`D:\GOG_Games\Gamma\S.T.A.L.K.E.R. GAMMA\Anomaly\bin`

| Executable | Bytes | PE timestamp (UTC) |
|---|---:|---|
| AnomalyDX8.exe | 22,576,640 | 2026-05-15 05:26:58 |
| AnomalyDX8AVX.exe | 22,476,800 | 2026-05-15 05:26:52 |
| AnomalyDX9.exe | 22,735,872 | 2026-05-15 05:27:02 |
| AnomalyDX9AVX.exe | 22,630,400 | 2026-05-15 05:27:27 |
| AnomalyDX10.exe | 23,025,152 | 2026-05-15 05:26:59 |
| AnomalyDX10AVX.exe | 22,918,656 | 2026-05-15 05:26:58 |
| **AnomalyDX11.exe** | 23,259,648 | 2026-05-15 05:27:21 |
| **AnomalyDX11AVX.exe** | 23,154,176 | 2026-05-15 05:27:07 |
| VerifiedDX11.exe | 44,763,648 | 2024-09-23 01:23:57 |

The GAMMA-recommended binary is **`AnomalyDX11AVX.exe`**, reached through `moshortcut://:Anomaly (DX11-AVX)`. The host supports AVX and AVX2, so this executable is valid here.

`VerifiedDX11.exe` is the odd one out: much larger, built in 2024, and the only exe matching the shipped checksum list. It is the verification build, not the play build.

DLLs present in `bin/`: `discord_game_sdk.dll`, `icudt65.dll`, `icuuc65.dll`, `soft_oal.dll`, `tbb.dll`. That is the complete expected set for Anomaly 1.5.3, which statically links the x-ray modules rather than shipping `xrCore.dll` and its siblings. Four of the five match `tools/checksums.md5`. `soft_oal.dll` differs because GAMMA replaces it with a newer OpenAL Soft build and keeps the original as `soft_oal.dll.back`, whose MD5 `883083b84f2d080240954facc1769d80` matches the checksum list exactly. Nothing is missing.

### Configuration

`commandline.txt` contains a single flag:

```
-smap2048
```

`AnomalyLauncher.cfg`:

```
DX11
AVX
0
2560
1067
NODBG
1
NOSNDFIX
NOSNDPREFETCH
```

The 2560x1067 pair is the launcher's own window geometry, not the game resolution. The game resolution lives in `user.ltx`.

`fsgame.ltx` is stock Anomaly 1.5.3. `$app_data_root$` resolves to `Anomaly\appdata\`, logs to `appdata\logs\`, saves to `appdata\savedgames\` as `*.scop` and `*.scoc`.

### appdata

`Anomaly/appdata/user.ltx` exists, 341 lines. `logs/`, `savedgames/` and `shaders_cache/` all exist and are **empty**, confirming the game has never been run on this machine.

Key render and performance settings:

| Setting | Value |
|---|---|
| renderer | renderer_r4 (DX11) |
| vid_mode | 1920x1080 |
| rs_screenmode | fullscreen |
| rs_v_sync | off |
| rs_refresh_60hz | off |
| texture_lod | 0 |
| r__geometry_lod | 1.5 |
| r__detail_radius | 110 |
| r__detail_density | 0.25 |
| r__tf_aniso | 16 |
| r__tf_mipbias | -0.5 |
| r__supersample | 1 |
| r2_sun_quality | st_opt_medium |
| r2_sunshafts_mode | volumetric |
| r2_sunshafts_quality | st_opt_high |
| r2_ssao / r2_ssao_mode | st_opt_off / disabled |
| r2_gi | off |
| r2_aa / r2_smaa | off / off |
| r2_soft_particles | off |
| r2_soft_water | on |
| r2_dof_enable | off |
| r2_mblur_enabled | off |
| r2_detail_bump | off |
| r2_steep_parallax | off |
| rs_skeleton_update | 32 |
| particle_update_mod | 0.5 |
| lua_gcstep | 718 |
| ph_iterations / ph_frequency | 18 / 100.0 |
| snd_device / snd_targets / snd_cache_size | OpenAL Soft / 256 / 256 |
| snd_efx | off |
| cfg_load | Atmos_Summer |
| g_game_difficulty | gd_veteran |

`ssfx_motionblur` is set to `(12.0, 0.1, 0.0, 0.0)`, so Screen Space Shaders motion blur is active at runtime even though the engine's own `r2_mblur` is off.

---

## 2. Mod Organizer 2

`GAMMA/ModOrganizer.ini`, version 2.5.2, portable (`portable.txt` present).

- `gameName=STALKER Anomaly`, served by the bundled `plugins/basic_games/games/game_stalkeranomaly.py`.
- `gamePath=D:/GOG_Games/Gamma/S.T.A.L.K.E.R. GAMMA/Anomaly` — correct, matches the actual install.
- `selected_profile=G.A.M.M.A` — the intended profile.
- `first_start=false`, and window geometry is saved, so MO2 itself has been opened at least once by the installer.

Ten custom executables are defined, all with correct absolute paths into this install:

1. Anomaly Launcher
2. **Anomaly (DX11-AVX)**
3. Anomaly (DX11)
4. Anomaly (DX10-AVX)
5. Anomaly (DX10)
6. Anomaly (DX9-AVX)
7. Anomaly (DX9)
8. Anomaly (DX8-AVX)
9. Anomaly (DX8)
10. Explore Virtual Folder

One cosmetic note: `MainWindow_executablesListBox_index=3` means the GUI dropdown is parked on the DX10-AVX entry, not DX11-AVX. Launching via the `moshortcut://:Anomaly (DX11-AVX)` argument bypasses the dropdown entirely, so this does not affect scripted launches.

The GOG play task in `goggame-1595922314.info` has a wrinkle worth knowing. The **primary** task passes `"moshortcut://:Anomaly (DX11)"`, without AVX. The DX11-AVX variant is a secondary task. Both carry `RUNASADMIN`. The contract's launch line is the AVX one.

### Profiles

Two profiles exist: `G.A.M.M.A` and `GAMMA Custom`.

| Profile | Entries | Enabled | Disabled | Separators |
|---|---:|---:|---:|---:|
| G.A.M.M.A (selected) | 797 | 605 | 192 | 29 |
| GAMMA Custom | 619 | 309 | 310 | 25 |

`profiles/G.A.M.M.A/settings.ini`:

```
[General]
LocalSaves=false
LocalSettings=true
```

`LocalSaves=false` means saves land in `Anomaly/appdata/savedgames`, shared across profiles. `LocalSettings=true` means `user.ltx` is profile-local, but the profile copy does not exist yet. See the caveat in the verdict.

`archives.txt` is empty, `lockedorder.txt` is empty, `initweaks.ini` only sets `bInvalidateOlderFiles=1`. All normal for Anomaly.

`profiles/G.A.M.M.A/` also holds `modlist.txt.2022_11_21_21_06_36`, a much shorter backup list. Harmless leftover.

<details>
<summary>G.A.M.M.A load order — first 15 entries (highest priority first)</summary>

```
# This file was automatically generated by Mod Organizer.
-Tiskar's Attachment Alignment Fixes
-Teivaz Gunslinger Knives Quick Melee
-Solarint's JSRS v5 GAMMA Patch
-Reshade Disabler
-No logs
-Momopate's Anti Savecum
-Meatchunk's prefetcher for G.A.M.M.A
-Longreed's fixed artefacts spawns under maps
-Lifestorock's Bleak Fall Redux Performance
-Jaku's Improved Shaders
-Grulag's Dead Bushes
-G.A.M.M.A. NPCs cannot see through foliage
-G.A.M.M.A. January PDA crash fix
-G.A.M.M.A. Hip quest rewrite
```

The very top of the file is a block of disabled alternates, which is how the modpack ships.
</details>

<details>
<summary>G.A.M.M.A load order — last 15 entries (lowest priority)</summary>

```
+16- Hit Effects - Wepl
+15- Voiced Actor - DesmanMetzger
+13- Quieter Wood Boxes Breaking - cringeybabey
+12- PDA Radio Extended - Starcry_
+11- Preblowout Murder - Ethylia
+10- Better sound - Grokitach
+9- Exo Servomotor Sounds - HarukaSai
+8- Better Merc voicelines - YankeeGolf
+7- EFT Jump and Land SFX - HarukaSai
+6- EFT Aim Rattle - Qudix
+5- Anomaly Radio Extended - DesmanMetzger
+4- Injury Audio Extended - DesmanMetzger
+3- Soundscape Overhaul - Solarint
+2- Main Menu Theme - Deathcard Cabin - Grokitach
+1- Audio_separator
```
</details>

<details>
<summary>Highest-priority enabled mods (top 25)</summary>

```
+G.A.M.M.A. End of List_separator
+335- Auto-Stacking Items - Zatura
+G.A.M.M.A. Disabled (DO NOT ACTIVATE)_separator
+Maybelline's Restore XCV phantoms
+Oleh's Tasks Crash Fixes
+Oleh's Package Loot Rework
+Scrunkly's Collection of Mods
+478- Custom iTheon's PDA Taskboard - lizzardman
+463- Tasks QoL Pack - Serious
+SaloEater's Use Package Only Once
+SaloEater's Remember HFE's Stove Slot
+SaloEater's Double Click to Use Tools
+G.A.M.M.A. Accurate Defense Values
+Anomaly 1.5.3 Shaders Fix
+G.A.M.M.A. New Main Menu
+Momopate's Barrel Condition Effects Display
+Alternative Addons & Patches_separator
+G.A.M.M.A. Inspect on double tap F disabler
+G.A.M.M.A. Unjam Reload on the same key
+Realistic Magazines_separator
+G.A.M.M.A. Deer Hunter as 338 Federal
+442- Expanded & Fixed Maps - HailTheMonolith
+438- Sights and Optics Retexture - Meowie
+437- Weighted NPC Random Loadouts - SD
+444- Thick Russian Reticles - Napolemon
```
</details>

### mods directory

- 804 entries, of which 797 are mod directories and 7 are stray non-mod files (`README.md`, `LICENSE`, `good_weapons.txt`, `to_mipmap.txt`, and three balance spreadsheets). MO2 ignores them.
- Total size: **77 GB**.
- **Every one of the 797 mod names in `modlist.txt` exists on disk, and every mod directory on disk appears in `modlist.txt`.** No orphans in either direction.
- **Zero empty mod directories.**
- 480 zero-byte files across all mods. Spot-checking shows these are intentional: marker files such as `grok_better_sound_enabled.txt` and `ES_GGX_by_LVutner.txt`, instruction stubs such as `DO NOT INSTALL FOMOD FOLDER.txt`, and deliberate blank overrides under `207- Mags Redux .../gamedata/configs/icon_override/magazines2/`. Nothing alarming.
- A number of mods lack `meta.ini`. These are the modpack's own manually-placed addons rather than Nexus downloads, which is expected for a Grok install and does not affect the VFS.
- 34 directories have no `gamedata/` subfolder. Almost all are `*_separator` markers, which by design contain nothing. Two real exceptions: `25- High Res Loading Screens - Bazingarrey` and `456- FDDA Redone Fixes - Kute`.
- Five mods ship an `appdata/` folder, all shader packs delivering Atmospherics weather presets: `188- Enhanced Shaders`, `290- Atmospherics ... Latest`, `Jaku's Improved Shaders`, `Jaku's Improved Shaders v1.2`, `Shaders cumulative pack for GAMMA`. **None of them contains a `user.ltx`**, so nothing shadows the engine settings file through the VFS.

### Duplicates

Two mod names appear twice with different numeric prefixes:

- `290-` and `296- Atmospherics Shaders Weathers and Reshade - Hippobot` — **both disabled**. The enabled one is `290- Atmospherics Shaders Weathers and Reshade Latest - Hippobot`.
- `304-` and `310- Dark Signal Weather and Ambiance Audio - Shrike` — `304-` enabled, `310-` disabled.

Both are intentional alternates, correctly resolved. `190- Screen Space Shaders` similarly ships in four versioned variants, of which one is active.

### overwrite, logs, crashDumps

All three are **empty**. Consistent with a fresh install that has not been played.

### downloads

26 GB of source archives, more than 400 files. Kept by the installer, not needed at runtime.

---

## 3. Grok's Modpack Installer

`GAMMA/.Grok's Modpack Installer`, 27 GB.

| Item | Value |
|---|---|
| `version.txt` | **920** |
| `G.A.M.M.A_definition_version.txt` | **920** |
| Author | Grokitach |
| Launcher | `G.A.M.M.A. Launcher.exe` (3.9 MB) |

Version and definition version agree at 920, meaning the installed modpack matches the definition set it was built from. No pending update state.

`modpack_maker_metadata.txt`:

```
author = Grokitach
modpack name = G.A.M.M.A.
modpack maker list = modpack_data/modpack_maker_list.txt
ModOrganier2 modlist = modpack_data/modlist.txt
modpack additional files = modpack_addons
```

Addons list: `mods.txt` holds **491 lines**, one per ModDB addon, each carrying download URL, author, display name, archive filename and an MD5. `mods_to_check.txt` is byte-identical to `mods.txt`, meaning the installer's verification pass completed with no outstanding entries.

`mirrors.txt` records six DBolical mirrors, all flagged `True`.

`G.A.M.M.A/modpack_addons/` holds **423 directories** and `modpack_patches/` holds 4. **419 of the 423 addon directories also exist under `GAMMA/mods/`**, so the installer keeps a complete second copy of nearly every addon. That is by design and is the main reason the install is 146 GB rather than roughly 90 GB.

**No installer log files exist.** A recursive search for `*.log` in the installer tree matched only mod content files whose names happen to contain the letters "log": `dialog.script`, various `*_logic.ltx`, `ui_furniture_light_dialog.xml` and similar. The installer left no run log behind.

---

## 4. Integrity

`goggame-galaxyFileList.ini` declares `files_counter=163752` and spans 163,919 lines. A pseudorandom sample of 20 path entries was drawn and checked for existence and size.

**Result: 20 present, 0 missing.**

<details>
<summary>Sampled files</summary>

```
OK     2644606  GAMMA\.Grok's Modpack Installer\...\76- Boomsticks and Sharpsticks - Mich\...\wpn_sr2_m1_e0t2_hud.ogf
OK         107  GAMMA\mods\438- Sights and Optics Retexture - Meowie\...\wpn_1p29_4x_bump.thm
OK      699216  GAMMA\mods\31- Fixed Vanilla Models and Textures - Blackgrowl\...\act_faces_4_01_bump.dds
OK      101759  GAMMA\.Grok's Modpack Installer\...\Oleh's Weapons Sounds Tweaks and Fixes\...\grot_reload.ogg
OK       10789  GAMMA\mods\305- Dux Characters Kit Voices Pack - Demonized\...\attack_3.ogg
OK       59598  GAMMA\.Grok's Modpack Installer\...\76- Boomsticks and Sharpsticks - Mich\...\ak74_shoot_13.ogg
OK       14419  GAMMA\mods\4- Injury Audio Extended - DesmanMetzger\...\death_8.ogg
OK      105194  GAMMA\.Grok's Modpack Installer\...\Oleh's Miscellaneous Sound Improvements\...\inv_cooking_stove.ogg
OK       97332  GAMMA\mods\312- Gunslinger Guns for Anomaly\...\upsgun.omf
OK      353300  GAMMA\mods\31- Fixed Vanilla Models and Textures - Blackgrowl\...\stalker_freedom2b_mask.ogf
OK      138618  GAMMA\.Grok's Modpack Installer\...\Oleh's Weapons Sounds Tweaks and Fixes\...\mg_muzzle_mono_far2.ogg
OK        3971  GAMMA\.Grok's Modpack Installer\...\G.A.M.M.A. No Copyrighted Music\...\Bandits_radio_33.ogg
OK     1758099  GAMMA\mods\351- M249 Reanimation - NickolasNikova\...\wpn_m249_hud.ogf
OK       19987  GAMMA\.Grok's Modpack Installer\...\Oleh's Miscellaneous Sound Improvements\...\water-07.ogg
OK     1398256  GAMMA\mods\32- FVM Nosorogs models - Blackgrowl\...\helm_nosorog_duty.dds
OK     1667558  GAMMA\mods\410- 3DSS for GAMMA\...\wpn_sig550_ekp8_hud.ogf
OK      373872  GAMMA\mods\31- Fixed Vanilla Models and Textures - Blackgrowl\...\stalker_neutral_a.ogf
OK         323  GAMMA\mods\76- BAS patch 28-12-21 (2nd 76) - Mich\...\mka_sh_alt_fire1.anm
OK     1489793  GAMMA\.Grok's Modpack Installer\...\76- BAS patch 28-12-21 (2nd 76) - Mich\...\wpn_pl15_tan_hud.ogf
OK      265989  GAMMA\mods\312- Gunslinger Guns for Anomaly\...\wpn_glockauto_hud_animation.omf
```
</details>

Engine binaries were additionally MD5-verified against `Anomaly/tools/checksums.md5`. `VerifiedDX11.exe` and four of five DLLs match byte for byte. `soft_oal.dll` is a deliberate GAMMA replacement whose original is preserved as `soft_oal.dll.back` and matches. The eight `Anomaly*.exe` files do not match, because GAMMA ships rebuilt engine binaries newer than the checksum list. See the verdict.

`goglog.ini` records that the installer set `HKLM\SYSTEM\CurrentControlSet\Control\FileSystem\LongPathsEnabled` to 1. **Verified: the registry value is currently 1.** This matters, because the MO2 VFS builds very deep paths across 797 mods.

`path.ini` and `path.txt` both point at `D:\GOG_Games\Gamma\S.T.A.L.K.E.R. GAMMA`. Correct.

### Disk space

| Path | Size |
|---|---:|
| `GAMMA/mods` | 77 GB |
| `GAMMA/.Grok's Modpack Installer` | 27 GB |
| `GAMMA/downloads` | 26 GB |
| `Anomaly` | 17 GB |
| **Whole install** | **146 GB** |

Drive D: 931 GB total, 336 GB used, **595 GB free (37% used)**.

---

## 5. Host

| Component | Value |
|---|---|
| CPU | AMD Ryzen 7 9800X3D, 8 cores / 16 threads, 4700 MHz max |
| RAM | 31.7 GB (two 16 GB Corsair modules at 4800 MHz) |
| GPU | NVIDIA GeForce RTX 5080, 16303 MiB |
| Driver | 610.88 (WDDM 32.0.16.1088), dated 2026-07-21 |
| VBIOS | 98.03.3b.c0.68 |
| AVX / AVX2 | **Both supported** |
| DirectX | 4.09.00.0904 |
| OS | Windows 10 Pro, build 19045 (10.0.19045) |

The 9800X3D with its large L3 cache is close to ideal for this engine, which is heavily single-thread and cache bound.

### Displays

Three monitors, all 2560x1440:

| Device | Primary | Bounds |
|---|---|---|
| `\\.\DISPLAY1` | yes | 0,0 2560x1440 at 144 Hz |
| `\\.\DISPLAY2` | no | 2560,0 2560x1440 |
| `\\.\DISPLAY3` | no | -2560,0 2560x1440 |

A multi-monitor setup with exclusive fullscreen can produce focus-loss stutter if anything steals focus mid-run. The harness should avoid touching other windows while a run is in progress.

### Benchmark tooling

| Tool | Status |
|---|---|
| PresentMon | **absent** (not in Program Files, not on PATH, not in winget) |
| OCAT | **absent** |
| CapFrameX | **absent** |
| MSI Afterburner | present, 4.6.6.16757, `C:\Program Files (x86)\MSI Afterburner` — **running** |
| RivaTuner Statistics Server | present, 7.3.5.28314, `C:\Program Files (x86)\RivaTuner Statistics Server` — **running** |

### Python

`py -3.12 -c "import psutil"` **works**. Python 3.12.10 at `C:\Program Files\Python312\python.exe`, psutil 7.2.2.

Other interpreters available: 3.13 (Store), 3.10 (user), 3.9 (machine).

---

## 6. Searches

### "aalo"

**No matches anywhere in the install.** Checked both filenames (recursive, case-insensitive) and file contents across `.ltx`, `.script`, `.txt`, `.xml`, `.ini`, `.md` and `.cfg`. The lab namespace is clean and does not collide with anything shipped.

### "beam" in mod names

**No matches.** Neither `GAMMA/mods` nor the installer's `modpack_addons` contains a mod with "beam" in its name.

### A-Life knobs

Exactly **two** files on disk define an `[alife]` section, and both are MO2 mods:

- `GAMMA/mods/G.A.M.M.A. Alife optimization/gamedata/configs/alife.ltx` — **ENABLED** (line 315 of the G.A.M.M.A modlist)
- `GAMMA/mods/Turn this on if you stutter/gamedata/configs/alife.ltx` — **DISABLED** (line 196)

The base game's own `alife.ltx` is not loose on disk. It lives packed inside `Anomaly/db/configs/configs.db0`. `Anomaly/gamedata/configs/` holds only nine small loose override files, none A-Life related.

The active values, from the enabled mod:

| Knob | Active (Alife optimization) | Alternate (Turn this on if you stutter) |
|---|---:|---:|
| `schedule_min` (ms) | 170 | 1 |
| `schedule_max` (ms) | 470 | 1 |
| `process_time` (us) | 4320 | 900 |
| `objects_per_update` | 3 | 20 |
| `update_monster_factor` | 0.3 | 0.1 |
| `time_factor` | 6 | 6 |
| `normal_time_factor` | 10 | 10 |
| `switch_distance` (m) | 450 | 1500 |
| `switch_factor` | 0.1 | 0.1 |
| `auto_switch` | **true** | false |
| `auto_switch_timer` (ms) | 10000 | 10000 |
| `auto_switch_distance_start` (m) | 1250 | 1250 |
| `auto_switch_distance_normal` (m) | 450 | 325 |
| `autosave_interval` | 00:15:00 | 00:15:00 |
| `delay_autosave_interval` | 00:00:30 | 00:00:30 |

Two things matter for experiment design.

1. **`auto_switch = true` is active.** `switch_distance` is not static during play. It is forced to `auto_switch_distance_start` (1250 m) on load, then drops to `auto_switch_distance_normal` (450 m) after `auto_switch_timer` (10 s), driven from `xr_patch.script`. Any benchmark that starts measuring inside the first ten seconds after a load will be measuring a different A-Life radius than one that starts at thirty seconds. Warm-up discipline is mandatory.
2. **The disabled alternate sits at higher priority than the enabled one** (line 196 versus line 315; the lower line number wins in MO2). Enabling `Turn this on if you stutter` will completely override `G.A.M.M.A. Alife optimization` rather than merge with it. That makes the pair a clean, ready-made A/B: one mod toggle swaps the entire `[alife]` block between a conservative 450 m / 3-objects profile and an aggressive 1500 m / 20-objects profile.

No other `.ltx` anywhere in `GAMMA/mods` or `Anomaly/gamedata` defines `switch_distance`, `online_radius`, `objects_per_update`, `process_time` or `update_monster_factor`.

`online_radius` specifically does not appear in any loose `.ltx`. It remains at whatever the packed base config sets, and would need `Anomaly/tools/db_unpacker.bat` run against `configs.db0` to read.

---

## 7. Things that look broken

Checked and **clear**:

- No missing DLLs. `bin/` carries exactly the five DLLs Anomaly 1.5.3 expects, all verified against the shipped checksum list except the deliberate OpenAL replacement.
- No empty mod directories.
- No mod in the load order missing from disk, and no mod on disk missing from the load order.
- MO2 `gamePath` and both GOG path files point at the real install location.
- The MO2 Anomaly game plugin (`game_stalkeranomaly.py`) is present, so MO2 recognises the game type.
- `LongPathsEnabled` is 1.
- `overwrite/`, `logs/` and `crashDumps/` are empty. No prior crashes to explain.

Checked and **noted**:

- Stale `checksums.md5` versus the rebuilt engine exes. Explained above, not corruption.
- Stale `bin/dxgi.log` referencing `F:\Anomaly\Anomaly-1.5.1\`, a packager artefact, alongside a dozen orphaned ReShade preset files with no ReShade binary to consume them.
- 480 zero-byte files, all intentional markers or blank overrides.
- Two duplicate mod name pairs, both correctly resolved in the load order.
- 419 addons duplicated between `mods/` and the installer's `modpack_addons/`, plus 26 GB of retained downloads. Recoverable space if it is ever needed, but do not delete `modpack_addons` while the Grok launcher may still be run.
- `MainWindow_executablesListBox_index=3` parks the MO2 GUI dropdown on DX10-AVX. Irrelevant to `moshortcut://` launches.
