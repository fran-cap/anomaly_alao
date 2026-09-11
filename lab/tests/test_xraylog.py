"""Engine log parsing."""

from __future__ import annotations

from aalo import xraylog

CLEAN_LOG = """\
Starting engine...
* Detected CPU: AMD Ryzen 9 7950X3D, F16C, AVX, mmx, sse, sse2
* [x-ray]: Loading level [l02_garbage]
* phase time: 1200 ms
* phase time: 800 ms
! Can't find texture 'wpn\\wpn_ak74_hud'
~ sound: no such resource
* [ALife] simulation: objects : 12045, online : 187
* Level loading time: 12.5 sec
"""

CRASH_LOG = """\
Starting engine...
* [x-ray]: Loading level [l01_escape]
FATAL ERROR

[error]Expression    : fatal error
[error]Function      : CScriptEngine::lua_error
[error]File          : ..\\xrServerEntities\\script_engine.cpp
[error]Description   : attempt to index a nil value

stack trace:
 xrCore.dll
 xrGame.dll
"""


def test_clean_log_basics():
    log = xraylog.parse(CLEAN_LOG)
    assert log.crashed is False
    assert log.levels == ["l02_garbage"]
    assert log.phase_times_ms == [1200, 800]
    assert log.warning_count == 2


def test_load_time_prefers_explicit_value():
    log = xraylog.parse(CLEAN_LOG)
    assert log.load_time_s == 12.5


def test_load_time_falls_back_to_phase_times():
    text = "\n".join(l for l in CLEAN_LOG.splitlines() if "loading time" not in l.lower())
    log = xraylog.parse(text)
    assert log.load_time_s == 2.0  # (1200 + 800) ms


def test_alife_stats_extracted():
    log = xraylog.parse(CLEAN_LOG)
    assert log.alife.get("online") == 187.0
    assert log.alife.get("objects") == 12045.0


def test_sigils_and_tags():
    log = xraylog.parse(CLEAN_LOG)
    tagged = [e for e in log.entries if e.tag == "x-ray"]
    assert tagged and tagged[0].text.startswith("Loading level")
    assert all(e.is_warning for e in log.warnings)


def test_crash_detection_and_stack_trace():
    log = xraylog.parse(CRASH_LOG)
    assert log.crashed is True
    assert log.error_count >= 4
    assert any("attempt to index a nil value" in e for e in log.errors)
    assert "xrGame.dll" in log.stack_trace


def test_summary_shape():
    s = xraylog.parse(CLEAN_LOG).summary()
    for key in ("lines", "load_time_s", "crashed", "errors", "levels", "alife"):
        assert key in s
    assert s["phase_time_total_ms"] == 2000


def test_timestamped_lines():
    text = "[  12.345] * [x-ray]: Loading level [l03_agroprom]\n[  13.000] ! warning here\n"
    log = xraylog.parse(text)
    assert log.entries[0].t_s == 12.345
    assert log.levels == ["l03_agroprom"]
    assert log.warning_count == 1


def test_empty_and_garbage_input():
    assert xraylog.parse("").entries == []
    log = xraylog.parse("\x00\x01 not a log line\n\n")
    assert log.crashed is False


def test_load_handles_cp1251(tmp_path):
    p = tmp_path / "xray_test.log"
    p.write_bytes("! ошибка текстуры\n".encode("cp1251"))
    log = xraylog.load(p)
    assert log.warning_count == 1


def test_newest_log_picks_latest_mtime(tmp_path):
    import os
    import time

    old = tmp_path / "xray_old.log"
    new = tmp_path / "xray_new.log"
    old.write_text("* old\n", encoding="utf-8")
    new.write_text("* new\n", encoding="utf-8")
    os.utime(old, (time.time() - 500, time.time() - 500))
    assert xraylog.newest_log(tmp_path) == new


def test_newest_log_missing_dir(tmp_path):
    assert xraylog.newest_log(tmp_path / "nope") is None


# GAMMA never prints "Loading level [..]"; these are the engine lines it does
# print, copied from a real GAMMA 0.9.4 session (2026-09-10).
GAMMA_LOAD_LOG = """\
[d:/gog_games/gamma/s.t.a.l.k.e.r. gamma/anomaly/bin/../appdata/user.ltx] successfully loaded.
* 18677 spawn points are successfully loaded
* New game is successfully created!
Level name: l12_stancia_2
* 18677 spawn points are successfully loaded
* 22410 objects are successfully loaded
* Game player - autosave is successfully loaded from file 'd:/gog_games/gamma/s.t.a.l.k.e.r. gamma/anomaly/bin/../appdata/savedgames/player - autosave.scop' (0.716s)
"""


def test_gamma_world_markers():
    log = xraylog.parse(GAMMA_LOAD_LOG)
    # config and spawn-registry "successfully loaded" lines are not world loads
    assert log.world_loads == ["<new game>", "player - autosave"]
    assert log.levels == []
    assert log.summary()["world_loads"] == ["<new game>", "player - autosave"]


def test_menu_only_log_has_no_world_marker():
    log = xraylog.parse(GAMMA_LOAD_LOG.splitlines()[0] + "\n")
    assert log.world_loads == []
