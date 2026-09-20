"""I-049 hand patch for drx_da_main.script: 353 per-binder actor_on_update
closures -> one shared throttled walker.

Same trick as i043_axr_main_patch.py: byte-exact string surgery on the live
winner, every replacement asserted to hit exactly once, so a GAMMA update that
touches these lines makes this fail loudly instead of emitting half a patch.

The mod is "234- Dynamic Anomalies Overhaul - Demonized". Its
bind_anomaly_field.net_spawn registers one `actor_on_update` closure per
anomaly binder; the gen-3 in-game listener profile measured 265-353 of them on
the `gammabaseline` save, 134-179 us/frame, and on all but a handful of frames
every one of them does nothing but `time_global()` and a compare.

Usage:
    py -3.12 i049_drx_da_patch.py <out.script> [--src <in.script>]

--src defaults to the ALAO-rewritten live copy in overlays/ref3-alao-b (the
baseline arm of the in-game experiment); pass the pristine mod copy to get the
delivery version.
"""
from __future__ import annotations

import argparse
from pathlib import Path

# The ALAO-rewritten live copy. The baseline arm of the I-049 experiment is
# ref3-alao-b itself, so the variant has to be built on top of this one.
SRC_ALAO = Path(
    r"C:\code\GIT\anomaly_alao\lab\coord\overlays\ref3-alao-b"
    r"\gamedata\scripts\drx_da_main.script"
)
# The pristine mod copy, for a standalone delivery patch.
SRC_PRISTINE = Path(
    r"D:\GOG_Games\Gamma\S.T.A.L.K.E.R. GAMMA\GAMMA\mods"
    r"\234- Dynamic Anomalies Overhaul - Demonized\gamedata\scripts\drx_da_main.script"
)

NL = "\r\n"

# --- anchors -----------------------------------------------------------------

REG_OLD = """	if key and callbacks[key] then
		UnregisterScriptCallback(callbacks[key].callback, callbacks[key].func)
	end
"""

REG_NEW = """	if key and callbacks[key] then
		-- I-049: throttled anomaly updates do not live in axr_main's intercepts.
		if callbacks[key].throttled then
			anomaly_update_remove(key)
		else
			UnregisterScriptCallback(callbacks[key].callback, callbacks[key].func)
		end
	end
"""

UNREG_OLD = """function unregister_callback(key)
	if not callbacks[key] then return end
	printf("unregistering callback %s, %s", key, callbacks[key].callback)
	UnregisterScriptCallback(callbacks[key].callback, callbacks[key].func)
"""

UNREG_NEW = """function unregister_callback(key)
	if not callbacks[key] then return end
	printf("unregistering callback %s, %s", key, callbacks[key].callback)
	if callbacks[key].throttled then
		anomaly_update_remove(key)
	else
		UnregisterScriptCallback(callbacks[key].callback, callbacks[key].func)
	end
"""

# where the shared dispatcher goes: immediately before the net_spawn patch
ANCHOR_SPAWN = """bind_anomaly_field_spawn = bind_anomaly_field.anomaly_field_binder.net_spawn
"""

CLOSURE_HEAD = """	register_callback("actor_on_update", function()
		local tg = time_global()
		if tg < self.on_update_time then return end
"""
CLOSURE_TAIL = """	end, nil, self.update_key)
"""

BLOCK_HEAD = """-- ---------------------------------------------------------------------------
-- I-049: one shared walker instead of one actor_on_update listener per anomaly.
--
-- net_spawn below used to do register_callback("actor_on_update", function() ...
-- end) per anomaly binder, so a loaded level put 265-353 listeners on the
-- engine's busiest callback, and on all but a handful of frames every one of
-- them called time_global(), compared it with self.on_update_time and returned.
-- The in-game profile put that at 134-179 us per frame, plus the share of
-- axr_main's dispatch (a min-heap over the whole intercept table) that those
-- listeners own.
--
-- Same work, one listener: the binders sit in a registration-ordered array and
-- a single walker does the compare inline. What keeps this identical rather
-- than merely equivalent:
--   * time_global() is Device.dwTimeGlobal, which the engine advances once per
--     frame, so one sample for the whole pass is the number all 353 calls
--     returned anyway. The throttles are NOT re-phased.
--   * `for i = 1, au_n` freezes the bound before the first body runs, so a
--     binder registered by a body in this pass first fires next frame - which
--     is what hspairs did, it collects its keys up front.
--   * a binder unregistered by an earlier body in this pass is skipped
--     (`if s`), which is hspairs' `if val ~= nil` guard.
--   * removal punches a hole and compaction runs after the walk, never during.
-- The one thing that is NOT identical: the 353 used to occupy 353 separate
-- slots in actor_on_update's order and now share the first one's slot. They
-- only ever touch their own binder, so nothing observable depends on it.
-- ---------------------------------------------------------------------------

local au_self  = {}      -- i -> binder, false marks a hole
local au_lvl   = {}      -- i -> level name captured at spawn
local au_key   = {}      -- i -> callbacks key
local au_index = {}      -- key -> i
local au_n     = 0
local au_holes = 0
local au_registered = false

local function anomaly_update_body(self, level_name, tg)
"""

BLOCK_TAIL = """end

local function au_compact()
	local w = 0
	for r = 1, au_n do
		local s = au_self[r]
		if s then
			w = w + 1
			if w ~= r then
				au_self[w] = s
				au_lvl[w] = au_lvl[r]
				local k = au_key[r]
				au_key[w] = k
				au_index[k] = w
			end
		end
	end
	for r = w + 1, au_n do
		au_self[r] = nil
		au_lvl[r] = nil
		au_key[r] = nil
	end
	au_n = w
	au_holes = 0
end

local function anomaly_updates_dispatch()
	local tg = time_global()
	local selves, lvls = au_self, au_lvl
	local body = anomaly_update_body
	for i = 1, au_n do
		local s = selves[i]
		if s and tg >= s.on_update_time then
			body(s, lvls[i], tg)
		end
	end
	if au_holes > 0 then
		au_compact()
	end
end

-- called from unregister_callback / register_callback above
function anomaly_update_remove(key)
	local i = au_index[key]
	if not i then return false end
	au_index[key] = nil
	au_self[i] = false
	au_lvl[i] = false
	au_key[i] = false
	au_holes = au_holes + 1
	return true
end

function register_anomaly_update(key, obj, level_name)
	if callbacks[key] then
		unregister_callback(key)
	end
	local n = au_n + 1
	au_n = n
	au_self[n] = obj
	au_lvl[n] = level_name
	au_key[n] = key
	au_index[key] = n
	callbacks[key] = {
		callback = "actor_on_update",
		func = anomaly_updates_dispatch,
		throttled = true
	}
	printf("registering callback %s, key %s", "actor_on_update", key)
	if not au_registered then
		au_registered = true
		RegisterScriptCallback("actor_on_update", anomaly_updates_dispatch)
	end
	return key
end

function anomaly_update_count()
	return au_n, au_holes
end

"""


def _crlf(s: str) -> str:
    """This source file may itself be CRLF; normalise before re-inflating."""
    return s.replace("\r\n", "\n").replace("\n", NL)


def _sub(text: str, old: str, new: str) -> str:
    old = _crlf(old)
    new = _crlf(new)
    n = text.count(old)
    assert n == 1, f"expected 1 hit, got {n} for:\n{old!r}"
    return text.replace(old, new)


def _dedent_one_tab(body: str) -> str:
    out = []
    for line in body.split(NL):
        out.append(line[1:] if line.startswith("\t") else line)
    return NL.join(out)


def patch(text: str) -> str:
    # 1/2. make the two public callback helpers aware of the throttled entries,
    #      so unregister_callbacks() and a same-key re-register keep working.
    text = _sub(text, REG_OLD, REG_NEW)
    text = _sub(text, UNREG_OLD, UNREG_NEW)

    # 3. lift the closure body out to module level, verbatim, minus one tab.
    head = _crlf(CLOSURE_HEAD)
    tail = _crlf(CLOSURE_TAIL)
    assert text.count(head) == 1, "closure head anchor"
    i = text.index(head)
    j = text.index(tail, i)
    assert text.count(tail) == 1, "closure tail anchor"
    body = text[i + len(head):j]          # the part after the throttle check
    body = _dedent_one_tab(body)

    call = "\tregister_anomaly_update(self.update_key, self, level_name)" + NL
    text = text[:i] + call + text[j + len(tail):]

    # 4. the dispatcher block, right before the net_spawn patch it serves.
    block = _crlf(BLOCK_HEAD) + body + _crlf(BLOCK_TAIL)
    text = _sub(text, ANCHOR_SPAWN, block + ANCHOR_SPAWN)
    return text


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out", type=Path)
    ap.add_argument("--src", type=Path, default=None)
    ap.add_argument("--pristine", action="store_true",
                    help="patch the unmodified mod copy instead of the ALAO one")
    a = ap.parse_args()
    src = a.src or (SRC_PRISTINE if a.pristine else SRC_ALAO)
    raw = src.read_bytes().decode("utf-8")
    out_text = patch(raw)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_bytes(out_text.encode("utf-8"))
    print(f"wrote {a.out} ({a.out.stat().st_size} bytes, source {src} {src.stat().st_size})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
