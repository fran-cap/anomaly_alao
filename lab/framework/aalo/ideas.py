"""The idea pool behind ``data/ideas.json`` and its beam search.

Optimisation ideas are generated, measured, scored and then pruned: each
generation keeps the top *k* by score and spawns children from the survivors.
The JSON shape is fixed by CONTRACT.md and shared with the dashboard.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import config as _config

__all__ = [
    "CATEGORIES",
    "STATUSES",
    "LEVELS",
    "IdeaPool",
    "load",
    "save",
    "new_idea",
]

CATEGORIES = ("alife", "engine", "render", "scripts", "mods", "config", "other")
STATUSES = ("proposed", "queued", "running", "kept", "pruned")
LEVELS = ("low", "med", "high")

_FIELDS = (
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
)


def new_idea(
    idea_id: str,
    title: str,
    category: str = "other",
    hypothesis: str = "",
    change: str = "",
    measure: str = "",
    expected_gain: str = "med",
    risk: str = "low",
    status: str = "proposed",
    score=None,
    parent=None,
    generation: int = 0,
    notes: str = "",
) -> dict:
    """Build one idea dict, validating the enumerated fields."""
    if category not in CATEGORIES:
        raise ValueError(f"category must be one of {CATEGORIES}, got {category!r}")
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}, got {status!r}")
    for name, value in (("expected_gain", expected_gain), ("risk", risk)):
        if value not in LEVELS:
            raise ValueError(f"{name} must be one of {LEVELS}, got {value!r}")
    return {
        "id": idea_id,
        "title": title,
        "category": category,
        "hypothesis": hypothesis,
        "change": change,
        "measure": measure,
        "expected_gain": expected_gain,
        "risk": risk,
        "status": status,
        "score": score,
        "parent": parent,
        "generation": int(generation),
        "notes": notes,
    }


class IdeaPool:
    """Mutable view over ``data/ideas.json``."""

    def __init__(self, ideas=None, path: Path | None = None):
        self.ideas: list[dict] = ideas or []
        self.path = path

    # -- io ----------------------------------------------------------------
    @classmethod
    def load(cls, path=None, cfg=None) -> "IdeaPool":
        cfg = cfg or _config.get()
        p = Path(path) if path else cfg.ideas_file
        if not p.is_file():
            return cls([], p)
        data = json.loads(p.read_text(encoding="utf-8"))
        return cls(list(data.get("ideas", [])), p)

    def save(self, path=None) -> Path:
        destination = path or self.path
        if destination is None:
            raise ValueError("no path to save to; pass one or load from a file")
        p = Path(destination)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"ideas": self.ideas}, indent=2), encoding="utf-8")
        return p

    # -- queries -----------------------------------------------------------
    def get(self, idea_id: str) -> dict | None:
        for i in self.ideas:
            if i["id"] == idea_id:
                return i
        return None

    def by_status(self, status: str) -> list[dict]:
        return [i for i in self.ideas if i.get("status") == status]

    def generation(self, gen: int) -> list[dict]:
        return [i for i in self.ideas if int(i.get("generation", 0)) == gen]

    @property
    def max_generation(self) -> int:
        return max((int(i.get("generation", 0)) for i in self.ideas), default=0)

    def next_id(self) -> str:
        nums = []
        for i in self.ideas:
            ident = str(i.get("id", ""))
            if ident.startswith("I-") and ident[2:].isdigit():
                nums.append(int(ident[2:]))
        return f"I-{max(nums, default=0) + 1:03d}"

    # -- mutation ----------------------------------------------------------
    def add(self, title: str, **kwargs) -> dict:
        """Add a new idea; the id is allocated unless one is passed."""
        idea_id = kwargs.pop("idea_id", None) or self.next_id()
        if self.get(idea_id):
            raise ValueError(f"idea {idea_id} already exists")
        idea = new_idea(idea_id, title, **kwargs)
        self.ideas.append(idea)
        return idea

    def update(self, idea_id: str, **fields) -> dict:
        idea = self.get(idea_id)
        if idea is None:
            raise KeyError(idea_id)
        for k, v in fields.items():
            if k not in _FIELDS:
                raise KeyError(f"unknown idea field {k!r}")
            idea[k] = v
        return idea

    def score(self, idea_id: str, value: float, notes: str | None = None) -> dict:
        """Record a measured score.  Scoring alone does not decide survival."""
        idea = self.update(idea_id, score=float(value))
        if notes:
            idea["notes"] = (idea.get("notes", "") + " " + notes).strip()
        return idea

    def set_status(self, idea_id: str, status: str) -> dict:
        if status not in STATUSES:
            raise ValueError(f"status must be one of {STATUSES}")
        return self.update(idea_id, status=status)

    def prune(self, idea_id: str, reason: str = "") -> dict:
        idea = self.set_status(idea_id, "pruned")
        if reason:
            idea["notes"] = (idea.get("notes", "") + f" pruned: {reason}").strip()
        return idea

    # -- beam search -------------------------------------------------------
    def beam(self, k: int = 3, generation: int | None = None, min_score=None) -> dict:
        """Keep the top *k* scored ideas of a generation, prune the rest.

        Unscored ideas are left alone: they have not been measured yet, so they
        are neither survivors nor casualties.  Returns
        ``{"kept": [...], "pruned": [...], "unscored": [...]}`` of ids.
        """
        gen = self.max_generation if generation is None else generation
        pool = [i for i in self.generation(gen) if i.get("status") != "pruned"]
        scored = [i for i in pool if i.get("score") is not None]
        unscored = [i for i in pool if i.get("score") is None]
        scored.sort(key=lambda i: float(i["score"]), reverse=True)

        kept, pruned = [], []
        for rank, idea in enumerate(scored):
            survives = rank < k and (min_score is None or float(idea["score"]) >= float(min_score))
            idea["status"] = "kept" if survives else "pruned"
            (kept if survives else pruned).append(idea["id"])
        return {
            "generation": gen,
            "kept": kept,
            "pruned": pruned,
            "unscored": [i["id"] for i in unscored],
        }

    def spawn(self, parents=None, per_parent: int = 2, generation: int | None = None, mutate=None) -> list[dict]:
        """Create the next generation from surviving parents.

        *mutate* is ``f(parent, n) -> dict`` of field overrides; the default
        derives a placeholder variant that a human or the beam agent fills in.
        """
        gen = self.max_generation if generation is None else generation
        if parents is None:
            parents = [i for i in self.generation(gen) if i.get("status") == "kept"]
        elif parents and isinstance(parents[0], str):
            parents = [self.get(p) for p in parents]
        children: list[dict] = []
        for parent in parents:
            if parent is None:
                continue
            for n in range(per_parent):
                overrides = mutate(parent, n) if mutate else {}
                child = self.add(
                    overrides.pop("title", f"{parent['title']} (variant {n + 1})"),
                    category=overrides.pop("category", parent.get("category", "other")),
                    hypothesis=overrides.pop("hypothesis", parent.get("hypothesis", "")),
                    change=overrides.pop("change", parent.get("change", "")),
                    measure=overrides.pop("measure", parent.get("measure", "")),
                    expected_gain=overrides.pop("expected_gain", parent.get("expected_gain", "med")),
                    risk=overrides.pop("risk", parent.get("risk", "low")),
                    status="proposed",
                    parent=parent["id"],
                    generation=int(parent.get("generation", 0)) + 1,
                    notes=overrides.pop("notes", ""),
                )
                child.update(overrides)
                children.append(child)
        return children

    def __len__(self) -> int:
        return len(self.ideas)

    def __iter__(self):
        return iter(self.ideas)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<IdeaPool {len(self.ideas)} ideas gen<={self.max_generation}>"


def load(path=None, cfg=None) -> IdeaPool:
    return IdeaPool.load(path, cfg)


def save(pool: IdeaPool, path=None) -> Path:
    return pool.save(path)
