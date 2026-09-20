-- @pattern make_callback_dispatch_delivery
-- @title I-051 delivery form: file-edited axr_main vs monkey-patched module table
-- @status proposed
-- @iters 4 12 73 125
-- @doc_at 73
-- @notes I-051. This pair does NOT measure the dispatch optimisation - bench/make_callback_dispatch.lua
-- @notes does that. It measures whether DELIVERING that optimisation as a monkey patch (a separate
-- @notes zzz_*.script that swaps axr_main.make_callback at on_game_start, form B, the shippable mod)
-- @notes costs anything per dispatch against editing axr_main.script itself (form A, the upstream PR).
-- @notes THE PASS CONDITION HERE IS 1.00x, NOT 1.15x. A G2 "fail" is the expected and wanted result:
-- @notes it means the two delivery forms are indistinguishable and the conflict-free one is free.
-- @notes Both arms are called the way _g.SendScriptCallback calls it, `axr_main.make_callback(...)`,
-- @notes so both pay the same module-table index. In both forms `intercepts` and `order_list` are
-- @notes upvalues of the dispatcher; form B gets `intercepts` through debug.getupvalue at install
-- @notes time (once), not per dispatch. The only real difference is in callback_set/unset, which
-- @notes form B wraps - registration cost, not dispatch cost, and registration happens ~1800 times
-- @notes at load and rarely after.
-- @notes K is the listener count: actor_on_update 73-125, npc_on_update 12, monster_on_update 4.
-- @setup
local acc = 0

-- ---------------------------------------------------------------- form A
-- the file-edit: dispatcher and order cache are file-locals of axr_main itself
local axr_a = {}
do
    local intercepts = { actor_on_update = {} }
    local next_index = { actor_on_update = 1 }
    local order_list = {}

    local function rebuild_order(name)
        local t = intercepts[name]
        if (not t) then order_list[name] = nil return end
        local keys, n = {}, 0
        for k in pairs(t) do n = n + 1; keys[n] = k end
        table.sort(keys, function(a,b) return t[a] < t[b] end)
        order_list[name] = keys
        return keys
    end

    function axr_a.callback_set(name, f)
        intercepts[name][f] = next_index[name]
        next_index[name] = next_index[name] + 1
        rebuild_order(name)
    end

    function axr_a.make_callback(name, ...)
        local t = intercepts[name]
        if (t) then
            local list = order_list[name] or rebuild_order(name)
            for i = 1, #list do
                local func_or_userdata = list[i]
                if (t[func_or_userdata] ~= nil) then
                    if (type(func_or_userdata) == "function") then
                        func_or_userdata(...)
                    elseif (func_or_userdata[name]) then
                        func_or_userdata[name](func_or_userdata, ...)
                    end
                end
            end
        end
    end
end

-- ---------------------------------------------------------------- form B
-- the monkey patch: a stock axr_main, then a separate chunk that reaches
-- `intercepts` via debug.getupvalue and swaps the module fields
local axr_b = {}
do
    local intercepts = { actor_on_update = {} }
    local next_index = { actor_on_update = 1 }
    function axr_b.callback_set(name, f)
        intercepts[name][f] = next_index[name]
        next_index[name] = next_index[name] + 1
    end
    function axr_b.make_callback(name, ...)          -- stock, replaced below
        for f, v in pairs(intercepts[name]) do
            if (type(f) == "function") then f(...) end
        end
    end
end
do
    -- everything in here is the separate script, with no lexical access to
    -- axr_b's locals: it has to fish `intercepts` out the way the mod does
    local intercepts
    for i = 1, 60 do
        local n, v = debug.getupvalue(axr_b.make_callback, i)
        if n == nil then break end
        if n == "intercepts" then intercepts = v end
    end
    local order_list = {}
    local orig_set = axr_b.callback_set

    local function build(name)
        local t = intercepts[name]
        if (not t) then order_list[name] = nil return nil end
        local keys, n = {}, 0
        for k in pairs(t) do n = n + 1; keys[n] = k end
        table.sort(keys, function(a,b) return t[a] < t[b] end)
        order_list[name] = keys
        return keys
    end

    axr_b.callback_set = function(name, f)
        orig_set(name, f)
        order_list[name] = nil
    end

    axr_b.make_callback = function(name, ...)
        local t = intercepts[name]
        if (t) then
            local list = order_list[name] or build(name)
            for i = 1, #list do
                local func_or_userdata = list[i]
                if (t[func_or_userdata] ~= nil) then
                    if (type(func_or_userdata) == "function") then
                        func_or_userdata(...)
                    elseif (func_or_userdata[name]) then
                        func_or_userdata[name](func_or_userdata, ...)
                    end
                end
            end
        end
    end
end

for i = 1, K do
    local f = function(binder, delta) acc = acc + delta end
    axr_a.callback_set("actor_on_update", f)
    axr_b.callback_set("actor_on_update", f)
end
axr_b.make_callback("actor_on_update", D, 0)     -- warm the lazy cache
local binder = D
-- @original
for r = 1, N do axr_a.make_callback("actor_on_update", binder, 1) end
-- @rewrite
for r = 1, N do axr_b.make_callback("actor_on_update", binder, 1) end
-- @sink
acc
