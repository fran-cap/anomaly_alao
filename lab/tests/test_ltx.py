"""ltx parser: round-trip fidelity, editing, and the two dialects."""

from __future__ import annotations

from aalo import ltx

SECTIONED = """\
; header comment
#include "common.ltx"

[alife]:base_alife
switch_distance    = 150    ; how far NPCs stay online
switch_factor      = 0.1

[smart_terrain]
; a comment inside a section
max_population = 8
"""

USER = """\
_preset Default
r2_sun_quality st_opt_medium
r2_sun_tsm on
bind jump kSPACE
bind crouch kLCONTROL
default_controls
"""


def test_sectioned_roundtrip_is_byte_exact():
    doc = ltx.parse(SECTIONED)
    assert doc.dumps() == SECTIONED


def test_sectioned_structure():
    doc = ltx.parse(SECTIONED)
    assert doc.sections == ["alife", "smart_terrain"]
    assert doc.section_parent("alife") == "base_alife"
    assert doc.includes == ["common.ltx"]
    assert doc.get("alife", "switch_distance") == "150"
    assert doc.get("smart_terrain", "max_population") == "8"
    assert doc.get("alife", "missing") is None


def test_edit_preserves_everything_else():
    doc = ltx.parse(SECTIONED)
    doc.set("alife", "switch_distance", "120")
    out = doc.dumps()
    assert "switch_distance = 120" in out
    assert "; how far NPCs stay online" in out  # inline comment kept
    assert "; header comment" in out
    assert "#include" in out
    assert out.count("switch_factor") == 1


def test_set_creates_key_and_section():
    doc = ltx.parse(SECTIONED)
    doc.set("alife", "new_key", "7")
    doc.set("brand_new", "k", "v")
    assert doc.get("alife", "new_key") == "7"
    assert doc.get("brand_new", "k") == "v"
    assert "[brand_new]" in doc.dumps()
    # the new key lands inside its own section, not after the next header
    body = doc.dumps()
    assert body.index("new_key") < body.index("[smart_terrain]")


def test_delete_entry():
    doc = ltx.parse(SECTIONED)
    assert doc.delete("alife", "switch_factor") is True
    assert doc.delete("alife", "switch_factor") is False
    assert "switch_factor" not in doc.dumps()


def test_user_ltx_roundtrip_and_lookup():
    doc = ltx.parse_user(USER)
    assert doc.dumps() == USER
    assert doc.get("r2_sun_quality") == "st_opt_medium"
    assert doc.get("default_controls") == ""
    assert doc.get_all("bind") == ["jump kSPACE", "crouch kLCONTROL"]


def test_user_ltx_set_and_append():
    doc = ltx.parse_user(USER)
    doc.set("r2_sun_quality", "st_opt_low")
    doc.set("r__geometry_lod", "0.75")
    out = doc.dumps()
    assert "r2_sun_quality st_opt_low" in out
    assert out.endswith("r__geometry_lod 0.75")
    assert "bind jump kSPACE" in out


def test_user_ltx_to_dict_folds_binds():
    d = ltx.parse_user(USER).to_dict()
    assert d["bind jump"] == "kSPACE"
    assert d["bind crouch"] == "kLCONTROL"
    assert d["r2_sun_tsm"] == "on"


def test_dialect_detection():
    assert ltx.looks_like_user_ltx(USER) is True
    assert ltx.looks_like_user_ltx(SECTIONED) is False
    assert isinstance(ltx.parse_auto(USER, "user.ltx"), ltx.UserLtx)
    assert isinstance(ltx.parse_auto(SECTIONED, "alife.ltx"), ltx.LtxFile)


def test_diff_user_ltx_shape():
    before = ltx.parse_user(USER)
    after = ltx.parse_user(USER)
    after.set("r2_sun_quality", "st_opt_low")
    after.set("r__detail_radius", "50")
    diff = ltx.diff_user_ltx(before, after)
    assert diff["r2_sun_quality"] == ["st_opt_medium", "st_opt_low"]
    assert diff["r__detail_radius"] == [None, "50"]
    assert "r2_sun_tsm" not in diff


def test_diff_ltx_sectioned():
    a = ltx.parse(SECTIONED)
    b = ltx.parse(SECTIONED)
    b.set("alife", "switch_distance", "90")
    assert ltx.diff_ltx(a, b) == {"alife/switch_distance": ["150", "90"]}


def test_crlf_preserved():
    text = SECTIONED.replace("\n", "\r\n")
    doc = ltx.parse(text)
    assert doc.newline == "\r\n"
    assert doc.dumps() == text


def test_save_and_reload(tmp_path):
    p = tmp_path / "x.ltx"
    doc = ltx.parse(SECTIONED)
    doc.set("alife", "switch_distance", "42")
    doc.save(p)
    assert ltx.load(p).get("alife", "switch_distance") == "42"


# -- real files (read-only) -------------------------------------------------


def test_real_fsgame_roundtrip(real_fsgame):
    raw = ltx._read(real_fsgame)
    doc = ltx.load(real_fsgame)
    assert doc.dumps() == raw
    # fsgame.ltx keeps all its entries before any section header.
    entries = doc.items(None)
    assert any(k == "$game_data$" for k, _ in entries)
    assert any(k == "$logs$" for k, _ in entries)


def test_real_user_ltx_roundtrip(real_user_ltx):
    raw = ltx._read(real_user_ltx)
    doc = ltx.load_user(real_user_ltx)
    assert doc.dumps() == raw
    d = doc.to_dict()
    assert len(d) > 50
    assert any(k.startswith("r2_") for k in d)


def test_real_user_ltx_edit_does_not_touch_disk(real_user_ltx):
    original = real_user_ltx.read_bytes()
    doc = ltx.load_user(real_user_ltx)
    doc.set("r2_sun_quality", "st_opt_low")
    assert "r2_sun_quality st_opt_low" in doc.dumps()
    assert real_user_ltx.read_bytes() == original
