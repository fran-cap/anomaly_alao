-- @pattern make_callback_dispatch_work
-- @title live hspairs dispatch, listeners that do a little work (honest dispatch share)
-- @status proposed
-- @iters 4 12 20 60 73 125
-- @doc_at 125
-- @notes I-043. Same two dispatchers as make_callback_dispatch (the live hspairs one and the
-- @notes sorted-array candidate), but every listener does real work instead of returning at once:
-- @notes half bail on a cheap guard, half run ~16 float ops. The ratio here is what the change is
-- @notes worth end to end; the ABSOLUTE microseconds saved per dispatch are the same in both pairs,
-- @notes and that is the number the site arithmetic uses.
-- @setup
local acc = 0

-- ------------------------------------------------ _g_patches.script (loose, verbatim)
if not _G.__rawpairs then _G.__rawpairs = pairs end
local _p = _G.__rawpairs
_G.pairs = function(t, ...)
    local m = getmetatable(t)
    if not (m and m.__pairs) then return _p(t, ...) end
    return m.__pairs(t, ...)
end

local math_floor = math.floor
_G.nil_func = function() return nil end

local sift_down_order = function(keys, idx, end_idx, t, order)
    local hole_idx = idx
    local sifting_key = keys[hole_idx]
    local half_idx = math_floor(end_idx / 2)
    while hole_idx <= half_idx do
        local child = hole_idx * 2
        local right = child + 1
        if child < end_idx and order(t, keys[right], keys[child]) then
            child = right
        end
        if order(t, keys[child], sifting_key) then
            keys[hole_idx] = keys[child]
            hole_idx = child
        else
            break
        end
    end
    keys[hole_idx] = sifting_key
end

local sift_down_default = function(keys, idx, end_idx)
    local hole_idx = idx
    local sifting_key = keys[hole_idx]
    local half_idx = math_floor(end_idx / 2)
    while hole_idx <= half_idx do
        local child = hole_idx * 2
        local right = child + 1
        if child < end_idx and keys[right] < keys[child] then
            child = right
        end
        if keys[child] < sifting_key then
            keys[hole_idx] = keys[child]
            hole_idx = child
        else
            break
        end
    end
    keys[hole_idx] = sifting_key
end

local function safe_order(order)
    return function(t, a, b)
        local va = t[a]
        local vb = t[b]
        if va == nil then return false end
        if vb == nil then return true end
        return order(t, a, b)
    end
end

_G.hspairs = function(t, order)
    local n = 0
    local keys = {}
    for k in pairs(t) do
        n = n + 1
        keys[n] = k
    end
    if n == 0 then
        return nil_func
    end
    if n == 1 then
        local done = false
        return function()
            if done then return nil end
            done = true
            local val = t[keys[1]]
            if val ~= nil then
                return keys[1], val
            end
        end
    end
    if order then
        order = safe_order(order)
    end
    local sift_down = order and sift_down_order or sift_down_default
    for i = math_floor(n / 2), 1, -1 do
        sift_down(keys, i, n, t, order)
    end
    return function()
        while n > 0 do
            local best_key = keys[1]
            local val = t[best_key]
            keys[1] = keys[n]
            keys[n] = nil
            n = n - 1
            if n > 0 then
                sift_down(keys, 1, n, t, order)
            end
            if val ~= nil then
                return best_key, val
            end
        end
        return nil
    end
end

sort_func_keys_ascend = function(t, a, b) return a < b end
sort_func_values_ascend = function(t, a, b) return t[a] < t[b] end

-- this is the spairs the game actually calls
_G.spairs = function(t, order)
    if order and order ~= sort_func_keys_ascend then
        return hspairs(t, order)
    else
        return mspairs_default(t)
    end
end

-- ------------------------------------------------ axr_main.script (loose, verbatim)
local intercepts = { actor_on_update = {} }
local next_index = { actor_on_update = 1 }

local function callback_set(name, func_or_userdata)
    if (intercepts[name]) then
        intercepts[name][func_or_userdata] = next_index[name]
        next_index[name] = next_index[name] + 1
    end
end

local function make_callback(name, ...)
    if (intercepts[name]) then
        for func_or_userdata, v in spairs(intercepts[name], sort_func_values_ascend) do
            if (type(func_or_userdata) == "function") then
                func_or_userdata(...)
            elseif (func_or_userdata[name]) then
                func_or_userdata[name](func_or_userdata, ...)
            end
        end
    end
end

-- ------------------------------------------------ candidate
local order_list = {}

local function rebuild(name)
    local t = intercepts[name]
    local keys, n = {}, 0
    for k in pairs(t) do n = n + 1; keys[n] = k end
    table.sort(keys, function(a, b) return t[a] < t[b] end)
    order_list[name] = keys        -- replaced, never edited: an in-flight dispatch keeps its snapshot
    return keys
end

local function make_callback_fast(name, ...)
    local t = intercepts[name]
    if (t) then
        local list = order_list[name] or rebuild(name)
        for i = 1, #list do
            local func_or_userdata = list[i]
            if (t[func_or_userdata] ~= nil) then   -- hspairs skips entries whose value went nil
                if (type(func_or_userdata) == "function") then
                    func_or_userdata(...)
                elseif (func_or_userdata[name]) then
                    func_or_userdata[name](func_or_userdata, ...)
                end
            end
        end
    end
end

-- deterministic on purpose: a time-based throttle would make the work depend on N
for i = 1, K do
    local base = i % 64 + 1
    local idle = (i % 2 == 0)
    local f = function(binder, delta)
        if (idle) then return end
        local s = 0
        for j = 1, 16 do s = s + binder[(base + j) % 64 + 1] end
        acc = acc + delta + s * 0.0
    end
    callback_set("actor_on_update", f)
end
rebuild("actor_on_update")
local binder = D
-- @original
for r = 1, N do make_callback("actor_on_update", binder, 1) end
-- @rewrite
for r = 1, N do make_callback_fast("actor_on_update", binder, 1) end
-- @sink
acc
