"""I-013 / I-010: LuaJIT 2.0 trace-abort classification and per-frame detection.

Every expectation in here traces back to a measurement in
`lab/reports/luajit20-nyi.md`, produced by `lab/tools/nyi_probe.py`. If one of
these starts failing, re-run the probe before editing the test - the constant
tables are supposed to describe the VM, not the other way round.
"""

import pytest

from ast_analyzer import (
    ASTAnalyzer,
    LUAJIT20_NYI_FUNCS,
    LUAJIT20_NYI_VARIANTS,
    _is_per_frame_callback_name,
)
from conftest import findings_named, find_one


@pytest.fixture
def classify(write_script):
    def _classify(src):
        path = write_script(src)
        analyzer = ASTAnalyzer()
        findings = analyzer.analyze_file(path)
        by_name = {}
        for info in analyzer.jit_modes.values():
            by_name[info.func_name] = info
        return by_name, findings

    return _classify


# ---------------------------------------------------------------------------
# the classifier
# ---------------------------------------------------------------------------

def test_pure_arithmetic_body_is_compiled(classify):
    by_name, _ = classify("""
        function actor_on_update()
            local s = 0
            for i = 1, 10 do
                s = s + math.floor(i * 1.5) + math.sqrt(i)
            end
            return s
        end
    """)
    assert by_name["actor_on_update"].mode == "compiled"
    assert by_name["actor_on_update"].sites == []


def test_concat_makes_a_body_interpreted(classify):
    """BC_CAT is NYI in LuaJIT 2.0 - even two operands, even outside a loop."""
    by_name, _ = classify("""
        function actor_on_update()
            local s = "a" .. "b"
            return s
        end
    """)
    info = by_name["actor_on_update"]
    assert info.mode == "interpreted"
    assert any("BC_CAT" in r for r in info.reasons)


def test_chained_concat_counts_once(classify):
    """`a .. b .. c` is a nest of Concat nodes but one BC_CAT."""
    by_name, _ = classify("""
        function actor_on_update()
            return "a" .. "b" .. "c" .. "d"
        end
    """)
    sites = by_name["actor_on_update"].sites
    assert len([s for s in sites if s.name == ".."]) == 1


def test_pairs_aborts_but_ipairs_does_not(classify):
    by_name, _ = classify("""
        function a_on_update(t)
            for k, v in pairs(t) do print(k) end
        end
        function b_on_update(t)
            local n = 0
            for i, v in ipairs(t) do n = n + v end
            return n
        end
    """)
    assert by_name["a_on_update"].mode == "interpreted"
    assert by_name["b_on_update"].mode == "compiled"


def test_two_arg_table_insert_compiles_three_arg_does_not(classify):
    """Measured: table.insert(t, v) has a fast path, the positional form has not."""
    by_name, _ = classify("""
        function a_on_update(t)
            table.insert(t, 1)
        end
        function b_on_update(t)
            table.insert(t, 1, 2)
        end
    """)
    assert by_name["a_on_update"].mode == "compiled"
    info = by_name["b_on_update"]
    assert info.mode == "interpreted"
    assert any("table.insert" in r for r in info.reasons)


def test_engine_call_makes_a_body_interpreted(classify):
    """Any non-fastfunc C function aborts with NYICF, and that is every engine export."""
    by_name, _ = classify("""
        function actor_on_update()
            local o = level.object_by_id(1)
            return o
        end
    """)
    info = by_name["actor_on_update"]
    assert info.mode == "interpreted"
    assert any("NYICF" in r for r in info.reasons)


def test_engine_method_call_is_recognised(classify):
    by_name, _ = classify("""
        function actor_on_update()
            local p = db.actor:position()
            return p
        end
    """)
    assert by_name["actor_on_update"].mode == "interpreted"


def test_unknown_method_is_assumed_compilable(classify):
    """The engine-method list is a whitelist on purpose: we under-report."""
    by_name, _ = classify("""
        function actor_on_update(obj)
            return obj:some_mod_helper_method()
        end
    """)
    assert by_name["actor_on_update"].mode == "compiled"


@pytest.mark.parametrize("method", ["section_name", "profile_name"])
def test_se_object_name_getters_are_engine_calls(classify, method):
    """I-042: both were missing from ENGINE_NYI_METHODS.

    `se_obj:section_name()` / `:profile_name()` are LuaBind exports on the
    server object, same as `:id()` - a body whose only engine call was one of
    them used to come back `compiled`, which is exactly the wrong answer for
    every mode-gated decision downstream.
    """
    by_name, _ = classify("""
        function actor_on_update(se_obj)
            local s = se_obj:%s()
            return s
        end
    """ % method)
    info = by_name["actor_on_update"]
    assert info.mode == "interpreted"
    assert any(method in r and "NYICF" in r for r in info.reasons)


def test_conditional_only_abort_is_mixed(classify):
    by_name, _ = classify("""
        function actor_on_update(flag)
            local s = 0
            for i = 1, 10 do
                if flag then
                    s = s + string.format("%d", i):len()
                end
            end
            return s
        end
    """)
    assert by_name["actor_on_update"].mode == "mixed"


def test_closure_creation_aborts(classify):
    """BC_FNEW is NYI; a closure anywhere in a per-frame body kills its trace."""
    by_name, _ = classify("""
        function actor_on_update()
            local f = function() return 1 end
            return f()
        end
    """)
    info = by_name["actor_on_update"]
    assert info.mode == "interpreted"
    assert any("BC_FNEW" in r for r in info.reasons)


def test_nested_closure_sites_belong_to_the_closure(classify):
    """A closure gets its own trace, so its NYI sites are not ours."""
    by_name, _ = classify("""
        function outer_helper()
            local cb = function(t)
                for k, v in pairs(t) do print(k) end
            end
            return cb
        end
    """)
    # the anonymous body owns the pairs abort
    anon = by_name["<anon>"]
    assert any("pairs" in r for r in anon.reasons)
    # the outer function only sees the closure creation, not the pairs
    assert not any("pairs" in r for r in by_name["outer_helper"].reasons)


# ---------------------------------------------------------------------------
# findings and the annotations other passes consume
# ---------------------------------------------------------------------------

def test_jit_mode_finding_for_interpreted_per_frame_body(classify):
    _, findings = classify("""
        function actor_on_update()
            local s = "a" .. "b"
            return s
        end
    """)
    f = find_one(findings, "jit_mode")
    assert f.severity == "RED"
    assert f.details["jit_mode"] == "interpreted"
    assert f.details["abort_lines"]
    assert any("BC_CAT" in r for r in f.details["abort_reasons"])


def test_no_jit_mode_finding_for_a_compiled_body(classify):
    _, findings = classify("""
        function actor_on_update()
            local s = 0
            for i = 1, 10 do s = s + i end
            return s
        end
    """)
    assert findings_named(findings, "jit_mode") == []


def test_table_insert_finding_carries_the_mode(classify):
    _, findings = classify("""
        function actor_on_update(t)
            local o = level.object_by_id(1)
            table.insert(t, o)
        end
    """)
    f = find_one(findings, "table_insert_append")
    assert f.details["jit_mode"] == "interpreted"


def test_uncached_globals_finding_carries_the_mode(classify):
    _, findings = classify("""
        function actor_on_update(t)
            local a = math.floor(1.5)
            local b = math.floor(2.5)
            local c = math.floor(3.5)
            local d = math.floor(4.5)
            return a + b + c + d
        end
    """)
    f = find_one(findings, "uncached_globals_summary")
    assert f.details["jit_mode"] == "compiled"


def test_per_frame_finding_carries_the_mode(classify):
    _, findings = classify("""
        function actor_on_update(t)
            for k, v in pairs(t) do print(k) end
        end
    """)
    f = find_one(findings, "per_frame_callback")
    assert f.details["jit_mode"] == "interpreted"
    assert "interpreted (LuaJIT 2.0)" in f.message


# ---------------------------------------------------------------------------
# I-010: the widened per-frame detection
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name, expected", [
    ("actor_on_update", True),
    ("npc_on_update", True),
    ("monster_on_update", True),
    ("physic_object_on_update", True),
    ("squad_on_update", True),
    ("bas_actor_on_update", True),      # a mod's own prefixed copy
    ("actor_on_first_update", False),   # runs once
    ("client_on_first_update", False),
    ("timed_update", False),            # deliberately not per-frame
    ("send_update", False),
    ("on_game_start", False),
])
def test_per_frame_callback_name_detection(name, expected):
    assert _is_per_frame_callback_name(name) is expected


def test_update_method_counts_as_per_frame(write_script):
    """I-010: the 100-odd class :update methods were previously invisible."""
    path = write_script("""
        CFoo = {}
        function CFoo:update(delta)
            for k, v in pairs(self.items) do print(k) end
        end
    """)
    analyzer = ASTAnalyzer()
    findings = analyzer.analyze_file(path)
    names = [cb.name for cb in analyzer.per_frame_callbacks]
    assert names == ["CFoo:update"]
    assert find_one(findings, "per_frame_callback").details["callback_name"] == "CFoo:update"


def test_capital_update_method_counts_too(write_script):
    path = write_script("""
        CBar = {}
        function CBar:Update()
            local s = "a" .. "b"
        end
    """)
    analyzer = ASTAnalyzer()
    analyzer.analyze_file(path)
    assert [cb.name for cb in analyzer.per_frame_callbacks] == ["CBar:Update"]


def test_local_function_callback_counts_as_per_frame(write_script):
    """I-042: `local function actor_on_update` + RegisterScriptCallback.

    The commonest shape in mod scripts (36 of the 144 live per-frame
    registrations in the GAMMA profile) and the visitor used to skip it, so
    every one of those bodies was invisible to the classifier.
    """
    path = write_script("""
        local function actor_on_update()
            local s = "a" .. "b"
        end
        function on_game_start()
            RegisterScriptCallback("actor_on_update", actor_on_update)
        end
    """)
    analyzer = ASTAnalyzer()
    findings = analyzer.analyze_file(path)
    assert [cb.name for cb in analyzer.per_frame_callbacks] == ["actor_on_update"]
    assert find_one(findings, "jit_mode").details["jit_mode"] == "interpreted"
    cb = analyzer.per_frame_callbacks[0]
    assert cb.end_line > cb.start_line


def test_local_function_with_an_ordinary_name_is_not_per_frame(write_script):
    path = write_script("""
        local function build_table()
            local s = "a" .. "b"
        end
    """)
    analyzer = ASTAnalyzer()
    analyzer.analyze_file(path)
    assert analyzer.per_frame_callbacks == []


def test_non_update_method_is_not_per_frame(write_script):
    path = write_script("""
        CBaz = {}
        function CBaz:save(packet)
            local s = "a" .. "b"
        end
    """)
    analyzer = ASTAnalyzer()
    analyzer.analyze_file(path)
    assert analyzer.per_frame_callbacks == []


def test_per_frame_end_line_survives_a_nested_per_frame_body(write_script):
    """The end_line used to be written to per_frame_callbacks[-1]."""
    path = write_script("""
        function actor_on_update()
            local t = {}
        end
        CFoo = {}
        function CFoo:update()
            local u = {}
        end
    """)
    analyzer = ASTAnalyzer()
    analyzer.analyze_file(path)
    for cb in analyzer.per_frame_callbacks:
        assert cb.end_line > cb.start_line


# ---------------------------------------------------------------------------
# the constant tables themselves
# ---------------------------------------------------------------------------

def test_nyi_tables_do_not_contradict_each_other():
    assert not (set(LUAJIT20_NYI_FUNCS) & set(LUAJIT20_NYI_VARIANTS))


def test_compilable_stdlib_is_absent_from_the_nyi_table():
    """Measured as compiled; if one of these lands in the table it is a bug."""
    for name in ("ipairs", "string.sub", "string.byte", "string.len", "tonumber",
                 "type", "rawget", "rawset", "select", "table.getn",
                 "math.floor", "math.sqrt", "math.random", "math.pow", "pcall"):
        assert name not in LUAJIT20_NYI_FUNCS
