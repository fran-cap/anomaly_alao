"""Transformer machinery: edit overlap resolution, enabler groups, encoding
preservation, .alao-bak handling and idempotence."""

import shutil

import pytest

from ast_transformer import ASTTransformer, SourceEdit
from conftest import luajit_compiles
from models import detect_file_encoding


def _transformer(source):
    t = ASTTransformer()
    t.source = source
    t._compute_line_offsets()
    return t


# ---------------------------------------------------------------------------
# _apply_edits: overlap resolution
# ---------------------------------------------------------------------------

def test_non_overlapping_edits_all_apply():
    t = _transformer("aaabbbccc")
    t.edits = [
        SourceEdit(0, 3, "AAA"),
        SourceEdit(6, 9, "CCC"),
    ]
    assert t._apply_edits() == "AAAbbbCCC"


def test_higher_priority_edit_wins_an_overlap():
    t = _transformer("aaabbbccc")
    t.edits = [
        SourceEdit(0, 6, "low", priority=0),
        SourceEdit(3, 9, "HIGH", priority=100),
    ]
    # the high-priority edit is admitted first; the low-priority one overlaps it
    assert t._apply_edits() == "aaaHIGH"


def test_overlap_is_rejected_regardless_of_which_side_it_starts_on():
    t = _transformer("0123456789")
    t.edits = [
        SourceEdit(4, 8, "MID", priority=100),
        SourceEdit(0, 5, "left", priority=0),   # ends inside MID
        SourceEdit(7, 10, "right", priority=0),  # starts inside MID
    ]
    # only MID applies; 0-3 and 8-9 survive untouched
    assert t._apply_edits() == "0123MID89"


def test_edits_touching_at_a_boundary_are_not_overlapping():
    t = _transformer("0123456789")
    t.edits = [
        SourceEdit(0, 5, "L", priority=100),
        SourceEdit(5, 10, "R", priority=0),
    ]
    assert t._apply_edits() == "LR"


def test_identical_insertions_at_one_position_are_deduped():
    t = _transformer("abc")
    t.edits = [
        SourceEdit(1, 1, "X"),
        SourceEdit(1, 1, "X"),
    ]
    assert t._apply_edits() == "aXbc"


def test_insertion_inside_an_admitted_replacement_is_dropped():
    t = _transformer("0123456789")
    t.edits = [
        SourceEdit(2, 8, "REPL", priority=100),
        SourceEdit(5, 5, "ins", priority=0),
    ]
    assert t._apply_edits() == "01REPL89"


# ---------------------------------------------------------------------------
# _apply_edits: enabler groups
# ---------------------------------------------------------------------------

def test_enabler_survives_when_one_replacement_survives():
    t = _transformer("aaabbb")
    t.edits = [
        SourceEdit(0, 0, "local c = x\n", group_id=1, is_enabler=True),
        SourceEdit(3, 6, "c()", group_id=1),
    ]
    out = t._apply_edits()
    assert out == "local c = x\naaac()"


def test_enabler_is_dropped_when_every_replacement_is_rejected():
    t = _transformer("aaabbb")
    t.edits = [
        SourceEdit(0, 6, "-- commented out", priority=200),
        SourceEdit(0, 0, "local c = x\n", group_id=1, is_enabler=True),
        SourceEdit(3, 6, "c()", group_id=1, priority=0),
    ]
    out = t._apply_edits()
    assert out == "-- commented out"
    assert "local c = x" not in out


def test_a_replacement_in_another_group_does_not_rescue_the_enabler():
    t = _transformer("aaabbbccc")
    t.edits = [
        SourceEdit(0, 6, "GONE", priority=200),
        SourceEdit(0, 0, "local c1 = x\n", group_id=1, is_enabler=True),
        SourceEdit(3, 6, "c1()", group_id=1),
        SourceEdit(0, 0, "local c2 = y\n", group_id=2, is_enabler=True),
        SourceEdit(6, 9, "c2()", group_id=2),
    ]
    out = t._apply_edits()
    assert "local c2 = y" in out
    assert "local c1 = x" not in out


# ---------------------------------------------------------------------------
# encoding preservation
# ---------------------------------------------------------------------------

CP1251_SOURCE = """\
-- Проверка кодировки
function f(x)
    -- возводим в квадрат
    return math.pow(x, 2)
end
"""


def test_cp1251_file_is_detected_as_cp1251(tmp_path):
    path = tmp_path / "russian.script"
    path.write_bytes(CP1251_SOURCE.encode("cp1251"))
    assert detect_file_encoding(path) == "cp1251"


def test_fixing_a_cp1251_file_keeps_the_encoding_and_the_comments(tmp_path):
    path = tmp_path / "russian.script"
    path.write_bytes(CP1251_SOURCE.encode("cp1251"))

    modified, content, count = ASTTransformer().transform_file(path, backup=False)
    assert modified is True

    raw = path.read_bytes()
    # still CP1251, not silently re-encoded to UTF-8
    assert detect_file_encoding(path) == "cp1251"
    with pytest.raises(UnicodeDecodeError):
        raw.decode("utf-8")

    text = raw.decode("cp1251")
    assert "Проверка кодировки" in text
    assert "возводим в квадрат" in text
    # and the actual fix landed
    assert "math.pow" not in text
    assert "x*x" in text
    ok, err = luajit_compiles(text)
    assert ok, err


@pytest.mark.xfail(
    strict=True,
    reason="ast_transformer.py:155 writes with Path.write_text(), which applies "
           "Python's default newline translation. On Windows every LF file comes "
           "back as CRLF, so a one-token fix rewrites every line in the file.",
)
def test_lf_line_endings_are_preserved(tmp_path):
    path = tmp_path / "lf.script"
    path.write_bytes(b"function f(x)\n    return math.pow(x, 2)\nend\n")

    modified, content, count = ASTTransformer().transform_file(path, backup=False)
    assert modified is True

    raw = path.read_bytes()
    assert b"x*x" in raw
    assert b"\r\n" not in raw


def test_crlf_line_endings_survive(tmp_path):
    path = tmp_path / "crlf.script"
    path.write_bytes(b"function f(x)\r\n    return math.pow(x, 2)\r\nend\r\n")

    ASTTransformer().transform_file(path, backup=False)

    raw = path.read_bytes()
    assert b"x*x" in raw
    assert b"\r\n" in raw


def test_utf8_bom_file_keeps_its_bom(tmp_path):
    path = tmp_path / "bom.script"
    path.write_bytes(b"\xef\xbb\xbf" + b"function f(x)\n    return math.pow(x, 2)\nend\n")
    assert detect_file_encoding(path) == "utf-8-sig"

    modified, content, count = ASTTransformer().transform_file(path, backup=False)
    assert modified is True
    assert path.read_bytes().startswith(b"\xef\xbb\xbf")


# ---------------------------------------------------------------------------
# .alao-bak handling
# ---------------------------------------------------------------------------

FIXABLE = "function f(x)\n    return math.pow(x, 2)\nend\n"


def test_backup_is_created_next_to_the_file(tmp_path):
    path = tmp_path / "a.script"
    path.write_text(FIXABLE, encoding="utf-8")

    ASTTransformer().transform_file(path, backup=True)

    bak = tmp_path / "a.script.alao-bak"
    assert bak.exists()
    assert bak.read_text(encoding="utf-8") == FIXABLE


def test_an_existing_backup_is_never_overwritten(tmp_path):
    path = tmp_path / "a.script"
    path.write_text(FIXABLE, encoding="utf-8")

    bak = tmp_path / "a.script.alao-bak"
    bak.write_text("-- the pristine original, do not clobber\n", encoding="utf-8")

    ASTTransformer().transform_file(path, backup=True)

    assert bak.read_text(encoding="utf-8") == "-- the pristine original, do not clobber\n"


def test_no_backup_flag_writes_no_backup(tmp_path):
    path = tmp_path / "a.script"
    path.write_text(FIXABLE, encoding="utf-8")

    ASTTransformer().transform_file(path, backup=False)

    assert not (tmp_path / "a.script.alao-bak").exists()


def test_dry_run_touches_nothing(tmp_path):
    path = tmp_path / "a.script"
    path.write_text(FIXABLE, encoding="utf-8")

    modified, content, count = ASTTransformer().transform_file(path, backup=True, dry_run=True)

    assert modified is True
    assert "x*x" in content
    assert path.read_text(encoding="utf-8") == FIXABLE
    assert not (tmp_path / "a.script.alao-bak").exists()


# ---------------------------------------------------------------------------
# idempotence
# ---------------------------------------------------------------------------

IDEMPOTENCE_CASES = {
    "math_pow": "function f(x)\n    return math.pow(x, 2) + math.pow(x, 3)\nend\n",
    "table_insert": "function f(t, v)\n    table.insert(t, v)\n    return t\nend\n",
    "caching": (
        "function f(t)\n"
        "    return math.floor(t) + math.floor(t + 1)\n"
        "        + math.floor(t + 2) + math.floor(t + 3)\n"
        "end\n"
    ),
    "distance": "function f(p, q)\n    return p:distance_to(q) < 10\nend\n",
    "repeated_alife": (
        "function f(id)\n"
        "    local a = alife():object(id)\n"
        "    local b = alife():actor()\n"
        "    local c = alife():story_object(id)\n"
        "    local d = alife():object(id + 1)\n"
        "    return a, b, c, d\n"
        "end\n"
    ),
    "debug": 'function f(x)\n    log("a")\n    printf("b")\n    return x\nend\n',
    "dead_code": "function g()\n    if false then\n        local q = 9\n    end\n    return 0\nend\n",
}

ALL_FLAGS = dict(
    fix_debug=True,
    fix_yellow=True,
    experimental=True,
    fix_nil=True,
    remove_dead_code=True,
)


# ---------------------------------------------------------------------------
# nested edits: a cache rewrite inside a table.insert argument list
# ---------------------------------------------------------------------------

# `unpack` is called four times, so it gets cached as `unpack_`. One of those
# call sites sits inside the arguments of a table.insert that ALAO also wants to
# rewrite to `t[#t+1] = ...`. The two edits are nested, not peers.
NESTED_CACHE_IN_TABLE_INSERT = """\
function build(src)
    local t = {}
    local a = unpack(src)
    local b = unpack(src)
    local c = unpack(src)
    table.insert(t, unpack(src))
    return t, a, b, c
end
"""

# The same shape with math.random, which is how it appears in GAMMA's
# actor_effects.script and grok_bo_enhanced_recoil.script.
NESTED_CACHE_IN_TABLE_INSERT_RANDOM = """\
function build(src)
    local anims = {}
    for i, v in pairs(src) do
        local a = math.random(0, 1)
        local b = math.random(0, 2)
        local c = math.random(0, 3)
        table.insert(anims, {e = i, d = math.random(0, 1), c = a + b + c})
    end
    return anims
end
"""


# And with string.sub, which is the shape in vanilla luapanda.lua:3490.
NESTED_CACHE_IN_TABLE_INSERT_SUB = """\
function split(s, t)
    local a = string.sub(s, 1, 3)
    local b = string.sub(s, 2, 4)
    local c = string.sub(s, 3, 5)
    table.insert(t, string.sub(s, 4, 6))
    return t, a, b, c
end
"""


def test_the_cache_rewrite_itself_lands(tmp_path):
    """Half of the pair does apply - this is the part that is not broken."""
    path = tmp_path / "nested.script"
    path.write_text(NESTED_CACHE_IN_TABLE_INSERT, encoding="utf-8")

    ASTTransformer().transform_file(path, backup=False)
    text = path.read_text(encoding="utf-8")

    assert "local unpack_ = unpack" in text
    assert text.count("unpack_(src)") == 4


def test_nested_cache_edit_does_not_cancel_the_table_insert_rewrite(tmp_path):
    path = tmp_path / "nested.script"
    path.write_text(NESTED_CACHE_IN_TABLE_INSERT, encoding="utf-8")

    ASTTransformer().transform_file(path, backup=False)
    text = path.read_text(encoding="utf-8")

    # the cache local is hoisted and every call site uses it
    assert "local unpack_ = unpack" in text
    # and the append rewrite still happens, with the cached name inside it
    assert "t[#t+1] = unpack_(src)" in text
    assert "table.insert" not in text


@pytest.mark.parametrize(
    "src", [
        NESTED_CACHE_IN_TABLE_INSERT,
        NESTED_CACHE_IN_TABLE_INSERT_RANDOM,
        NESTED_CACHE_IN_TABLE_INSERT_SUB,
    ],
)
def test_fix_is_a_fixpoint_for_nested_edits(tmp_path, src):
    """Run --fix twice over the same file and demand identical bytes.

    Backups are on and the .alao-bak is deleted between passes, which is the only
    way a user ever gets a second pass - the CLI skips any file that already has
    one. Whatever the second pass still changes is an optimization ALAO reported
    and then silently threw away.
    """
    path = tmp_path / "nested.script"
    bak = tmp_path / "nested.script.alao-bak"
    path.write_text(src, encoding="utf-8")

    ASTTransformer().transform_file(path, backup=True)
    after_first = path.read_bytes()
    assert bak.exists()
    bak.unlink()

    second_modified, _, _ = ASTTransformer().transform_file(path, backup=True)
    after_second = path.read_bytes()

    assert after_second == after_first, (
        "second --fix pass changed the file again:\n"
        f"--- after first ---\n{after_first.decode('utf-8')}\n"
        f"--- after second ---\n{after_second.decode('utf-8')}"
    )
    assert second_modified is False


@pytest.mark.parametrize("name", sorted(IDEMPOTENCE_CASES))
def test_a_second_fix_pass_changes_nothing(tmp_path, name):
    """Fixing an already-fixed file must be a no-op.

    The CLI normally protects against this by skipping files that already have a
    .alao-bak, but the transform itself has to be stable too - otherwise a user
    who reverts and re-runs, or points ALAO at someone else's optimised scripts,
    gets a second round of edits on top of the first.
    """
    path = tmp_path / f"{name}.script"
    path.write_text(IDEMPOTENCE_CASES[name], encoding="utf-8")

    first_modified, _, _ = ASTTransformer().transform_file(path, backup=False, **ALL_FLAGS)
    assert first_modified is True, f"{name}: nothing was fixed on the first pass"
    after_first = path.read_text(encoding="utf-8")

    ok, err = luajit_compiles(after_first)
    assert ok, f"{name}: first pass produced source LuaJIT rejects: {err}\n{after_first}"

    second_modified, _, _ = ASTTransformer().transform_file(path, backup=False, **ALL_FLAGS)
    after_second = path.read_text(encoding="utf-8")

    assert after_second == after_first, (
        f"{name}: second pass changed the file again\n"
        f"--- after first ---\n{after_first}\n--- after second ---\n{after_second}"
    )
    assert second_modified is False


# ---------------------------------------------------------------------------
# --verify-compile (I-004): a rewrite that does not compile is never written
# ---------------------------------------------------------------------------

def _break_the_rewrite(transformer):
    """Make _apply_edits hand back Lua that LuaJIT will refuse."""
    original = transformer._apply_edits

    def broken():
        original()
        return "function f(x) return x end end end -- unbalanced\n"

    transformer._apply_edits = broken
    return transformer


def test_a_broken_rewrite_is_refused_and_the_original_survives(tmp_path):
    path = tmp_path / "a.script"
    path.write_text(FIXABLE, encoding="utf-8")

    t = _break_the_rewrite(ASTTransformer())
    modified, content, count = t.transform_file(path, backup=True, verify_compile=True)

    assert modified is False, "a rewrite that does not compile must not be reported as applied"
    assert t.compile_error, "the compile error should be recorded"
    assert path.read_text(encoding="utf-8") == FIXABLE, "the original must survive untouched"
    assert not (tmp_path / "a.script.alao-bak").exists()


def test_without_verification_the_broken_rewrite_is_written(tmp_path):
    """The guard is what saves the file - proves the previous test is not vacuous."""
    path = tmp_path / "a.script"
    path.write_text(FIXABLE, encoding="utf-8")

    t = _break_the_rewrite(ASTTransformer())
    modified, content, count = t.transform_file(path, backup=False, verify_compile=False)

    assert modified is True
    assert t.compile_error is None
    assert path.read_text(encoding="utf-8") != FIXABLE


def test_a_good_rewrite_passes_verification(tmp_path):
    path = tmp_path / "a.script"
    path.write_text(FIXABLE, encoding="utf-8")

    t = ASTTransformer()
    modified, content, count = t.transform_file(path, backup=False, verify_compile=True)

    assert modified is True
    assert t.compile_error is None
    assert "x*x" in path.read_text(encoding="utf-8")


def test_apply_edits_counts_what_it_dropped():
    """edits_dropped_overlap is the counter that would have caught I-008."""
    t = _transformer("aaabbbccc")
    t.edits = [
        SourceEdit(0, 6, "low", priority=0),
        SourceEdit(3, 9, "HIGH", priority=100),
    ]
    t._apply_edits()
    assert t.edits_applied == 1
    assert t.edits_dropped == 1


def test_apply_edits_counts_absorbed_edits_as_applied():
    t = _transformer("table.insert(t, unpack(x))")
    t.edits = [
        SourceEdit(0, 26, "t[#t+1] = unpack(x)", priority=0),
        SourceEdit(16, 22, "unpack_", priority=0),
    ]
    out = t._apply_edits()
    assert out == "t[#t+1] = unpack_(x)"
    assert t.edits_applied == 2
    assert t.edits_dropped == 0
