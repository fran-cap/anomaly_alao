"""coord queue hold / release (I-059).

Parking bay for pending FPS requests: an fps run and a corpus job exclude each
other, and gen-4 gave the corpus its quiet box by moving the queue JSONs by
hand. `hold` makes that official.

Everything here runs against a temp coord root. The live queue at
C:\\code\\GIT\\anomaly_alao\\lab\\coord\\queue has real items in it and a runner
watching it; these tests must never touch it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

LAB = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LAB / "coord"))

import coord  # noqa: E402


@pytest.fixture
def coord_root(tmp_path, monkeypatch):
    """Point every coord path at tmp_path, then prove we did."""
    root = tmp_path / "coord"
    monkeypatch.setattr(coord, "COORD_ROOT", root)
    monkeypatch.setattr(coord, "LOCKS", root / "locks")
    monkeypatch.setattr(coord, "QUEUE", root / "queue")
    monkeypatch.setattr(coord, "STATUS", root / "status")
    coord._ensure_dirs()
    assert str(coord.QUEUE).startswith(str(tmp_path))
    return root


def _submit(idea="I-000", priority=5):
    return coord.queue_submit(idea, "agent-test", {"label": idea}, "fps", priority)


def _ids(state):
    return [it["id"] for it in coord.queue_list(state)]


def test_held_dir_exists_and_starts_empty(coord_root):
    assert (coord_root / "queue" / "held").is_dir()
    assert coord.queue_list("held") == []


def test_hold_moves_a_pending_item_out_of_pending(coord_root):
    it = _submit()
    held = coord.queue_hold(it["id"], "agent-test")
    assert held["state"] == "held"
    assert held["held_at"] and held["held_at_by"] == "agent-test"
    assert _ids("pending") == []
    assert _ids("held") == [it["id"]]
    assert (coord_root / "queue" / "held" / f"{it['id']}.json").is_file()
    assert not (coord_root / "queue" / "pending" / f"{it['id']}.json").exists()


def test_release_puts_it_back(coord_root):
    it = _submit()
    coord.queue_hold(it["id"], "agent-test")
    back = coord.queue_release(it["id"], "org")
    assert back["state"] == "pending"
    assert back["released_at_by"] == "org"
    assert _ids("pending") == [it["id"]]
    assert _ids("held") == []


def test_release_keeps_priority_and_submit_time(coord_root):
    first = _submit("I-AAA", priority=1)
    second = _submit("I-BBB", priority=5)
    coord.queue_hold(first["id"], "agent-test")
    coord.queue_release(first["id"], "agent-test")
    # priority 1 still wins over priority 5
    assert coord.queue_claim("fps-runner")["id"] == first["id"]
    assert coord.queue_claim("fps-runner")["id"] == second["id"]


def test_the_runner_never_claims_a_held_item(coord_root):
    it = _submit()
    coord.queue_hold(it["id"], "agent-test")
    assert coord.queue_claim("fps-runner") is None
    assert _ids("held") == [it["id"]]          # and it is still there
    coord.queue_release(it["id"], "agent-test")
    assert coord.queue_claim("fps-runner")["id"] == it["id"]


def test_hold_all_and_release_all(coord_root):
    ids = [_submit(f"I-{n:03d}")["id"] for n in range(3)]
    assert len(coord.queue_hold_all("org")) == 3
    assert sorted(_ids("held")) == sorted(ids)
    assert coord.queue_claim("fps-runner") is None
    assert len(coord.queue_release_all("org")) == 3
    assert sorted(_ids("pending")) == sorted(ids)


def test_hold_all_leaves_running_and_finished_items_alone(coord_root):
    pending = _submit("I-PEND")
    running = _submit("I-RUN")
    coord.queue_claim("fps-runner")   # priority tie -> oldest first == pending
    coord.queue_hold_all("org")
    assert _ids("held") == [running["id"]]
    assert len(_ids("running")) == 1


def test_holding_a_running_item_is_refused(coord_root):
    it = _submit()
    coord.queue_claim("fps-runner")
    with pytest.raises(ValueError) as e:
        coord.queue_hold(it["id"], "org")
    assert "running" in str(e.value)
    assert _ids("running") == [it["id"]]


def test_releasing_something_that_is_not_held_is_refused(coord_root):
    it = _submit()
    with pytest.raises(ValueError) as e:
        coord.queue_release(it["id"], "org")
    assert "pending" in str(e.value)
    assert _ids("pending") == [it["id"]]


def test_unknown_id_raises(coord_root):
    with pytest.raises(FileNotFoundError):
        coord.queue_hold("nope", "org")


def test_queue_list_all_shows_held_items(coord_root, capsys):
    it = _submit("I-SHOW")
    coord.queue_hold(it["id"], "org")
    states = {i["state"] for i in coord.queue_list("all")}
    assert states == {"held"}
    assert "held" in coord.QUEUE_STATES
    assert "[held" in coord.board()


# ---------------------------------------------------------------- CLI

def _cli(*argv):
    return coord.main(list(argv))


def test_cli_hold_release_roundtrip(coord_root, capsys):
    it = _submit("I-CLI")
    assert _cli("queue", "hold", it["id"], "--owner", "org") == 0
    assert _ids("held") == [it["id"]]
    assert _cli("queue", "release", "--all", "--owner", "org") == 0
    assert _ids("pending") == [it["id"]]
    out = capsys.readouterr().out
    assert it["id"] in out


def test_cli_hold_all_holds_every_pending_item(coord_root):
    _submit("I-1")
    _submit("I-2")
    assert _cli("queue", "hold", "--all", "--owner", "org") == 0
    assert len(_ids("held")) == 2


def test_hold_accepts_an_id_prefix(coord_root):
    it = _submit("I-PRE")
    coord.queue_hold(it["id"][:8], "org")
    assert _ids("held") == [it["id"]]


def test_cli_hold_without_id_or_all_is_an_error(coord_root):
    with pytest.raises(SystemExit):
        _cli("queue", "hold")


def test_cli_list_accepts_the_held_state(coord_root, capsys):
    it = _submit("I-LS")
    coord.queue_hold(it["id"], "org")
    assert _cli("queue", "list", "--state", "held") == 0
    assert it["id"] in capsys.readouterr().out


# ---------------------------------------------------------------- the runner

def test_fps_runner_only_ever_claims_through_queue_claim():
    """The user's fps_runner.py is running right now and must not need a
    restart: it reaches the queue exclusively through coord.queue_claim, which
    reads `pending` and nothing else."""
    src = (LAB / "coord" / "fps_runner.py").read_text(encoding="utf-8")
    assert "queue_claim" in src
    for forbidden in ('QUEUE /', '"pending"', "'pending'", '"held"', "'held'"):
        assert forbidden not in src
