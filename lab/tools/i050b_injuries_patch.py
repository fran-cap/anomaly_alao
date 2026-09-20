"""I-050b: hand patch for the live zzz_player_injuries.script.

The gen-3 per-listener profile put this file's `actor_on_update` at ~74 us EVERY
frame, 1 call/frame, standing still and uninjured.  Nothing in that body is
doing anything: the frame is spent crossing the Lua/C boundary ~104 times to
re-fetch things that cannot change inside one frame and to re-decide questions
the script already knows the answer to.

The patch removes crossings, not behaviour.  Each change and the argument for
it:

 1. `get_hud()` 16x -> 1x per HUD pass.  It returns the process-wide
    CUIGameCustom; the script already assumes non-nil (it indexes the first
    result unchecked).
 2. `time_global()` 16x -> 1x.  ParamBar called it once per bar; every call in
    one frame returns the same millisecond (the whole pass is 74 us).  It is
    now threaded down from actor_on_update, with `tg or time_global()` so an
    outside caller of HUDUpdate/bhs_concussion still works.
 3. `bhs_garbage`: ParamBar asked the engine for a custom static by that fixed
    name on every no-background bar (6x/frame) only to remove it if present.
    Nothing in the live script set ever adds it - the only three files that
    mention the name are the three copies of this very script, in this same
    dead branch - so the lookup and its branch are dead.
 4. the `<bar>_bg` statics: same lookup, 8x/frame, for names this script is the
    only writer of.  The *remove* side now consults a Lua-side `bg_shown` set
    instead of the engine; the *add* side still asks the engine, so a static
    the engine dropped on its own (level change, auto-delete) is still
    recreated.  Worst case the set is stale and we call RemoveCustomStatic on a
    name that is already gone, which is a no-op.  The live HUD mode
    (show_hud_type 2) only ever takes the remove side, so this is 8 crossings a
    frame in the configuration that was measured.
 5. `TEXT_BASED_PATCH` read through MCM twice per frame.  `ini_file_ex:r_value`
    caches with `if (cache_result) then`, so a stored *false* never hits the
    cache and each read pays section_exist + line_exist + r_string: 6 crossings
    a frame for a value the file already snapshots at load time twice
    (`hide_default_hud`, `showtexthud`).  Now read once, like those two.
 6. two dead reads in actor_on_update: `actor.health` into `newhealth`/`amount`
    whose only consumer is commented out, and `actor:get_movement_speed()` into
    `movementspeed`, never used in that function (PartialDamage computes its
    own).  That one call is 7 crossings: the getter plus x,y,z read twice each.
 7. a dead `hud_d:wnd()` in the picture branch whose result is never used.

What is NOT in here, on purpose (see the report): the per-frame
`ActorMenu.get_maingame()` HUD-bar suppression, whose `hidehudonce` latch is a
local and so resets every frame, and the two unconditional
`level.set_pp_effector_factor` calls.  Both would change observable behaviour.

Usage:  py -3.12 lab/tools/i050b_injuries_patch.py <out.script> [<in.script>]
Default input is the ALAO-rewritten live copy in the ref3-alao-b overlay, which
is the arm the 74 us was measured on.
"""
from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(
    r"C:\code\GIT\anomaly_alao\lab\coord\overlays\ref3-alao-b"
    r"\gamedata\scripts\zzz_player_injuries.script"
)

NL = "\r\n"


def sub(text, old, new, count=1):
    old = old.replace("\n", NL)
    new = new.replace("\n", NL)
    got = text.count(old)
    assert got == count, f"expected {count} hit(s), got {got} for:\n{old!r}"
    return text.replace(old, new)


def patch(text: str) -> str:
    # --- 5. TEXT_BASED_PATCH read once ------------------------------------
    text = sub(
        text,
        'local hide_default_hud= (zzz_player_injuries_mcm.get_config("TEXT_BASED_PATCH")) and false or true',
        '-- I-050b: read TEXT_BASED_PATCH once. ini_file_ex:r_value caches with\n'
        '-- `if (cache_result) then`, so a stored FALSE never hits the cache and every\n'
        '-- read costs section_exist + line_exist + r_string. The file already treats\n'
        '-- this option as load-time constant (hide_default_hud, showtexthud below).\n'
        'local TEXT_BASED_PATCH = zzz_player_injuries_mcm.get_config("TEXT_BASED_PATCH")\n'
        'local hide_default_hud= TEXT_BASED_PATCH and false or true',
    )
    text = sub(
        text,
        'local showtexthud=(zzz_player_injuries_mcm.get_config("TEXT_BASED_PATCH")) and 2 or 0',
        'local showtexthud=(TEXT_BASED_PATCH) and 2 or 0',
    )

    # --- 2. bhs_concussion takes the frame's tg ---------------------------
    text = sub(
        text,
        'function bhs_concussion()\n'
        '\n'
        '\tif not (NEW_LIMB_PENALTIES_FEATURE) then return end\n'
        '\n'
        '\tlocal tg = time_global()',
        'function bhs_concussion(tg)\n'
        '\n'
        '\tif not (NEW_LIMB_PENALTIES_FEATURE) then return end\n'
        '\n'
        '\ttg = tg or time_global()\t-- I-050b: reuse the caller\'s clock read',
    )

    # --- 3/4. the bg_shown set, next to the other HUD latch ---------------
    text = sub(
        text,
        'local scuffed_fix = false\t\n',
        'local scuffed_fix = false\t\n'
        '-- I-050b: which "<bar>_bg" custom statics this script has added and not\n'
        '-- removed. ParamBar used to ask the engine on every bar on every frame just\n'
        '-- to decide whether to remove one; this script is the only writer of those\n'
        '-- names, so a Lua-side set answers the same question with no crossing.\n'
        'local bg_shown = {}\n',
    )

    # --- 1/2. one get_hud + one time_global per HUD pass -------------------
    text = sub(
        text,
        'local function HUDUpdate()\n'
        '\tif showtexthud>=1 then --text',
        'local function HUDUpdate(tg)\n'
        '\t-- I-050b: one time_global() and one get_hud() for the whole pass instead\n'
        '\t-- of 16 each. get_hud() is the process-wide CUIGameCustom (the code below\n'
        '\t-- already indexed the first result unchecked) and the pass takes tens of\n'
        '\t-- microseconds, so every time_global() in it returned the same ms.\n'
        '\ttg = tg or time_global()\n'
        '\tlocal hud = get_hud()\n'
        '\tif not (hud) then return end\n'
        '\tif showtexthud>=1 then --text',
    )

    # picture branch: drop its own get_hud
    text = sub(
        text,
        '\t\tlocal staticname="body_health_system"\n'
        '\t\tif display.types[display_ratio] then\n'
        '\t\t\tif display_ratio~=169 then\n'
        '\t\t\t\tstaticname=staticname.."_"..tostring(display_ratio)\n'
        '\t\t\tend\n'
        '\t\tend\n'
        '\t\tlocal hud = get_hud()\n'
        '\t\tlocal hud_d = hud:GetCustomStatic(staticname)',
        '\t\tlocal staticname="body_health_system"\n'
        '\t\tif display.types[display_ratio] then\n'
        '\t\t\tif display_ratio~=169 then\n'
        '\t\t\t\tstaticname=staticname.."_"..tostring(display_ratio)\n'
        '\t\t\tend\n'
        '\t\tend\n'
        '\t\tlocal hud_d = hud:GetCustomStatic(staticname)',
    )

    # --- 7. the dead wnd()/get_hud pair in the picture branch --------------
    text = sub(
        text,
        '\t\tif (hud_d ~= nil) then\n'
        '\t\t\twnd = hud_d:wnd()\n'
        '\n'
        '\t\t\t\n'
        '\t\t\tlocal hud = get_hud()\n'
        '\t\t\tif not (hud) then \n'
        '\t\t\t\treturn \n'
        '\t\t\tend\n'
        '\t\tend\n'
        '\t\t\n'
        '\t\t::otherhudparts::',
        '\t\t-- I-050b: `wnd` was assigned here and never read, and get_hud() is\n'
        '\t\t-- already checked at the top of the function.\n'
        '\t\t::otherhudparts::',
    )

    # --- 3/4. ParamBar background block ------------------------------------
    text = sub(
        text,
        '\t\t\t---- minimal bgs ----\n'
        '\t\t\tlocal staticname \n'
        '\t\t\tif showbg then\n'
        '\t\t\t\tstaticname=customstatic.."_bg"\n'
        '\t\t\telse\n'
        '\t\t\t\tstaticname="bhs_garbage"\n'
        '\t\t\tend\n'
        '\t\t\tif display.types[display_ratio] then\n'
        '\t\t\t\tif display_ratio~=169 then\n'
        '\t\t\t\t\tstaticname=staticname.."_"..tostring(display_ratio)\n'
        '\t\t\t\tend\n'
        '\t\t\tend\n'
        '\t\t\tlocal hud = get_hud()\n'
        '\t\t\tlocal hud_d = hud:GetCustomStatic(staticname)\n'
        '\t\t\tlocal wnd\n'
        '\t\t\t\n'
        '\t\t\tif not healthstatus or show_hud_type~=1 or (not showbg) then --not show\n'
        '\t\t\t\tif (hud_d ~= nil) then\n'
        '\t\t\t\t\thud:RemoveCustomStatic(staticname)\n'
        '\t\t\t\t\thud_d = nil\n'
        '\t\t\t\tend\n'
        '\t\t\telse\n'
        '\t\t\t\tif (hud_d == nil) then\n'
        '\t\t\t\t\thud:AddCustomStatic(staticname,true)\n'
        '\t\t\t\t\thud_d = hud:GetCustomStatic(staticname)\n'
        '\t\t\t\t\twnd = hud_d:wnd()\n'
        '\t\t\t\t\tif (wnd ~= nil) then\n'
        '\t\t\t\t\t\twnd:SetAutoDelete(true)\n'
        '\t\t\t\t\tend\n'
        '\t\t\t\tend\n'
        '\t\t\t\t\n'
        '\t\t\t\tif (hud_d ~= nil) then\n'
        '\t\t\t\t\twnd = hud_d:wnd()\n'
        '\t\t\t\t\t\n'
        '\t\t\t\t\tlocal hud = get_hud()\n'
        '\t\t\t\t\tif not (hud) then \n'
        '\t\t\t\t\t\tgoto progressbars \n'
        '\t\t\t\t\tend\n'
        '\t\t\t\tend\n'
        '\t\t\t\n'
        '\t\t\tend\n'
        '\t\t\t-------------------\n'
        '\t\t\t::progressbars::',
        '\t\t\t---- minimal bgs ----\n'
        '\t\t\t-- I-050b: with showbg false this block only ever looked up the fixed\n'
        '\t\t\t-- name "bhs_garbage" so it could remove it - and nothing anywhere in\n'
        '\t\t\t-- the live script set ever adds that name, so the whole branch was\n'
        '\t\t\t-- dead (6 GetCustomStatic a frame). With showbg true the presence of\n'
        '\t\t\t-- the "_bg" static is tracked in bg_shown instead of re-asked.\n'
        '\t\t\tif showbg then\n'
        '\t\t\t\tlocal staticname=customstatic.."_bg"\n'
        '\t\t\t\tif display.types[display_ratio] then\n'
        '\t\t\t\t\tif display_ratio~=169 then\n'
        '\t\t\t\t\t\tstaticname=staticname.."_"..tostring(display_ratio)\n'
        '\t\t\t\t\tend\n'
        '\t\t\t\tend\n'
        '\t\t\t\tif not healthstatus or show_hud_type~=1 then --not show\n'
        '\t\t\t\t\t-- bg_shown is only trusted in this direction: if the engine\n'
        '\t\t\t\t\t-- dropped the static behind our back we call\n'
        '\t\t\t\t\t-- RemoveCustomStatic on a name that is already gone, which is\n'
        '\t\t\t\t\t-- a no-op. The add path below still asks the engine.\n'
        '\t\t\t\t\tif bg_shown[staticname] then\n'
        '\t\t\t\t\t\thud:RemoveCustomStatic(staticname)\n'
        '\t\t\t\t\t\tbg_shown[staticname] = nil\n'
        '\t\t\t\t\tend\n'
        '\t\t\t\telse\n'
        '\t\t\t\t\tlocal hud_d = hud:GetCustomStatic(staticname)\n'
        '\t\t\t\t\tif (hud_d == nil) then\n'
        '\t\t\t\t\t\thud:AddCustomStatic(staticname,true)\n'
        '\t\t\t\t\t\thud_d = hud:GetCustomStatic(staticname)\n'
        '\t\t\t\t\t\tlocal wnd = hud_d:wnd()\n'
        '\t\t\t\t\t\tif (wnd ~= nil) then\n'
        '\t\t\t\t\t\t\twnd:SetAutoDelete(true)\n'
        '\t\t\t\t\t\tend\n'
        '\t\t\t\t\tend\n'
        '\t\t\t\t\tbg_shown[staticname] = true\n'
        '\t\t\t\tend\n'
        '\t\t\tend\n'
        '\t\t\t-------------------\n'
        '\t\t\t::progressbars::',
    )

    # --- 2. ParamBar's own clock read --------------------------------------
    text = sub(
        text,
        '\t\t\tif show_hud_type<1 or (time_global()-show_hud_change_time<50) then --not show',
        '\t\t\tif show_hud_type<1 or (tg-show_hud_change_time<50) then --not show',
    )

    # --- 5. the second per-frame MCM read ----------------------------------
    text = sub(
        text,
        '\t\t--- blue bkg health equals heals-timedhp -----\n'
        '\t\tif not (zzz_player_injuries_mcm.get_config("TEXT_BASED_PATCH")) then',
        '\t\t--- blue bkg health equals heals-timedhp -----\n'
        '\t\tif not (TEXT_BASED_PATCH) then',
    )
    text = sub(
        text,
        '\t\t-- trace_this("Entre\\n")\n'
        '\t\tif not (zzz_player_injuries_mcm.get_config("TEXT_BASED_PATCH")) then',
        '\t\t-- trace_this("Entre\\n")\n'
        '\t\tif not (TEXT_BASED_PATCH) then',
    )

    # --- 6. dead reads in actor_on_update ----------------------------------
    text = sub(
        text,
        '\t\tlocal actor = db.actor\n'
        '\t\tlocal pairs_ = pairs\n'
        '\t\tlocal newhealth=actor.health\n'
        '\t\tlocal amount=myhealth-newhealth\n',
        '\t\tlocal actor = db.actor\n'
        '\t\tlocal pairs_ = pairs\n'
        '\t\t-- I-050b: newhealth/amount fed only the commented-out block below.\n',
    )
    text = sub(
        text,
        '\t\tlocal speedvector=actor:get_movement_speed()\n'
        '\t\tlocal movementspeed=(speedvector.x*speedvector.x)+(speedvector.y*speedvector.y)+(speedvector.z*speedvector.z)\n',
        '\t\t-- I-050b: speedvector/movementspeed were never read in this function\n'
        '\t\t-- (PartialDamage takes its own). The getter plus six vector component\n'
        '\t\t-- reads were 7 boundary crossings a frame for nothing.\n',
    )

    # --- thread tg into the two callees ------------------------------------
    text = sub(
        text,
        '\t\tlimp_speed_slow()\n'
        '\t\ttorso_penalty()\n'
        '\t\tbhs_concussion()\n',
        '\t\tlimp_speed_slow()\n'
        '\t\ttorso_penalty()\n'
        '\t\tbhs_concussion(tg)\n',
    )
    text = sub(
        text,
        '\t\tHUDUpdate()\n'
        '\t\tPartialDamage()\n'
        'end\n',
        '\t\tHUDUpdate(tg)\n'
        '\t\tPartialDamage()\n'
        'end\n',
    )
    return text


def main(argv):
    out = Path(argv[1])
    src = Path(argv[2]) if len(argv) > 2 else SRC
    text = src.read_bytes().decode("cp1251")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(patch(text).encode("cp1251"))
    print(f"wrote {out} ({out.stat().st_size} bytes, source {src.stat().st_size})")


if __name__ == "__main__":
    main(sys.argv)
