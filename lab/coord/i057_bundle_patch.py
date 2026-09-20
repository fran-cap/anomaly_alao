"""I-057 track B: the remaining small per-frame listeners.

From the moving-scene listener ranking with all gen-4 patches in
(`20260920-110221-I-053-5895a5`, beam 11.1), what is left under the two big
ones, in script us/frame:

    fluid_aim:33          15.0
    liz_inertia:196       11.5
    light_gem_mcm:20      11.5
    actor_effects:1640     8.0      (its Update_Fog leg)
    sound_ambient:277      8.0
    battery_warning:39     7.0

This patcher covers the four where a semantics-preserving cut exists and is
worth writing down.  Each file gets its own function, each substitution asserts
its anchor bytes, and every behavioural non-identity is named here.

--------------------------------------------------------------------- fluid_aim
Per frame with a firearm out, the body asks the engine for the *same static ltx
data* up to four times and re-reads a settings option:

  * `wpn:section()` x4 (once for the class test, twice for the two kind tests,
    once inside `switch_weapon_state`) -> one per frame.
  * `SYS_GetParam(0, section, "class"/"kind")` x3-4 -> memoised per section.
    These read system.ltx, which is static for the run; the cache is keyed on
    the section string, so a weapon whose section changes with an upgrade gets
    its own entry.
  * `ui_options.get("control/general/aim_toggle")`.  On this install that is
    NOT a table read: `_g_patches.script` replaces `ini_file_ex` outright, so
    `axr_main.config:r_value` is two engine crossings, the option is absent
    from the config, and `ui_options.get` then falls through to
    `str_opt_explode` plus `get_console_cmd(1, "wpn_aim_toggle")` - every
    frame, and again on every fire/zoom key event.  Cached, invalidated on
    `on_option_change`.
    NON-IDENTITY: changing `wpn_aim_toggle` straight from the console, without
    going through the options screen (which sends `on_option_change`), is not
    seen until the next option change or level load.  Through the UI it is.

--------------------------------------------------------------- actor_effects
`Update_Fog` runs every frame, and whenever breathing fog is not currently
being drawn it calls `HUD_fog(false)`, which walks all 4 x 10 fog statics and
does `st:SetWndRect(Frect():set(0,0,0,0))` on each - 40 `Frect()` allocations
and 40 UI calls per frame to re-zero rectangles that are already zero.  The
`enable_breathing_fog` path above it already has a `fog_removed` latch; this
one has none.  The patch latches the loop only.  The three state writes
(`fog_val`, `fog_cycle = time_global()`, `fog_last_phase`) still run every
frame, deliberately: `fog_cycle` is the start of the respiratory cycle, and
latching it too would make the fog resume at an arbitrary phase instead of at 0.
NON-IDENTITY: none unless something outside this file resizes those statics.
Nothing in the enabled modlist mentions `hud_blur*` except actor_effects.

------------------------------------------------------------- battery_warning
`batt_checker` tests three engine menu predicates (`main_hud_shown`,
`ActorMenu.get_pda_menu():IsShown()`, `actor_menu.inventory_opened()`) *before*
its own 10-second throttle, so on 999 frames out of 1000 it pays for them and
then returns at the throttle anyway.  Both tests are side-effect-free and both
branches return without touching state, so swapping them is observationally
identical.  The detector-changed check above them still runs every frame.
NON-IDENTITY: none.

------------------------------------------------------------- light_gem_mcm
`light_gem` calls `get_hud()` and `hud:GetCustomStatic("mp_warm_up")` every
frame purely to find out whether it still has to build the gem - `cs` is used
nowhere else.  After the build, `gem` is non-nil forever.  The patch skips the
lookup once `gem` exists.  It also stops re-asserting `gem:Show(v)` when `v`
has not changed, and moves the `item_in_slot(9)` (flashlight) read into the
`elseif` that is its only reader.
NON-IDENTITY: if something removed the `mp_warm_up` custom static the original
would rebuild the gem and this will not.  `mp_warm_up` appears in exactly one
script in the enabled modlist (this one) and in no db script.  And if something
other than this function hid or showed the gem, the original would re-assert it
next frame and this will not; nothing else references it.

Not patched, and why: `liz_inertia_expanded` (11.5 us) is five
`game.play_hud_anm` engine calls whose arguments change every frame - the only
cut is hoisting `is_overriden(val.mask)`, called twice per lerp per frame, worth
~6 Lua calls; `sound_ambient` (8 us) is eight `xr_sound` calls that could be
skipped when all four volumes are unchanged, but they are shared looped-sound
channels and "nothing else sets this volume" is a claim I could not establish
from the scripts alone.

Usage:
    py -3.12 i057_bundle_patch.py <outdir> [--pristine]

Writes <outdir>/gamedata/scripts/<name>.script for each file it patches.
Sources default to the `gen4-all-b` copy where ALAO rewrote the file and the
live winning mod copy otherwise; `--pristine` forces the live copies.
"""
import argparse
import sys
from pathlib import Path

MODS = Path(r"D:\GOG_Games\Gamma\S.T.A.L.K.E.R. GAMMA\GAMMA\mods")
GEN4 = Path(r"C:\code\GIT\anomaly_alao\lab\coord\overlays\gen4-all-b\gamedata\scripts")

LIVE = {
    "fluid_aim.script": MODS / "65- Fluid Aim - Skieppy/gamedata/scripts/fluid_aim.script",
    "actor_effects.script": MODS / "G.A.M.M.A. 3D PDA and Headlamp Animations/gamedata/scripts/actor_effects.script",
    "battery_warning.script": MODS / "175- Battery Warning - RavenAscendant/gamedata/scripts/battery_warning.script",
    "light_gem_mcm.script": MODS / "45- Stealth Overhaul - xcvb/gamedata/scripts/light_gem_mcm.script",
}

NL = "\r\n"


def sub(text, old, new, count=1):
    old = old.replace("\n", NL)
    new = new.replace("\n", NL)
    got = text.count(old)
    assert got == count, f"expected {count} hit(s), got {got} for:\n{old!r}"
    return text.replace(old, new)


# ---------------------------------------------------------------- fluid_aim
FLUID_HEAD = """
-- I-057 ---------------------------------------------------------------------
-- SYS_GetParam(0, section, ...) reads system.ltx, which does not change at
-- runtime, and this file asks for the same section's "class"/"kind" three or
-- four times every frame. Memoise per section string (an upgraded weapon gets
-- a different section, so it gets its own entry).
local SYS_GetParam = SYS_GetParam
local NOVALUE = {}
local param_cache = {}
local function sys_param(sec, key)
	local c = param_cache[sec]
	if not c then
		c = {}
		param_cache[sec] = c
	end
	local v = c[key]
	if v == nil then
		v = SYS_GetParam(0, sec, key)
		if v == nil then v = NOVALUE end
		c[key] = v
	end
	if v == NOVALUE then return nil end
	return v
end

-- ui_options.get("control/general/aim_toggle") is an ini_file_ex r_value miss
-- followed by str_opt_explode and get_console_cmd(1, "wpn_aim_toggle"); it only
-- changes from the options screen, which sends on_option_change.
local aim_toggle_val
local function aim_toggle()
	local v = aim_toggle_val
	if v == nil then
		v = ui_options.get("control/general/aim_toggle")
		if v == nil then v = false end
		aim_toggle_val = v
	end
	return v
end
local function aim_toggle_invalidate()
	aim_toggle_val = nil
end
-- end I-057 -----------------------------------------------------------------
"""


def fluid_aim(text):
    text = sub(text, """
-- Actor On Update(Every tick)
local function actor_on_update()""", FLUID_HEAD + """
-- Actor On Update(Every tick)
local function actor_on_update()""")

    text = sub(text, """	fluid_aim_fire = key_state(bind_to_dik(key_bindings.kWPN_FIRE)) ~= 0
	if ui_options.get("control/general/aim_toggle") then
""", """	fluid_aim_fire = key_state(bind_to_dik(key_bindings.kWPN_FIRE)) ~= 0
	if aim_toggle() then
""")

    text = sub(text, """	local wpn = actor:active_item()
	local is_weapon = wpn and (IsWeapon(wpn) or SYS_GetParam(0,wpn:section(),"class") == "WP_BINOC") or false
	if (is_weapon) then
		local state = wpn:get_state()

		-- Melee
		if (SYS_GetParam(0,wpn:section(),"kind") == "w_melee") then
""", """	local wpn = actor:active_item()
	local sec = wpn and wpn:section()   -- I-057: one section() for the frame
	local is_weapon = wpn and (IsWeapon(wpn) or sys_param(sec,"class") == "WP_BINOC") or false
	if (is_weapon) then
		local state = wpn:get_state()
		local kind = sys_param(sec,"kind")   -- I-057: one kind lookup, reused below

		-- Melee
		if (kind == "w_melee") then
""")
    text = sub(text, """		elseif SYS_GetParam(0,wpn:section(),"kind") ~= "w_melee" then
""", """		elseif kind ~= "w_melee" then
""")
    text = sub(text, """				local wpn_kind = SYS_GetParam(0,wpn:section(),"kind")
""", """				local wpn_kind = kind
""")
    text = sub(text, """	if wpn:get_state() == 1 and not (wpn:weapon_is_scope()) and SYS_GetParam(0,wpn:section(),"kind") ~= "w_sniper" then
""", """	if wpn:get_state() == 1 and not (wpn:weapon_is_scope()) and sys_param(wpn:section(),"kind") ~= "w_sniper" then
""")

    # the two key handlers read the same option
    text = sub(text, """		if ui_options.get("control/general/aim_toggle") and axr_main.weapon_is_zoomed then
""", """		if aim_toggle() and axr_main.weapon_is_zoomed then
""")
    text = sub(text, """		if ui_options.get("control/general/aim_toggle") then
			fluid_aim_toggle = false
""", """		if aim_toggle() then
			fluid_aim_toggle = false
""")

    text = sub(text, """	RegisterScriptCallback("on_option_change", ignore_fire_key_press)
""", """	RegisterScriptCallback("on_option_change", ignore_fire_key_press)
	RegisterScriptCallback("on_option_change", aim_toggle_invalidate)   -- I-057
""")
    return text


# ------------------------------------------------------------- actor_effects
def actor_effects(text):
    text = sub(text, """local zbias = Frect():set(0,0,1024,1024)
function HUD_fog(enabled, actor, rect)
""", """local zbias = Frect():set(0,0,1024,1024)
-- I-057: the not-enabled branch below re-zeroes 40 statics that are already
-- zero, every frame. Latch the loop; the three state writes under it still run.
local fog_zeroed = false
function HUD_fog(enabled, actor, rect)
""")
    text = sub(text, """	if (not enabled) then
		for i,t in ipairs(fogs) do
			for ii,st in ipairs(t) do
				st:SetWndRect(Frect():set(0,0,0,0))
			end
		end
		fog_val = 0
""", """	if (not enabled) then
		if not fog_zeroed then           -- I-057
			for i,t in ipairs(fogs) do
				for ii,st in ipairs(t) do
					st:SetWndRect(Frect():set(0,0,0,0))
				end
			end
			fog_zeroed = true
		end
		fog_val = 0
""")
    # every path that draws a rect un-latches it
    text = sub(text, """	local power = actor.power
""", """	fog_zeroed = false               -- I-057
	local power = actor.power
""")
    return text


# ----------------------------------------------------------- battery_warning
def battery_warning(text):
    # Two subs that each avoid the whitespace-only lines between the blocks:
    # lift the menu test out, then put it back below the throttle.
    menu = """	if (not main_hud_shown()) or ActorMenu.get_pda_menu():IsShown() or actor_menu.inventory_opened() then --lets not beep at ppl in menus.
		return
	end
"""
    text = sub(text, menu, """	-- I-057: the throttle first. The three menu queries this used to do here
	-- have no side effects and both branches return without touching state, so
	-- testing the cheap one first is observationally identical - and the
	-- throttle is the one that is true on 999 frames out of 1000
	-- (tg_update_step is 10 s). Moved below.
""")
    text = sub(text, """	if tg < tg_update then --if it hasn't been enough time return

		return
	end
""", """	if tg < tg_update then --if it hasn't been enough time return

		return
	end

""" + menu)
    return text


# ------------------------------------------------------------ light_gem_mcm
def light_gem_mcm(text):
    text = sub(text, """local gem

function light_gem()
	local hud = get_hud()
	local cs = hud:GetCustomStatic("mp_warm_up")\x20
		if (cs == nil) then\x20
""", """local gem
local gem_shown            -- I-057: last value handed to gem:Show()

function light_gem()
	-- I-057: `cs` is used for nothing but the nil test, and once `gem` exists
	-- the branch below can never run again, so skip both engine lookups.
	if not gem then
	local hud = get_hud()
	local cs = hud:GetCustomStatic("mp_warm_up")\x20
		if (cs == nil) then\x20
""")
    # close the new `if not gem then` right after the original init block
    text = sub(text, """	end
	gem:Show(stealth_mcm.get_config("icon") or GEM_ON)
""", """	end
	end   -- I-057: end of the `if not gem` block
	local show = stealth_mcm.get_config("icon") or GEM_ON
	if show ~= gem_shown then        -- I-057: Show() only on a change
		gem_shown = show
		gem:Show(show)
	end
""")
    text = sub(text, """	local torch = db.actor:item_in_slot(10)
	local flash = db.actor:item_in_slot(9)

	if (torch and torch:torch_enabled()) then
		gem_lum = gem_lum + FLASHLIGHT_PENALTY --flat value to make the gem jump as a reminder that you should shut off the light
	elseif (flash and (flash:section() == "device_flashlight") and db.actor:active_detector()) then
""", """	local torch = db.actor:item_in_slot(10)

	if (torch and torch:torch_enabled()) then
		gem_lum = gem_lum + FLASHLIGHT_PENALTY --flat value to make the gem jump as a reminder that you should shut off the light
	else
	local flash = db.actor:item_in_slot(9)   -- I-057: only this branch reads it
	if (flash and (flash:section() == "device_flashlight") and db.actor:active_detector()) then
""")
    # the original `end` closes the inner if; the outer else needs one more
    text = sub(text, """	local val = TINT_MAX - clamp(TINT_MAX * LUM_MULT * gem_lum, 0, TINT_MAX)
""", """	end
	local val = TINT_MAX - clamp(TINT_MAX * LUM_MULT * gem_lum, 0, TINT_MAX)
""")
    return text


PATCHERS = {
    "fluid_aim.script": fluid_aim,
    "actor_effects.script": actor_effects,
    "battery_warning.script": battery_warning,
    "light_gem_mcm.script": light_gem_mcm,
}


def source_for(name: str, pristine: bool) -> Path:
    if not pristine:
        p = GEN4 / name
        if p.is_file():
            return p
    return LIVE[name]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("outdir", type=Path)
    ap.add_argument("--pristine", action="store_true")
    ap.add_argument("--only", action="append", default=None)
    a = ap.parse_args(argv)
    dst = a.outdir / "gamedata" / "scripts"
    dst.mkdir(parents=True, exist_ok=True)
    for name, fn in PATCHERS.items():
        if a.only and name not in a.only:
            continue
        src = source_for(name, a.pristine)
        text = src.read_bytes().decode("utf-8")
        out = fn(text)
        (dst / name).write_bytes(out.encode("utf-8"))
        print(f"{name}: {src.parent.parent.parent.name} -> {dst / name} "
              f"({len(text)} -> {len(out)} chars)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
