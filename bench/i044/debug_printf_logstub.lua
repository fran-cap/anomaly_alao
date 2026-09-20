-- @pattern debug_printf_logstub
-- @title live per-frame shape 3: printf("fmt %s (%s)", obj:name(), x) with the REAL _g.printf body, log() stubbed -> statement removed
-- @status proposed
-- @notes printf is NOT a no-op in Anomaly. The live winner of _g.script (vanilla db) defines it in Lua: tostring(fmt), then - when there is at least one vararg - a string.gsub over the format string with a CLOSURE substitution function (one closure allocated per call, one Lua call per %s), then log(fmt). There is no debug flag anywhere in it, so a printf that is reached always writes to the engine log.
-- @notes This arm stubs `log` with an empty Lua function, i.e. it measures everything EXCEPT the engine's log write. LOWER bound on the real cost. The upper bound is debug_printf_logfile.lua, which writes the line to a file instead.
-- @notes Argument shape copied from sr_monster.script fake_monster:update line 90: one format string, two %s, one engine getter and one tostring.
-- @n 300000 100000
-- @setup
local string_gsub = string.gsub
_G.log = function(s) end
function _G.printf(fmt, ...)
  if not (fmt) then return end
  local fmt = tostring(fmt)
  if (select('#', ...) >= 1) then
    local i = 0
    local p = {...}
    local function sr(a)
      i = i + 1
      if (type(p[i]) == 'userdata') then
        return 'userdata'
      end
      return tostring(p[i])
    end
    fmt = string_gsub(fmt, "%%s", sr)
  end
  log(fmt)
end
local obj = { nm = "walk_path_1" }
function obj:name() return self.nm end
local acc = 0
-- @original
for r = 1, N do
  printf("added action RUN to %s(%s)", obj:name(), tostring(r % 8))
  acc = acc + D[r % 64 + 1]
end
-- @rewrite
for r = 1, N do
  --printf("added action RUN to %s(%s)", obj:name(), tostring(r % 8))
  acc = acc + D[r % 64 + 1]
end
-- @sink
acc
