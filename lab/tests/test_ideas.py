"""Idea pool schema and beam search."""

from __future__ import annotations

import json

import pytest

from aalo import ideas


def got(pool: ideas.IdeaPool, ident: str) -> dict:
    """Fetch an idea that must exist, so type checkers see a dict not None."""
    idea = pool.get(ident)
    assert idea is not None, f"{ident} missing from the pool"
    return idea


def pool_with(n: int = 5, generation: int = 0) -> ideas.IdeaPool:
    p = ideas.IdeaPool([], None)
    for i in range(n):
        p.add(f"idea {i}", category="alife", generation=generation)
    return p


def test_add_allocates_sequential_ids():
    p = pool_with(3)
    assert [i["id"] for i in p.ideas] == ["I-001", "I-002", "I-003"]
    assert p.next_id() == "I-004"


def test_idea_has_every_contract_field():
    idea = pool_with(1).ideas[0]
    for key in (
        "id",
        "title",
        "category",
        "hypothesis",
        "change",
        "measure",
        "expected_gain",
        "risk",
        "status",
        "score",
        "parent",
        "generation",
        "notes",
    ):
        assert key in idea
    assert idea["status"] == "proposed"
    assert idea["score"] is None


def test_enumerations_are_validated():
    p = ideas.IdeaPool()
    with pytest.raises(ValueError):
        p.add("bad", category="nonsense")
    with pytest.raises(ValueError):
        p.add("bad", risk="extreme")
    with pytest.raises(ValueError):
        p.add("bad", status="whatever")


def test_score_and_prune():
    p = pool_with(2)
    p.score("I-001", 3.5, notes="measured")
    assert got(p, "I-001")["score"] == 3.5
    assert "measured" in got(p, "I-001")["notes"]
    p.prune("I-002", reason="regressed")
    assert got(p, "I-002")["status"] == "pruned"
    assert "regressed" in got(p, "I-002")["notes"]


def test_beam_keeps_top_k_and_prunes_rest():
    p = pool_with(5)
    for idea, score in zip(p.ideas, [1.0, 9.0, 5.0, 7.0, 2.0]):
        p.score(idea["id"], score)
    result = p.beam(k=2)
    assert result["kept"] == ["I-002", "I-004"]  # 9.0 then 7.0
    assert set(result["pruned"]) == {"I-001", "I-003", "I-005"}
    assert got(p, "I-002")["status"] == "kept"
    assert got(p, "I-001")["status"] == "pruned"


def test_beam_leaves_unscored_ideas_alone():
    p = pool_with(3)
    p.score("I-001", 4.0)
    result = p.beam(k=1)
    assert result["kept"] == ["I-001"]
    assert set(result["unscored"]) == {"I-002", "I-003"}
    assert got(p, "I-002")["status"] == "proposed"


def test_beam_min_score_floor():
    p = pool_with(2)
    p.score("I-001", 0.2)
    p.score("I-002", -1.0)
    result = p.beam(k=5, min_score=1.0)
    assert result["kept"] == []
    assert set(result["pruned"]) == {"I-001", "I-002"}


def test_beam_ignores_already_pruned():
    p = pool_with(3)
    for i, s in zip(p.ideas, [1.0, 2.0, 3.0]):
        p.score(i["id"], s)
    p.prune("I-003")
    result = p.beam(k=1)
    assert result["kept"] == ["I-002"]


def test_spawn_next_generation_from_survivors():
    p = pool_with(3)
    for i, s in zip(p.ideas, [1.0, 5.0, 3.0]):
        p.score(i["id"], s)
    p.beam(k=1)
    children = p.spawn(per_parent=2)
    assert len(children) == 2
    assert all(c["generation"] == 1 for c in children)
    assert all(c["parent"] == "I-002" for c in children)
    assert all(c["status"] == "proposed" for c in children)
    assert p.max_generation == 1


def test_spawn_with_mutation_hook():
    p = pool_with(1)
    p.score("I-001", 1.0)
    p.beam(k=1)
    children = p.spawn(per_parent=1, mutate=lambda parent, n: {"title": "tweaked", "risk": "high"})
    assert children[0]["title"] == "tweaked"
    assert children[0]["risk"] == "high"


def test_beam_targets_the_newest_generation():
    p = pool_with(2)
    for i, s in zip(p.ideas, [1.0, 2.0]):
        p.score(i["id"], s)
    p.beam(k=1)
    kids = p.spawn(per_parent=2)
    for k, s in zip(kids, [8.0, 4.0]):
        p.score(k["id"], s)
    result = p.beam(k=1)
    assert result["generation"] == 1
    assert result["kept"] == [kids[0]["id"]]
    assert got(p, "I-002")["status"] == "kept"  # generation 0 untouched


def test_save_and_load_roundtrip(tmp_path):
    p = pool_with(2)
    p.score("I-001", 2.0)
    path = p.save(tmp_path / "ideas.json")
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert list(raw.keys()) == ["ideas"]
    assert len(raw["ideas"]) == 2
    back = ideas.IdeaPool.load(path)
    assert got(back, "I-001")["score"] == 2.0


def test_load_missing_file_gives_empty_pool(tmp_path):
    p = ideas.IdeaPool.load(tmp_path / "nothing.json")
    assert len(p) == 0
    assert p.next_id() == "I-001"


def test_update_rejects_unknown_field():
    p = pool_with(1)
    with pytest.raises(KeyError):
        p.update("I-001", bogus=1)
