-- @pattern debug_printf_logfile
-- @title live per-frame shape 3b: same printf, log() writes the line to a file -> statement removed
-- @status proposed
-- @notes UPPER bound for a reached printf. The engine's log() is a lua_CFunction that appends to the xray log buffer and (with the default log settings) flushes to disk; I cannot measure that offline, so this arm brackets it with a buffered io.write to a real file on the same disk. The truth is somewhere between this and debug_printf_logstub.lua, and closer to this one whenever the console/log flush is on.
-- @notes The file handle is opened once in setup; no flush per line, so this is still an optimistic upper bound for a flushing engine log.
-- @n 200000 60000
-- @setup
local string_gsub = string.gsub
local fh = assert(io.open(os.getenv("TEMP") .. "/alao_i044_logbench.txt", "w"))
_G.log = function(s) fh:write(s, "\n") end
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
