"""Hand patch for the live axr_main.script: spairs dispatch -> sorted array.

Byte-exact string surgery on the live winner (read-only source), written out to
the overlay.  Every replacement is asserted to hit exactly once, so a future
GAMMA update that touches these lines makes this script fail loudly rather than
silently emit a half-patched file.
"""
import sys
from pathlib import Path

SRC = Path(r"D:\GOG_Games\Gamma\S.T.A.L.K.E.R. GAMMA\Anomaly\gamedata\scripts\axr_main.script")

# The file is CRLF; keep it that way.
NL = "\r\n"


def sub(text, old, new):
    old = old.replace("\n", NL)
    new = new.replace("\n", NL)
    assert text.count(old) == 1, f"expected 1 hit, got {text.count(old)} for:\n{old!r}"
    return text.replace(old, new)


def patch(text: str) -> str:
    # 1. the order array + its rebuild, right after the priority bookkeeping
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

-- I-043: dispatch order, precomputed.
-- make_callback used to call spairs() on every SendScriptCallback, which in
-- GAMMA is the min-heap hspairs from _g_patches: a keys array, a safe_order
-- closure, an iterator closure and O(K log K) nested Lua comparisons, all to
-- walk a table that only changes when somebody registers or unregisters. So
-- keep the sorted array instead and rebuild it there.
-- order_list[name] is REPLACED, never edited in place, so a dispatch that is
-- already running keeps iterating its own snapshot - same as hspairs, which
-- collects its keys before the first listener runs.
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
""")

    # 2-4. every mutation of intercepts rebuilds the array
    text = sub(text, """	if (not intercepts[name]) then
		intercepts[name] = {}
		next_index[name] = 1
	else""", """	if (not intercepts[name]) then
		intercepts[name] = {}
		next_index[name] = 1
		rebuild_order(name)
	else""")

    text = sub(text, """	if (intercepts[name]) then
		intercepts[name][func_or_userdata] = next_index[name]
		next_index[name] = next_index[name] + 1
	else""", """	if (intercepts[name]) then
		intercepts[name][func_or_userdata] = next_index[name]
		next_index[name] = next_index[name] + 1
		rebuild_order(name)
	else""")

    text = sub(text, """	if (intercepts[name]) then
		intercepts[name][func_or_userdata] = nil
	else""", """	if (intercepts[name]) then
		intercepts[name][func_or_userdata] = nil
		rebuild_order(name)
	else""")

    # 5. the dispatcher itself
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
	else""")
    return text


if __name__ == "__main__":
    out = Path(sys.argv[1])
    src = SRC.read_bytes().decode("cp1251")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(patch(src).encode("cp1251"))
    print(f"wrote {out} ({out.stat().st_size} bytes, source {SRC.stat().st_size})")
