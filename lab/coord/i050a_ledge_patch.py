"""I-050a hand patch for demonized_ledge_grabbing.script (checkLedgeGrabbing).

The per-listener profile (queue item 20260919-193557-I-048-b1325e) put
`demonized_ledge_grabbing.script:443` at 142 us/frame, 1 call/frame, standing
still - second only to drx_da_main's 353 closures. All of it is one
`actor_on_update` listener, `checkLedgeGrabbing`, and standing on flat ground
it spends that budget casting `settings.raySteps` (default 15) geometry rays
that are guaranteed to miss, every single frame.

Why it does not already stop: the function HAS a "nothing moved" early out
(the vecSimilar pair on the actor's position and direction), but it is switched
off by `and not settings.alternativeClimbDetection`, and alternative climb
detection is ON by default. The reason is sound - with it on, the scan's upper
bound is derived from the CAMERA pitch, which the actor's own direction vector
does not track - so the fix is to guard on the camera instead of removing the
exception.

Three changes, in order of what they are worth:

  1. cold-camera early out. Everything the scan reads is (cam_pos, cam_dir,
     actor position y, savedCamY, settings, STATIC level geometry - every ray
     in here is flags = 2). If none of those moved since the last full scan,
     the saved* values that scan wrote are still its answer and repeating it
     cannot change anything observable. Worth ~all 142 us while the camera is
     still; worth nothing while it moves.
  2. the scan's ray object is built once per scan instead of once per step.
     The constructor args are loop invariant and geometry_ray:get() sets
     position and direction on every call. 14 of 15 constructions go away.
     Worth something on every frame that actually scans.
  3. one device() and one db.actor for the prologue. ALAO's analyzer finds
     both of these (repeated_device x5, repeated_db_actor x8 in this one
     function) and its transformer declines the edit, because the first call
     sits inside a multi-line argument list - see _edit_repeated_calls'
     `if paren_depth > 0: return`. Small; included because it is free.

Everything below the throttle is moved into `ledgeScan` byte for byte so the
diff stays reviewable; the only edits inside it are the shared ray and
`db.actor` -> the already-dereferenced `actor`.

Usage:
    py -3.12 i050a_ledge_patch.py <out.script> [--src <in.script>]

--src defaults to the ALAO-rewritten copy in overlays/ref3-alao-b, because that
is the baseline arm for the in-game comparison. It also works on the untouched
live copy: ALAO changes nothing inside checkLedgeGrabbing except two
string.find calls at the material check, which no replacement here touches.
"""
import argparse
import sys
from pathlib import Path

# The live winner: mod "350- Ledge Grabbing - Demonized" is the only enabled mod
# in the modlist that ships this file.
LIVE = Path(
    r"D:\GOG_Games\Gamma\S.T.A.L.K.E.R. GAMMA\GAMMA\mods"
    r"\350- Ledge Grabbing - Demonized\gamedata\scripts\demonized_ledge_grabbing.script"
)
ALAO = Path(
    r"C:\code\GIT\anomaly_alao\lab\coord\overlays\ref3-alao-b"
    r"\gamedata\scripts\demonized_ledge_grabbing.script"
)

NL = "\r\n"

# How still is still. The scan samples geometry every
# settings.climbTriggerDistance / settings.raySteps = 8.7 cm by default, and
# the function's own "nothing moved" guard works to 2 cm; 0.1 mm of position
# and 1e-5 of a (unit) direction component are three orders below either, so
# what this skips is camera noise, not camera movement. Set both to 0 for
# bit-exact equality if you would rather the guard only fire on a frozen frame.
EPS_POS = "0.0001"
EPS_DIR = "0.00001"


def sub(text, old, new, count=1):
    old = old.replace("\n", NL)
    new = new.replace("\n", NL)
    got = text.count(old)
    assert got == count, f"expected {count} hit(s), got {got} for:\n{old!r}"
    return text.replace(old, new)


def patch(text: str) -> str:
    # ------------------------------------------------------------------ 0.
    # State for the guard. Declared up here because `reset` (defined well above
    # checkLedgeGrabbing) has to be able to bump the reset counter, and a Lua
    # local is only visible below its declaration.
    text = sub(text, """
local climbActive = false
""", """
local climbActive = false

-- I-050: cold-camera early out state. See the header of i050a_ledge_patch.py.
local ccValid = false
local ccPX, ccPY, ccPZ = 0, 0, 0
local ccDX, ccDY, ccDZ = 0, 0, 0
local ccAY = 0
local ccGen = -1
local ccReset = 0
local settingsGen = 0
local resetGen = 0
local CC_EPS_POS = %s
local CC_EPS_DIR = %s
local ledgeScan
""" % (EPS_POS, EPS_DIR))

    # An MCM change is the one input to the scan that is neither the camera nor
    # the level, so it gets a generation counter of its own.
    text = sub(text, """	end
	return settings
end
""", """	end
	settingsGen = settingsGen + 1
	return settings
end
""")

    # reset() is the only thing besides the scan that writes savedClimbPos, and
    # it is called from actor_on_first_update, from the end of a climb and from
    # the debug toggle. Counting resets and recording the count AFTER the scan
    # means any reset from anywhere - including a third-party caller - forces
    # the next frame to rescan, while the scan's own resets do not.
    text = sub(text, """function reset(gizmosArr, force)
""", """function reset(gizmosArr, force)
	resetGen = resetGen + 1
""")

    # ------------------------------------------------------------------ 1-3.
    # The prologue: CSE, the guard, and the split into ledgeScan.
    text = sub(text, """function checkLedgeGrabbing()
	if climbActive then return end
	if not settings.enable then return reset() end
	if not checkClimbPrecondition() then return reset() end
	local actorPos = vector():set(
		device().cam_pos.x,
		db.actor:position().y,
		device().cam_pos.z
	)
	local actorDir = device().cam_dir
	local realActorPos = db.actor:position()
	local realActorDir = db.actor:direction()
""", """function checkLedgeGrabbing()
	if climbActive then ccValid = false return end
	if not settings.enable then ccValid = false return reset() end
	if not checkClimbPrecondition() then ccValid = false return reset() end

	-- I-050: one device() and one db.actor for the whole prologue.
	local dev = device()
	local camPos = dev.cam_pos
	local camDir = dev.cam_dir
	local actor = db.actor
	local realActorPos = actor:position()

	-- I-050: cold-camera early out. The scan below reads exactly camPos,
	-- camDir, realActorPos.y, savedCamY (fixed after actor_on_first_update),
	-- settings, and static level geometry. None of them moved since the last
	-- full scan => that scan's saved* output is still the answer, so running
	-- it again cannot change anything observable. ccValid is cleared on every
	-- path above that returns without scanning, and ccReset catches a reset()
	-- from anywhere else.
	if ccValid
	and ccGen == settingsGen
	and ccReset == resetGen
	and abs(camPos.x - ccPX) <= CC_EPS_POS
	and abs(camPos.y - ccPY) <= CC_EPS_POS
	and abs(camPos.z - ccPZ) <= CC_EPS_POS
	and abs(camDir.x - ccDX) <= CC_EPS_DIR
	and abs(camDir.y - ccDY) <= CC_EPS_DIR
	and abs(camDir.z - ccDZ) <= CC_EPS_DIR
	and abs(realActorPos.y - ccAY) <= CC_EPS_POS
	then
		return
	end

	local actorPos = vector():set(
		camPos.x,
		realActorPos.y,
		camPos.z
	)
	local actorDir = camDir
	local realActorDir = actor:direction()
""")

    # The split. Everything from here to the end of the function is the
    # original body, unchanged apart from the two edits below it.
    text = sub(text, """	tg = max(t, tg + settings.throttleCheck)

	-- Check if player has possibility to initiate climb
""", """	tg = max(t, tg + settings.throttleCheck)

	ledgeScan(actorPos, actorDir, camPos, camDir, actor)

	-- I-050: this frame did the full scan; remember what it was computed from.
	-- Recorded after the scan, so a reset() the scan itself performed is part
	-- of the recorded state rather than an invalidation of it.
	ccValid = true
	ccPX, ccPY, ccPZ = camPos.x, camPos.y, camPos.z
	ccDX, ccDY, ccDZ = camDir.x, camDir.y, camDir.z
	ccAY = realActorPos.y
	ccGen = settingsGen
	ccReset = resetGen
end

ledgeScan = function(actorPos, actorDir, camPos, camDir, actor)
	-- Check if player has possibility to initiate climb
""")

    # alternativeClimbDetection re-read device() twice; reuse the prologue's.
    text = sub(text, """		local devicePos = device().cam_pos
		local deviceDir = device().cam_dir
""", """		local devicePos = camPos
		local deviceDir = camDir
""")

    # ------------------------------------------------------------------ 2.
    # One ray object for the whole scan.
    text = sub(text, """	for j = 1, settings.raySteps do
		local i = rayStepDistance * j
		local ray = geometry_ray({
			ray_range = rayRange,
			flags = 2,
			ignore_object = db.actor
		})
""", """	-- I-050: one ray for the whole scan instead of one per step. The args are
	-- loop invariant and geometry_ray:get() sets position and direction on
	-- every call, so step j sees exactly what a fresh object would. The nested
	-- checks below keep rays of their own, so nothing re-queries this one
	-- while res.result (a userdata into its last query) is still being read.
	local scanRay = geometry_ray({
		ray_range = rayRange,
		flags = 2,
		ignore_object = actor
	})
	for j = 1, settings.raySteps do
		local i = rayStepDistance * j
		local ray = scanRay
""")

    # ------------------------------------------------------------------ 3.
    # The remaining db.actor reads inside the scan are the same object the
    # prologue already dereferenced. Each one is pinned by its neighbours,
    # because the same four lines appear again outside ledgeScan (amIStuck,
    # the unstuck check) where `actor` does not exist.
    def tabs(n, s):
        return "\t" * n + s

    for indent, before, after in (
        # playerWidthCheck's ray
        (7, tabs(7, "ray_range = rayRange,\n") + tabs(7, "flags = 2,\n"),
         tabs(6, "})\n") + tabs(6, "local r = ray:get(pos, dir)\n")),
        # posCheck's ray
        (8, tabs(8, "ray_range = savedCamY,\n") + tabs(8, "flags = 2,\n"),
         tabs(7, "})\n")),
        # the "advance a bit further" ray
        (8, tabs(8, "ray_range = rayRange,\n") + tabs(8, "flags = 2,\n"),
         tabs(7, "})\n\n")),
        # actorPos -> interPos collision check
        (2, tabs(2, "flags = 2,\n"), "    })\n" + tabs(1, "local pos = adjActorPos\n")),
        # interPos -> climbPos collision check
        (3, tabs(3, "flags = 2,\n"), tabs(1, "    })\n") + tabs(2, "local pos = interPos\n")),
    ):
        line = tabs(indent, "ignore_object = db.actor\n")
        text = sub(text, before + line + after,
                   before + tabs(indent, "ignore_object = actor\n") + after)
    return text


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("out", type=Path)
    ap.add_argument("--src", type=Path, default=None,
                    help="source script (default: the ref3-alao-b copy, else the live mod copy)")
    a = ap.parse_args(argv)
    src = a.src
    if src is None:
        src = ALAO if ALAO.is_file() else LIVE
    text = src.read_bytes().decode("utf-8")
    out = patch(text)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_bytes(out.encode("utf-8"))
    print(f"wrote {a.out} ({len(out.encode('utf-8'))} bytes, source {src} {src.stat().st_size})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
