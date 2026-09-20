"""I-051: build the upstream diff for `gamedata/scripts/axr_main.script`.

Upstream for these lines is `themrdemonized/xray-monolith`, branch
`all-in-one-vs2022-wpo`, file `gamedata/scripts/axr_main.script`.  The priority
system arrived as PR #339 (Kutez) on the `kutez` branch as a separate
`axr_main_patches.script`; on the default branch it is inlined into
`axr_main.script`, and that inlined copy is what GAMMA ships loose.  Verified:
the live GAMMA file and the upstream file differ in exactly one line, a comment
in the `intercepts` table, and the region this patch touches is byte-identical
(sha256 0ded82c38b765181b60c58508a1b59d2364c9681c9ed1a8e209ba8e997d7a99b, 1758
bytes).

This is the FILE-EDIT form of the change, for a pull request.  The end-user mod
in `lab/mods/alao-make-callback-dispatch/` does the same thing as a monkey patch
so that it redistributes no game file and cannot conflict with anything.

    py -3.12 lab/tools/i051_upstream_patch.py --diff
    py -3.12 lab/tools/i051_upstream_patch.py --out patched_axr_main.script
    py -3.12 lab/tools/i051_upstream_patch.py --src <a copy of axr_main.script> --diff

Every substitution is asserted to hit exactly once, so a file that has drifted
fails loudly instead of being half-patched.
"""
from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path

LIVE = Path(r"D:\GOG_Games\Gamma\S.T.A.L.K.E.R. GAMMA\Anomaly\gamedata\scripts\axr_main.script")
UPSTREAM_URL = ("https://raw.githubusercontent.com/themrdemonized/xray-monolith/"
                "all-in-one-vs2022-wpo/gamedata/scripts/axr_main.script")


# Two lines in the shipped dispatcher carry a trailing space.  Source literals
# below are written without it (editors eat it), so put it back before matching.
TRAILING_SPACE = (
    "sort_func_values_ascend) do",
    '(type(func_or_userdata) == "function") then',
)


def fix_trailing(s: str) -> str:
    for frag in TRAILING_SPACE:
        s = s.replace(frag + "\n", frag + " \n")
    return s


def sub(text: str, old: str, new: str, nl: str) -> str:
    old, new = fix_trailing(old), fix_trailing(new)
    old, new = old.replace("\n", nl), new.replace("\n", nl)
    n = text.count(old)
    assert n == 1, f"expected 1 hit, got {n} for:\n{old!r}"
    return text.replace(old, new)


def patch(text: str) -> str:
    nl = "\r\n" if "\r\n" in text else "\n"

    # 1. the cached order, next to the priority bookkeeping it mirrors
    text = sub(text, """
local next_index = {}
for name,v in pairs(intercepts) do
	next_index[name] = 1
end
""", """
local next_index = {}
for name,v in pairs(intercepts) do
	next_index[name] = 1
end

-- Cached dispatch order.
-- make_callback used to call spairs() on every SendScriptCallback. With the
-- modded-exes _g_patches loaded that is hspairs, a min-heap: per dispatch a
-- keys array, a safe_order closure, an iterator closure, an O(K) heapify and
-- then an O(log K) sift-down per listener whose every comparison is two nested
-- Lua calls - all to walk a table that only changes when somebody registers or
-- unregisters. actor_on_update has ~100 listeners and fires every frame.
-- So keep the sorted order and rebuild it where the table is mutated.
-- order_list[name] is REPLACED, never edited in place, so a dispatch that is
-- already running keeps iterating its own snapshot - the same thing hspairs
-- gets from collecting its keys before the first listener runs.
local order_list = {}

local function rebuild_order(name)
	local t = intercepts[name]
	if (not t) then
		order_list[name] = nil
		return
	end
	local keys, n = {}, 0
	for k in pairs(t) do
		n = n + 1
		keys[n] = k
	end
	table.sort(keys, function(a,b) return t[a] < t[b] end)
	order_list[name] = keys
	return keys
end
""", nl)

    # 2-4. every mutation of intercepts invalidates the cached order
    text = sub(text, """	if (not intercepts[name]) then
		intercepts[name] = {}
		next_index[name] = 1
	else""", """	if (not intercepts[name]) then
		intercepts[name] = {}
		next_index[name] = 1
		rebuild_order(name)
	else""", nl)

    text = sub(text, """	if (intercepts[name]) then
		intercepts[name][func_or_userdata] = next_index[name]
		next_index[name] = next_index[name] + 1
	else""", """	if (intercepts[name]) then
		intercepts[name][func_or_userdata] = next_index[name]
		next_index[name] = next_index[name] + 1
		rebuild_order(name)
	else""", nl)

    text = sub(text, """	if (intercepts[name]) then
		intercepts[name][func_or_userdata] = nil
	else""", """	if (intercepts[name]) then
		intercepts[name][func_or_userdata] = nil
		rebuild_order(name)
	else""", nl)

    # 5. the dispatcher
    text = sub(text, """function make_callback(name,...)
	if (intercepts[name]) then
		for func_or_userdata, v in spairs(intercepts[name], sort_func_values_ascend) do
			if (type(func_or_userdata) == "function") then
				func_or_userdata(...)
			elseif (func_or_userdata[name]) then
				func_or_userdata[name](func_or_userdata,...)
			end
		end
	else""", """function make_callback(name,...)
	local t = intercepts[name]
	if (t) then
		local list = order_list[name] or rebuild_order(name)
		for i = 1,#list do
			local func_or_userdata = list[i]
			-- hspairs re-reads t[key] on every step and skips the entry when it
			-- has gone nil, so a listener unregistered by an earlier listener in
			-- THIS pass never fires. Same guard here.
			if (t[func_or_userdata] ~= nil) then
				if (type(func_or_userdata) == "function") then
					func_or_userdata(...)
				elseif (func_or_userdata[name]) then
					func_or_userdata[name](func_or_userdata,...)
				end
			end
		end
	else""", nl)
    return text


def read_source(src: str | None) -> tuple[str, str]:
    if src == "upstream":
        import urllib.request
        return urllib.request.urlopen(UPSTREAM_URL, timeout=60).read().decode("cp1251"), UPSTREAM_URL
    p = Path(src) if src else LIVE
    return p.read_bytes().decode("cp1251"), str(p)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", help="path to an axr_main.script, or the word 'upstream'")
    ap.add_argument("--diff", action="store_true", help="print a unified diff")
    ap.add_argument("--out", help="write the patched file here (encoding and newlines preserved)")
    a = ap.parse_args()

    text, where = read_source(a.src)
    out = patch(text)

    if a.diff or not a.out:
        before = text.replace("\r\n", "\n").splitlines()
        after = out.replace("\r\n", "\n").splitlines()
        sys.stdout.write("\n".join(difflib.unified_diff(
            before, after,
            "a/gamedata/scripts/axr_main.script",
            "b/gamedata/scripts/axr_main.script",
            lineterm="", n=3)) + "\n")
    if a.out:
        p = Path(a.out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(out.encode("cp1251"))
        print(f"\nwrote {p} ({p.stat().st_size} bytes) from {where}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
