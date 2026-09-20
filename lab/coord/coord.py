#!/usr/bin/env python3
"""Team coordination for parallel ALAO agents: locks, a work queue and a status board.

Several agents work in separate git worktrees at the same time. Some things they
touch are NOT per-worktree and will be trampled without coordination:

  - the game (MO2, the engine exe, PresentMon's ETW session, user.ltx, profiles,
    the GAMMA/mods dir)                                             -> lock "game"
  - timed corpus runs (G6 timing is meaningless if two 8-worker runs
    overlap; also extracted/_work and lab/data/corpus run ids)        -> lock "corpus"
  - lab/data/ideas.json and lab/docs/beam-ideas.md (organizer only)  -> lock "ideas"
  - regenerating extracted/gamma or extracted/vanilla                 -> lock "extract"

State lives next to THIS file (lab/coord/{locks,queue,status}/ in the main
checkout), never in a worktree, so every agent sees the same thing no matter
where it runs from.  Call it with the absolute path:

    py -3.12 C:\\code\\GIT\\anomaly_alao\\lab\\coord\\coord.py <subcommand> ...

Locks are directories (os.mkdir is atomic on NTFS) holding owner.json with a
lease.  A lease that expires without a heartbeat is stale and may be broken.

    coord lock acquire game --owner agent-I001 --ttl 3600 [--wait 600]
    coord lock touch   game --owner agent-I001        # extend the lease
    coord lock release game --owner agent-I001
    coord lock status
    coord lock break   game                            # only if stale (or --force)
    coord run corpus --owner agent-I001 --ttl 900 -- py -3.12 tools/corpus_run.py ...
        (acquire, run the command with a heartbeat, release; exit code passthrough)

Queue (FIFO, one JSON file per item, moved between pending/held/running/done/failed):

    coord queue submit --idea I-001 --agent agent-I001 --file request.json
    coord queue list [--state pending|held|running|done|failed|all]
    coord queue claim  [--owner fps-runner]           # oldest pending -> running
    coord queue hold    <id> | --all                  # pending -> held, runner ignores it
    coord queue release <id> | --all                  # held -> pending, same place in line
    coord queue done   <id> --result result.json
    coord queue fail   <id> --error "text"
    coord queue show   <id>

Status board (one file per agent, no contention):

    coord post --agent agent-I001 --idea I-001 --phase corpus --msg "G4/G5 clean, 0/513"
    coord board
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

COORD_ROOT = Path(__file__).resolve().parent
LOCKS = COORD_ROOT / "locks"
QUEUE = COORD_ROOT / "queue"
STATUS = COORD_ROOT / "status"
# `held` is a parking bay, not a stage of the pipeline: an item sits there
# instead of `pending` and queue_claim never looks at it, so the runner idles
# while a corpus job gets a quiet box (gen-4 did this by hand with `move`).
QUEUE_STATES = ("pending", "held", "running", "done", "failed")
KNOWN_LOCKS = ("game", "corpus", "ideas", "extract")
# Taking the left lock also waits until every lock on the right is free. A timed
# corpus run (8 workers) on the same box as a frametime capture wrecks both,
# so corpus work yields to the game.
CONFLICTS = {"corpus": ("game",), "extract": ("game", "corpus")}


def _now() -> float:
    return time.time()


def _iso(ts: float | None = None) -> str:
    return datetime.fromtimestamp(ts if ts is not None else _now(), tz=timezone.utc).isoformat(timespec="seconds")


def _ensure_dirs() -> None:
    LOCKS.mkdir(parents=True, exist_ok=True)
    STATUS.mkdir(parents=True, exist_ok=True)
    for s in QUEUE_STATES:
        (QUEUE / s).mkdir(parents=True, exist_ok=True)


def _write_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- locks

class LockHeld(RuntimeError):
    pass


def _lock_dir(name: str) -> Path:
    if not name.replace("-", "").replace("_", "").isalnum():
        raise ValueError(f"bad lock name {name!r}")
    return LOCKS / f"{name}.lock"


def lock_info(name: str) -> dict | None:
    d = _lock_dir(name)
    f = d / "owner.json"
    if not d.is_dir():
        return None
    try:
        info = _read_json(f)
    except (FileNotFoundError, json.JSONDecodeError):
        # mkdir happened but owner.json not written yet (or torn) - treat as
        # held for a few seconds, then stale.
        age = _now() - d.stat().st_mtime
        return {"name": name, "owner": "?", "stale": age > 10, "expires": d.stat().st_mtime + 10}
    info["stale"] = _now() > info.get("expires", 0)
    return info


def lock_acquire(name: str, owner: str, ttl: float = 900, wait: float = 0, note: str = "") -> dict:
    """Take *name* for *owner*. Blocks up to *wait* seconds. Breaks stale leases."""
    _ensure_dirs()
    d = _lock_dir(name)
    deadline = _now() + wait
    token = uuid.uuid4().hex[:12]
    while True:
        busy = [c for c in CONFLICTS.get(name, ()) if (ci := lock_info(c)) and not ci.get("stale")]
        if busy:
            if _now() >= deadline:
                raise LockHeld(f"lock {name!r} must wait for {busy}: " + "; ".join(
                    f"{c} held by {lock_info(c).get('owner')} until {_iso(lock_info(c).get('expires'))}" for c in busy))
            time.sleep(min(15.0, max(1.0, deadline - _now())))
            continue
        try:
            d.mkdir()
        except FileExistsError:
            info = lock_info(name)
            if info and info.get("stale"):
                print(f"[coord] breaking stale lock {name} (owner={info.get('owner')}, "
                      f"expired {_iso(info.get('expires'))})", file=sys.stderr)
                shutil.rmtree(d, ignore_errors=True)
                continue
            if _now() >= deadline:
                raise LockHeld(f"lock {name!r} held by {info.get('owner') if info else '?'} "
                               f"until {_iso(info.get('expires')) if info else '?'}"
                               f"{' - ' + info.get('note', '') if info and info.get('note') else ''}")
            time.sleep(min(5.0, max(0.5, deadline - _now())))
            continue
        info = {
            "name": name, "owner": owner, "token": token, "pid": os.getpid(),
            "host": socket.gethostname(), "user": getpass.getuser(),
            "acquired": _iso(), "ttl": ttl, "expires": _now() + ttl, "note": note,
            "cwd": os.getcwd(),
        }
        _write_json(d / "owner.json", info)
        return info


def lock_touch(name: str, owner: str, ttl: float | None = None) -> dict:
    info = lock_info(name)
    if not info or info.get("owner") != owner:
        raise LockHeld(f"lock {name!r} is not held by {owner!r} (holder: {info.get('owner') if info else None})")
    ttl = ttl if ttl is not None else info.get("ttl", 900)
    info["ttl"] = ttl
    info["expires"] = _now() + ttl
    info["touched"] = _iso()
    info.pop("stale", None)
    _write_json(_lock_dir(name) / "owner.json", info)
    return info


def lock_release(name: str, owner: str, force: bool = False) -> bool:
    d = _lock_dir(name)
    info = lock_info(name)
    if info is None:
        return False
    if info.get("owner") != owner and not force:
        raise LockHeld(f"lock {name!r} is held by {info.get('owner')!r}, not {owner!r}; use --force to break it")
    shutil.rmtree(d, ignore_errors=True)
    return True


def lock_break(name: str, force: bool = False) -> bool:
    info = lock_info(name)
    if info is None:
        return False
    if not info.get("stale") and not force:
        raise LockHeld(f"lock {name!r} is live (owner {info.get('owner')!r}, expires {_iso(info.get('expires'))}); "
                       f"pass --force only if you are sure the holder is dead")
    shutil.rmtree(_lock_dir(name), ignore_errors=True)
    return True


class held:
    """Context manager: ``with held('corpus', owner, ttl=900, wait=600): ...``.

    Heartbeats every ttl/3 seconds from a daemon thread so a long job never
    goes stale while it is genuinely still running.
    """

    def __init__(self, name: str, owner: str, ttl: float = 900, wait: float = 0, note: str = ""):
        self.name, self.owner, self.ttl, self.wait, self.note = name, owner, ttl, wait, note
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _beat(self) -> None:
        while not self._stop.wait(max(5.0, self.ttl / 3)):
            try:
                lock_touch(self.name, self.owner, self.ttl)
            except LockHeld:
                return

    def __enter__(self):
        lock_acquire(self.name, self.owner, self.ttl, self.wait, self.note)
        self._thread = threading.Thread(target=self._beat, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_exc):
        self._stop.set()
        try:
            lock_release(self.name, self.owner)
        except LockHeld:
            pass
        return False


# ---------------------------------------------------------------- queue

def _find_item(item_id: str) -> tuple[Path, str]:
    for s in QUEUE_STATES:
        for p in (QUEUE / s).glob("*.json"):
            if p.stem == item_id or p.stem.startswith(item_id):
                return p, s
    raise FileNotFoundError(f"queue item {item_id!r} not found")


def queue_submit(idea: str, agent: str, request: dict, kind: str = "fps", priority: int = 5) -> dict:
    _ensure_dirs()
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    item_id = f"{ts}-{idea}-{uuid.uuid4().hex[:6]}"
    item = {
        "id": item_id, "kind": kind, "idea": idea, "agent": agent, "priority": priority,
        "submitted": _iso(), "state": "pending", "request": request,
    }
    _write_json(QUEUE / "pending" / f"{item_id}.json", item)
    return item


def queue_list(state: str = "all") -> list[dict]:
    _ensure_dirs()
    states = QUEUE_STATES if state == "all" else (state,)
    out = []
    for s in states:
        for p in sorted((QUEUE / s).glob("*.json")):
            try:
                it = _read_json(p)
            except json.JSONDecodeError:
                continue
            it["state"] = s
            out.append(it)
    return out


def queue_claim(owner: str) -> dict | None:
    """Move the highest-priority, oldest pending item to running. None if empty."""
    _ensure_dirs()
    pending = sorted(queue_list("pending"), key=lambda it: (it.get("priority", 5), it["submitted"]))
    for it in pending:
        src = QUEUE / "pending" / f"{it['id']}.json"
        dst = QUEUE / "running" / f"{it['id']}.json"
        try:
            os.rename(src, dst)  # atomic-ish claim; a second claimer loses the rename
        except (FileNotFoundError, PermissionError, OSError):
            continue
        it["state"] = "running"
        it["claimed_by"] = owner
        it["claimed"] = _iso()
        _write_json(dst, it)
        return it
    return None


def _queue_move(item_id: str, src_state: str, dst_state: str, owner: str,
                stamp: str) -> dict:
    """Move one item between two parking states. Anything already running or
    finished is left alone - moving those would race the runner."""
    src, state = _find_item(item_id)           # raises if there is no such item
    if state != src_state:
        raise ValueError(f"queue item {src.stem} is {state}, not {src_state}")
    dst = QUEUE / dst_state / src.name
    # rename first, like queue_claim does: if the runner got there in between,
    # the rename fails and we do not resurrect an item it is already running
    os.rename(src, dst)
    it = _read_json(dst)
    it["state"] = dst_state
    it[stamp] = _iso()
    it[f"{stamp}_by"] = owner
    _write_json(dst, it)
    return it


def queue_hold(item_id: str, owner: str) -> dict:
    """pending -> held. The runner stops seeing it."""
    _ensure_dirs()
    return _queue_move(item_id, "pending", "held", owner, "held_at")


def queue_release(item_id: str, owner: str) -> dict:
    """held -> pending. Priority and submit time are untouched, so it goes back
    into the same place in the ordering it had before."""
    _ensure_dirs()
    return _queue_move(item_id, "held", "pending", owner, "released_at")


def queue_hold_all(owner: str) -> list[dict]:
    return [queue_hold(it["id"], owner) for it in queue_list("pending")]


def queue_release_all(owner: str) -> list[dict]:
    return [queue_release(it["id"], owner) for it in queue_list("held")]


def queue_finish(item_id: str, ok: bool, result: dict | None = None, error: str = "") -> dict:
    path, _state = _find_item(item_id)
    it = _read_json(path)
    it["finished"] = _iso()
    if ok:
        it["result"] = result or {}
        new_state = "done"
    else:
        it["error"] = error
        new_state = "failed"
    it["state"] = new_state
    _write_json(QUEUE / new_state / path.name, it)
    path.unlink()
    return it


# ---------------------------------------------------------------- status board

def post_status(agent: str, idea: str = "", phase: str = "", msg: str = "", extra: dict | None = None) -> dict:
    _ensure_dirs()
    p = STATUS / f"{agent}.json"
    entry = {"agent": agent, "idea": idea, "phase": phase, "msg": msg, "updated": _iso(),
             "cwd": os.getcwd(), **(extra or {})}
    hist = []
    if p.is_file():
        try:
            hist = _read_json(p).get("history", [])
        except json.JSONDecodeError:
            hist = []
    hist.append({k: entry[k] for k in ("idea", "phase", "msg", "updated")})
    entry["history"] = hist[-50:]
    _write_json(p, entry)
    return entry


def board() -> str:
    _ensure_dirs()
    lines = ["== locks =="]
    for d in sorted(LOCKS.glob("*.lock")):
        info = lock_info(d.stem) or {}
        flag = " STALE" if info.get("stale") else ""
        lines.append(f"  {d.stem:10s} owner={info.get('owner')} expires={_iso(info.get('expires', 0))}{flag}"
                     f"{' - ' + info['note'] if info.get('note') else ''}")
    if len(lines) == 1:
        lines.append("  (none)")
    lines.append("== queue ==")
    items = queue_list("all")
    if not items:
        lines.append("  (empty)")
    for it in items:
        tail = ""
        if it["state"] == "done":
            r = it.get("result", {})
            tail = f" -> {r.get('summary', '')}"
        elif it["state"] == "failed":
            tail = f" !! {it.get('error', '')[:120]}"
        lines.append(f"  [{it['state']:7s}] {it['id']}  idea={it['idea']} agent={it['agent']} "
                     f"kind={it['kind']}{tail}")
    lines.append("== agents ==")
    files = sorted(STATUS.glob("*.json"))
    if not files:
        lines.append("  (no status posted)")
    for p in files:
        try:
            e = _read_json(p)
        except json.JSONDecodeError:
            continue
        lines.append(f"  {e['agent']:14s} {e.get('idea', ''):6s} {e.get('phase', ''):12s} "
                     f"{e.get('updated', '')}  {e.get('msg', '')}")
    return "\n".join(lines)


# ---------------------------------------------------------------- CLI

def _cmd_lock(a) -> int:
    if a.op == "acquire":
        info = lock_acquire(a.name, a.owner, a.ttl, a.wait, a.note or "")
        print(json.dumps(info, indent=2))
    elif a.op == "touch":
        print(json.dumps(lock_touch(a.name, a.owner, a.ttl if a.ttl != 900 else None), indent=2))
    elif a.op == "release":
        print("released" if lock_release(a.name, a.owner, a.force) else "not held")
    elif a.op == "break":
        print("broken" if lock_break(a.name, a.force) else "not held")
    elif a.op == "status":
        names = [a.name] if a.name else sorted(set(KNOWN_LOCKS) | {d.stem for d in LOCKS.glob("*.lock")})
        for n in names:
            info = lock_info(n)
            if info is None:
                print(f"{n:10s} free")
            else:
                print(f"{n:10s} owner={info.get('owner')} expires={_iso(info.get('expires', 0))}"
                      f"{' STALE' if info.get('stale') else ''}{' - ' + info['note'] if info.get('note') else ''}")
    return 0


def _cmd_run(a) -> int:
    if not a.wrapped:
        print("coord run: nothing to run after '--'", file=sys.stderr)
        return 2
    cmd = list(a.wrapped)
    # CreateProcess won't find 'py' the way a shell does; resolve it ourselves
    exe = shutil.which(cmd[0])
    if exe:
        cmd[0] = exe
    with held(a.lock, a.owner, a.ttl, a.wait, a.note or " ".join(cmd)[:120]):
        proc = subprocess.run(cmd)
        return proc.returncode


def _cmd_queue(a) -> int:
    if a.op == "submit":
        request = _read_json(Path(a.file)) if a.file else {}
        it = queue_submit(a.idea, a.agent, request, a.kind, a.priority)
        print(it["id"])
    elif a.op == "list":
        for it in queue_list(a.state):
            print(f"[{it['state']:7s}] {it['id']}  idea={it['idea']} agent={it['agent']} kind={it['kind']}")
    elif a.op == "claim":
        it = queue_claim(a.owner)
        print(json.dumps(it, indent=2) if it else "")
        return 0 if it else 1
    elif a.op == "done":
        result = _read_json(Path(a.result)) if a.result else {}
        print(json.dumps(queue_finish(a.id, True, result), indent=2))
    elif a.op == "fail":
        print(json.dumps(queue_finish(a.id, False, error=a.error or ""), indent=2))
    elif a.op in ("hold", "release"):
        one = queue_hold if a.op == "hold" else queue_release
        every = queue_hold_all if a.op == "hold" else queue_release_all
        if a.all:
            items = every(a.owner)
            if not items:
                print(f"nothing to {a.op}")
        else:
            items = [one(a.id, a.owner)]
        for it in items:
            print(f"[{it['state']:7s}] {it['id']}  idea={it['idea']}")
    elif a.op == "show":
        p, _ = _find_item(a.id)
        print(p.read_text(encoding="utf-8"))
    return 0


def _cmd_post(a) -> int:
    e = post_status(a.agent, a.idea or "", a.phase or "", a.msg or "")
    print(f"posted {e['agent']} {e['phase']} {e['updated']}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    lk = sub.add_parser("lock")
    lk.add_argument("op", choices=("acquire", "touch", "release", "break", "status"))
    lk.add_argument("name", nargs="?")
    lk.add_argument("--owner", default=os.environ.get("ALAO_AGENT", getpass.getuser()))
    lk.add_argument("--ttl", type=float, default=900, help="lease seconds (default 900)")
    lk.add_argument("--wait", type=float, default=0, help="seconds to block for a busy lock")
    lk.add_argument("--note", help="what you're doing with it")
    lk.add_argument("--force", action="store_true")
    lk.set_defaults(fn=_cmd_lock)

    rn = sub.add_parser("run", help="hold a lock while running a command")
    rn.add_argument("lock")
    rn.add_argument("--owner", default=os.environ.get("ALAO_AGENT", getpass.getuser()))
    rn.add_argument("--ttl", type=float, default=900)
    rn.add_argument("--wait", type=float, default=1800)
    rn.add_argument("--note")
    rn.set_defaults(wrapped=[])
    rn.set_defaults(fn=_cmd_run)

    q = sub.add_parser("queue")
    q.add_argument("op", choices=("submit", "list", "claim", "hold", "release",
                                  "done", "fail", "show"))
    q.add_argument("--all", action="store_true",
                   help="hold/release every item in the source state")
    q.add_argument("id", nargs="?")
    q.add_argument("--idea")
    q.add_argument("--agent", default=os.environ.get("ALAO_AGENT", getpass.getuser()))
    q.add_argument("--kind", default="fps")
    q.add_argument("--priority", type=int, default=5, help="1 = first, 9 = last")
    q.add_argument("--file", help="request JSON (submit)")
    q.add_argument("--state", default="all", choices=("all",) + QUEUE_STATES)
    q.add_argument("--owner", default=os.environ.get("ALAO_AGENT", getpass.getuser()))
    q.add_argument("--result", help="result JSON (done)")
    q.add_argument("--error")
    q.set_defaults(fn=_cmd_queue)

    ps = sub.add_parser("post")
    ps.add_argument("--agent", default=os.environ.get("ALAO_AGENT", getpass.getuser()))
    ps.add_argument("--idea")
    ps.add_argument("--phase")
    ps.add_argument("--msg")
    ps.set_defaults(fn=_cmd_post)

    bd = sub.add_parser("board")
    bd.set_defaults(fn=lambda _a: (print(board()), 0)[1])

    # `run` takes the wrapped command after a literal '--'. argparse.REMAINDER
    # would swallow our own --note/--ttl into it, so split it off by hand.
    argv = list(sys.argv[1:] if argv is None else argv)
    tail: list[str] = []
    if argv and argv[0] == "run" and "--" in argv:
        i = argv.index("--")
        argv, tail = argv[:i], argv[i + 1:]
    a = ap.parse_args(argv)
    if a.cmd == "run":
        a.wrapped = tail
    if a.cmd == "queue" and a.op == "submit" and not a.idea:
        ap.error("queue submit needs --idea")
    if a.cmd == "queue" and a.op in ("hold", "release") and not a.id and not a.all:
        ap.error(f"queue {a.op} needs an id or --all")
    if a.cmd == "lock" and a.op != "status" and not a.name:
        ap.error("lock needs a name")
    try:
        return a.fn(a)
    except LockHeld as e:
        print(f"[coord] {e}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
