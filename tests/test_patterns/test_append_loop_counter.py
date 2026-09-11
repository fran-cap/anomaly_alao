"""append_loop_counter (I-001): hoisted counter for append-only tables in loops.

The transform is worth ~13x with the JIT on, and it is also the one transform
in ALAO that can silently corrupt a table if the aliasing proof is wrong. So
most of this module is about what the analyzer must *refuse*.
"""

import pytest

from conftest import find_one, findings_named, pattern_names, luajit_compiles


PATTERN = "append_loop_counter"


# --------------------------------------------------------------------------
# hits
# --------------------------------------------------------------------------

def test_index_append_in_loop_is_green(analyze):
    findings = analyze("""
        function build(n)
            local out = {}
            for i = 1, n do
                out[#out+1] = i * 2
            end
            return out
        end
    """)
    f = find_one(findings, PATTERN)
    assert f.severity == "GREEN"
    assert f.line_num == 3
    assert f.details["table"] == "out"
    assert f.details["seed"] == "0"
    assert f.details["site_count"] == 1


def test_table_insert_in_loop_is_claimed_by_the_counter(analyze):
    """One winner: table_insert_append must not also fire on the same call."""
    findings = analyze("""
        function build(n)
            local out = {}
            for i = 1, n do
                table.insert(out, i * 2)
            end
            return out
        end
    """)
    f = find_one(findings, PATTERN)
    assert f.severity == "GREEN"
    assert findings_named(findings, "table_insert_append") == []


def test_table_insert_outside_a_loop_still_belongs_to_table_insert_append(analyze):
    findings = analyze("""
        function build()
            local out = {}
            table.insert(out, 1)
            return out
        end
    """)
    assert findings_named(findings, PATTERN) == []
    assert len(findings_named(findings, "table_insert_append")) == 1


def test_seed_is_hash_t_when_the_table_is_not_provably_empty(analyze):
    findings = analyze("""
        function build(n)
            local out = {}
            out[#out+1] = "head"
            for i = 1, n do
                out[#out+1] = i * 2
            end
            return out
        end
    """)
    f = find_one(findings, PATTERN)
    assert f.details["seed"] == "#out"


def test_positional_constructor_seeds_from_hash_t(analyze):
    findings = analyze("""
        function build(n)
            local out = {1, 2, 3}
            for i = 1, n do
                out[#out+1] = i * 2
            end
            return out
        end
    """)
    f = find_one(findings, PATTERN)
    assert f.details["seed"] == "#out"


def test_value_that_may_be_nil_is_yellow_not_green(analyze):
    findings = analyze("""
        function build(src)
            local out = {}
            for _, v in pairs(src) do
                out[#out+1] = v
            end
            return out
        end
    """)
    f = find_one(findings, PATTERN)
    assert f.severity == "YELLOW", "a variable can be nil; nil appends diverge"


def test_two_append_sites_in_one_loop(analyze):
    findings = analyze("""
        function build(n)
            local out = {}
            for i = 1, n do
                if i % 2 == 0 then
                    out[#out+1] = i * 2
                else
                    table.insert(out, i * 3)
                end
            end
            return out
        end
    """)
    f = find_one(findings, PATTERN)
    assert f.details["site_count"] == 2


# --------------------------------------------------------------------------
# near misses: everything the proof has to refuse
# --------------------------------------------------------------------------

REJECTED = {
    "escapes_via_a_call": """
        function build(n)
            local out = {}
            for i = 1, n do
                out[#out+1] = i
                consume(out)
            end
            return out
        end
    """,
    "escapes_as_a_table_insert_position_arg": """
        function build(n)
            local out = {}
            for i = 1, n do
                table.insert(out, 1, i)
            end
            return out
        end
    """,
    "captured_by_a_closure": """
        function build(n)
            local out = {}
            for i = 1, n do
                local cb = function() out[#out+1] = i end
                cb()
            end
            return out
        end
    """,
    "table_remove_inside_the_loop": """
        function build(n)
            local out = {}
            for i = 1, n do
                out[#out+1] = i
                table.remove(out)
            end
            return out
        end
    """,
    "table_sort_inside_the_loop": """
        function build(n)
            local out = {}
            for i = 1, n do
                out[#out+1] = i
                table.sort(out)
            end
            return out
        end
    """,
    "hash_t_read_for_another_purpose": """
        function build(n)
            local out = {}
            for i = 1, n do
                out[#out+1] = i
                if #out > 3 then break end
            end
            return out
        end
    """,
    "indexed_write_with_another_key": """
        function build(n)
            local out = {}
            for i = 1, n do
                out[#out+1] = i
                out[1] = 0
            end
            return out
        end
    """,
    "assigned_to_another_name": """
        function build(n)
            local out = {}
            for i = 1, n do
                out[#out+1] = i
                local alias = out
                alias[1] = 0
            end
            return out
        end
    """,
    "reassigned_inside_the_loop": """
        function build(n)
            local out = {}
            for i = 1, n do
                out = {}
                out[#out+1] = i
            end
            return out
        end
    """,
    "shadowed_inside_the_loop": """
        function build(n)
            local out = {}
            for i = 1, n do
                local out = {}
                out[#out+1] = i
            end
            return out
        end
    """,
    "keyed_table_constructor": """
        function build(n)
            local out = {mode = 1}
            for i = 1, n do
                out[#out+1] = i
            end
            return out
        end
    """,
    "holey_table_constructor": """
        function build(n)
            local out = {[2] = 5}
            for i = 1, n do
                out[#out+1] = i
            end
            return out
        end
    """,
    "not_a_local_at_all": """
        function build(out, n)
            for i = 1, n do
                out[#out+1] = i
            end
            return out
        end
    """,
    "declared_in_an_outer_block": """
        function build(n)
            local out = {}
            if n > 0 then
                for i = 1, n do
                    out[#out+1] = i
                end
            end
            return out
        end
    """,
    "loop_header_reads_the_table": """
        function build(n)
            local out = {}
            while #out < n do
                out[#out+1] = 1
            end
            return out
        end
    """,
    "touched_between_declaration_and_loop": """
        function build(n)
            local out = {}
            consume(out)
            for i = 1, n do
                out[#out+1] = i
            end
            return out
        end
    """,
    "value_reads_the_table_back": """
        function build(n)
            local out = {}
            for i = 1, n do
                out[#out+1] = #out
            end
            return out
        end
    """,
}


@pytest.mark.parametrize("name", sorted(REJECTED))
def test_rejected_shapes_produce_no_finding(analyze, name):
    findings = analyze(REJECTED[name])
    assert findings_named(findings, PATTERN) == [], (
        f"{name} must not be rewritten; findings: {sorted(pattern_names(findings))}"
    )


# --------------------------------------------------------------------------
# the rewrite itself
# --------------------------------------------------------------------------

def test_rewrite_shape(transform):
    out = transform("""
        function build(n)
            local out = {}
            for i = 1, n do
                out[#out+1] = i * 2
            end
            return out
        end
    """, fix_yellow=False)
    assert "local out_n = 0" in out
    assert "out_n = out_n + 1; out[out_n] = i * 2" in out
    assert "#out+1" not in out
    ok, err = luajit_compiles(out)
    assert ok, err


def test_counter_name_dodges_an_existing_local(transform):
    out = transform("""
        function build(n)
            local out_n = 7
            local out = {}
            for i = 1, n do
                out[#out+1] = i * 2
            end
            return out, out_n
        end
    """, fix_yellow=False)
    assert "local out_n_alao = 0" in out
    assert "out_n_alao = out_n_alao + 1; out[out_n_alao] = i * 2" in out
    assert "local out_n = 7" in out
    ok, err = luajit_compiles(out)
    assert ok, err


def test_yellow_rewrite_only_lands_with_fix_yellow(transform):
    src = """
        function build(src)
            local out = {}
            for _, v in pairs(src) do
                out[#out+1] = v
            end
            return out
        end
    """
    assert "out_n" not in transform(src)
    assert "out_n" in transform(src, fix_yellow=True)


def test_fix_is_idempotent(transform):
    src = """
        function build(n)
            local out = {}
            for i = 1, n do
                table.insert(out, i * 2)
            end
            return out
        end
    """
    once = transform(src)
    twice = transform(once, name="pass2.script")
    assert once == twice


# --------------------------------------------------------------------------
# differential execution
# --------------------------------------------------------------------------

CASES = {
    "plain": ("""
        function build(n)
            local out = {}
            for i = 1, n do
                out[#out+1] = i * 2
            end
            return table.concat(out, ",") .. "|" .. #out
        end
    """, (7,)),
    "table_insert": ("""
        function build(n)
            local out = {}
            for i = 1, n do
                table.insert(out, i * 2)
            end
            return table.concat(out, ",") .. "|" .. #out
        end
    """, (7,)),
    "empty_loop": ("""
        function build(n)
            local out = {}
            for i = 1, n do
                out[#out+1] = i * 2
            end
            return table.concat(out, ",") .. "|" .. #out
        end
    """, (0,)),
    "early_break": ("""
        function build(n)
            local out = {}
            for i = 1, n do
                if i > 3 then break end
                out[#out+1] = i * 2
            end
            return table.concat(out, ",") .. "|" .. #out
        end
    """, (20,)),
    "branching_sites": ("""
        function build(n)
            local out = {}
            for i = 1, n do
                if i % 2 == 0 then
                    out[#out+1] = i * 2
                else
                    table.insert(out, i * 3)
                end
            end
            return table.concat(out, ",") .. "|" .. #out
        end
    """, (9,)),
    "nested_loops": ("""
        function build(n)
            local out = {}
            for i = 1, n do
                for j = 1, 3 do
                    out[#out+1] = i * j
                end
            end
            return table.concat(out, ",") .. "|" .. #out
        end
    """, (4,)),
    "seeded_non_empty": ("""
        function build(n)
            local out = {1, 2, 3}
            for i = 1, n do
                out[#out+1] = i * 2
            end
            return table.concat(out, ",") .. "|" .. #out
        end
    """, (5,)),
    "seeded_by_a_prior_append": ("""
        function build(n)
            local out = {}
            out[#out+1] = 99
            for i = 1, n do
                out[#out+1] = i * 2
            end
            return table.concat(out, ",") .. "|" .. #out
        end
    """, (5,)),
    "two_loops_over_one_table": ("""
        function build(n)
            local out = {}
            for i = 1, n do
                out[#out+1] = i
            end
            for i = 1, n do
                out[#out+1] = i * 10
            end
            return table.concat(out, ",") .. "|" .. #out
        end
    """, (4,)),
    "loop_runs_zero_times": ("""
        function build(n)
            local out = {}
            while false do
                out[#out+1] = 1
            end
            return table.concat(out, ",") .. "|" .. #out
        end
    """, (3,)),
    "repeat_until": ("""
        function build(n)
            local out = {}
            local i = 0
            repeat
                i = i + 1
                out[#out+1] = i * 2
            until i >= n
            return table.concat(out, ",") .. "|" .. #out
        end
    """, (5,)),
    "return_from_inside_the_loop": ("""
        function build(n)
            local out = {}
            for i = 1, n do
                out[#out+1] = i
                if i == 3 then
                    return table.concat(out, ",") .. "|" .. #out
                end
            end
            return table.concat(out, ",") .. "|" .. #out
        end
    """, (9,)),
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_differential(transform, run_both, name):
    src, args = CASES[name]
    out = transform(src, fix_yellow=True)
    assert out != src.replace("\n    ", "\n"), f"{name}: nothing was rewritten"
    run_both(src, out, "build", *args)


def test_holes_from_a_nil_append_are_why_that_case_is_yellow(transform, run_both):
    """The GREEN proof exists exactly to keep this shape out of --fix.

    `t[#t+1] = nil` doesn't grow #t, so the next append overwrites the same
    slot while a counter would move on. This is provably-may-be-nil, so it
    only ever reaches the transformer under --fix-yellow, and a user who asks
    for YELLOW is asking for exactly this kind of judgement call.
    """
    src = """
        function build()
            local src = {10, 20, 30}
            local out = {}
            for _, v in pairs(src) do
                out[#out+1] = v
            end
            return #out
        end
    """
    assert "out_n" not in transform(src), "must not be touched by plain --fix"
    out = transform(src, fix_yellow=True)
    ok, err = luajit_compiles(out)
    assert ok, err
    # no nils in the input -> the two forms agree
    run_both(src, out, "build")


# --------------------------------------------------------------------------
# interaction with the other edit families
# --------------------------------------------------------------------------

def test_cached_global_inside_an_append_value_is_folded_in(transform, run_both):
    """The I-008 containment path: a smaller rewrite landing inside our append
    must be folded into the counter statement, not fight it for the span."""
    src = """
        function build(n)
            local out = {}
            for i = 1, n do
                out[#out+1] = tostring(i) .. "x"
            end
            local a = tostring(1)
            local b = tostring(2)
            local c = tostring(3)
            return table.concat(out, ",") .. a .. b .. c
        end
    """
    out = transform(src)
    assert "local tostr = tostring" in out
    assert 'out_n = out_n + 1; out[out_n] = tostr(i) .. "x"' in out
    run_both(src, out, "build", 4)


def test_debug_commented_out_next_to_an_append(transform, run_both):
    src = """
        function build(n)
            local out = {}
            for i = 1, n do
                printf("hi %s", i)
                out[#out+1] = i * 2
            end
            return table.concat(out, ",")
        end
    """
    out = transform(src, fix_debug=True)
    assert '-- printf("hi %s", i)' in out
    assert "out_n = out_n + 1; out[out_n] = i * 2" in out
    run_both(src, out, "build", 4)


def test_two_loops_over_one_table_reseed_from_hash_t(transform, run_both):
    src = """
        function build(n)
            local out = {}
            for i = 1, n do
                out[#out+1] = i * 2
            end
            for i = 1, n do
                out[#out+1] = i * 3
            end
            return table.concat(out, ",") .. "|" .. #out
        end
    """
    out = transform(src)
    assert out.count("local out_n") == 2
    assert "local out_n = 0" in out
    assert "local out_n = #out" in out
    run_both(src, out, "build", 4)
