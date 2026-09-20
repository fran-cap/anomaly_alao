"""I-057 track A: make demonized_ledge_grabbing's ray scan run on demand.

Where the number comes from: with all four gen-4 patches in (overlay
`gen4-all-b`) the listener-mode profile of the moving scene
(`20260920-110221-I-053-5895a5`, beam 11.1) still puts
`demonized_ledge_grabbing.script` at **68.2 us/frame**, the biggest single
listener left.  I-050a's cold-camera early out cannot fire while the camera
moves, so while the player walks the mod is back to building one ray object and
casting `settings.raySteps` (15) geometry rays every frame.

The observation this patch is built on: **the scan is speculative.**  Its only
outputs are the four file-locals `savedClimbPos`, `savedInterPos`,
`savedCollisionPos`, `savedClimbNormal` (plus the debug gizmos).  Grep the whole
enabled modlist: nothing outside this file touches them - they are file-locals,
and the only other mod that reaches into this script at all is
`zzzz_liz_fdda_redone_ledge_grabbing_patch.script`, which wraps
`checkClimbPrecondition` (a predicate this patch still calls).  Inside the file
they are read in exactly two places:

    tryToClimb()      - called from on_key_press, on_key_hold and checkJump
    onScreenCheck()   - called from checkJump and from the debugMode block

and every one of those runs only because the player pressed or is holding the
climb keybind.  So the per-frame scan is a cache warmed 200 times a second for a
reader that shows up a few times a minute.

What the patch does:

  * `checkLedgeGrabbing` (still registered on `actor_on_update`, so listener
    order and any other mod's view of the callback table are unchanged) returns
    immediately unless `debugMode` is on or a scan was explicitly asked for.
  * `ledgeScanNow()` sets that flag and calls `checkLedgeGrabbing` directly.
    It is called at the top of `checkJump` - which is itself an `actor_on_update`
    listener, registered only while the keybind is held - and immediately before
    each of the three `tryToClimb` calls on the key-press / key-hold paths.
  * Nothing inside the scan changes.  The cold-camera guard, the "nothing moved"
    guard, the throttle and the ray loop are all left exactly as they are, so a
    forced scan is subject to the same early-outs it always was.

Behavioural non-identities, precisely:

  1. **Freshness.**  In the `checkJump` path the scan now runs from inside
     `checkJump` rather than from the earlier-registered `checkLedgeGrabbing`
     slot of the *same* `actor_on_update` pass, so the inputs (`device().cam_pos`
     / `cam_dir`, `db.actor:position()`) are the same frame's - identical unless
     some listener registered between the two moves the actor mid-pass.  On the
     `on_key_press` / `on_key_hold` paths the scan now runs at the key event
     instead of at the last actor update, i.e. it is up to one frame *fresher*
     (<= 5 ms at 200 fps, <= 17 ms at 60 fps).  A climb can therefore trigger
     from a position at most one frame further along than before.
  2. **Cross-frame scan state advances only on scan frames.**  `savedActorPos` /
     `savedActorDir` (the "nothing moved" guard), `tg` (the `throttleCheck`
     timer) and the I-050a `cc*` block are written by the scan, so they now
     record the last *forced* scan instead of the last frame.  Both guards stay
     sound: they compare the current camera/actor state against the state the
     saved answer was computed from, and level geometry is static, so a guard
     that fires still returns an answer computed from inputs within its
     tolerance.  With `alternativeClimbDetection` on - the GAMMA default - the
     `vecSimilar` guard is disabled anyway and only the I-050a guard applies.
     With `throttleCheck > 0` (default 0) a forced scan can be throttled out,
     exactly as a per-frame scan would have been.
  3. **Housekeeping on non-scan frames.**  The three early returns at the top of
     the listener (`climbActive`, `settings.enable`, `checkClimbPrecondition`)
     call `reset()`, which in non-debug mode only nils `savedClimbPos`.  That now
     happens when the scan is asked for rather than every frame; since the only
     reader is preceded by a forced scan, no reader can see a value `reset()`
     would have cleared.
  4. `debugMode` keeps the old per-frame behaviour verbatim (the gizmos have to
     be redrawn every frame), so nothing about the debug view changes.

Usage:
    py -3.12 i057_ledge_ondemand_patch.py <out.script> [--src <in>] [--pristine]

--src defaults to the `gen4-all-b` copy (ALAO + I-050a), which is the baseline
arm of the in-game comparison.  `--pristine` patches the untouched live mod copy
instead, for an upstream diff; the patch applies to both.
"""
import argparse
import sys
from pathlib import Path

LIVE = Path(
    r"D:\GOG_Games\Gamma\S.T.A.L.K.E.R. GAMMA\GAMMA\mods"
    r"\350- Ledge Grabbing - Demonized\gamedata\scripts\demonized_ledge_grabbing.script"
)
GEN4 = Path(
    r"C:\code\GIT\anomaly_alao\lab\coord\overlays\gen4-all-b"
    r"\gamedata\scripts\demonized_ledge_grabbing.script"
)

NL = "\r\n"


def sub(text, old, new, count=1):
    old = old.replace("\n", NL)
    new = new.replace("\n", NL)
    got = text.count(old)
    assert got == count, f"expected {count} hit(s), got {got} for:\n{old!r}"
    return text.replace(old, new)


GATE = """
-- I-057: the ray scan below is a cache for tryToClimb() / onScreenCheck(), and
-- both of those only ever run from the climb keybind. So do not warm it every
-- frame; ledgeScanNow() runs it at the moment a reader needs it. debugMode
-- keeps the old per-frame behaviour because the gizmos are redrawn from here.
local forceScan = false
function ledgeScanNow()
	if climbActive then return end
	if not savedCamY then return end   -- actor_on_first_update has not run yet
	forceScan = true
	checkLedgeGrabbing()
	forceScan = false
end
"""


def patch(text: str) -> str:
    # ---------------------------------------------------------------- 1. gate
    # Two anchors, because the gen-4 arm already carries I-050a's ccValid reset.
    if "ccValid = false return end" in text:
        head = """local tg = 0
function checkLedgeGrabbing()
	if climbActive then ccValid = false return end
"""
    else:
        head = """local tg = 0
function checkLedgeGrabbing()
	if climbActive then return end
"""
    text = sub(text, head, head + """	if not (forceScan or debugMode) then return end
""")

    # forceScan/ledgeScanNow have to be declared above checkLedgeGrabbing (the
    # flag is an upvalue) and ledgeScanNow resolves checkLedgeGrabbing as a
    # global at call time, so it does not matter that it is defined later.
    text = sub(text, """
local tg = 0
function checkLedgeGrabbing()""", GATE + """
local tg = 0
function checkLedgeGrabbing()""")

    # ------------------------------------------------------- 2. demand points
    # checkJump is itself an actor_on_update listener, registered only while the
    # keybind is held, and every branch of it reads savedClimbPos.
    text = sub(text, """
function checkJump(pressed)
	if pressed == true then
""", """
function checkJump(pressed)
	ledgeScanNow()   -- I-057
	if pressed == true then
""")

    # on_key_press, both modifier branches (inputMethod "buttonPress" / "both").
    # The anchors deliberately start below the `... then ` lines: several of
    # those end in a trailing space in the shipped file and an editor that trims
    # them would silently break the assert.
    text = sub(text, """
                tryToClimb(savedSpeed.y < 0.001)
            end
""", """
                ledgeScanNow()   -- I-057
                tryToClimb(savedSpeed.y < 0.001)
            end
""")
    text = sub(text, """		elseif settings.modifier == 0 then
			tryToClimb(savedSpeed.y < 0.001)
""", """		elseif settings.modifier == 0 then
			ledgeScanNow()   -- I-057
			tryToClimb(savedSpeed.y < 0.001)
""")

    # on_key_hold.
    text = sub(text, """
                tryToClimb()""", """
                ledgeScanNow()   -- I-057
                tryToClimb()""")
    return text


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("out", type=Path)
    ap.add_argument("--src", type=Path, default=None)
    ap.add_argument("--pristine", action="store_true",
                    help="patch the untouched live mod copy instead of gen4-all-b")
    a = ap.parse_args(argv)
    src = a.src or (LIVE if a.pristine else GEN4)
    text = src.read_bytes().decode("utf-8")
    out = patch(text)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_bytes(out.encode("utf-8"))
    print(f"wrote {a.out} ({len(out.encode('utf-8'))} bytes, source {src} {src.stat().st_size})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
