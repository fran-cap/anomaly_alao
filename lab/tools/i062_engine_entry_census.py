"""I-062: what does the engine call directly, in the copies the live profile loads?

The I-048 profiler sees exactly one door into Lua: `axr_main.make_callback`.
Everything the engine enters Lua through some OTHER door is invisible to it, and
the walkout frames (25-44 ms, CPU bound, no listener above 12 ms) say something
is coming through one of those doors.  This is the static half of the answer:
enumerate the doors, rank them by how likely they are to fire when objects
switch online, and hand the top of the list to the walkout profiler build as its
wrap list.

The doors, in the order this tool reports them:

  1. **object binders** - `class "x" (object_binder)`.  The engine calls
     `net_spawn` / `net_destroy` / `reinit` / `update` / `net_save` / `load`
     on these directly, once per bound object.  `bind(obj)` at file scope is
     what the engine calls when an object comes online, so a `bind_*.script`
     with a `bind()` is a per-object entry point.
  2. **server objects** - `class "x" (cse_alife_*)`.  Smart terrains, squads,
     level changers: `update`, `on_register`, `STATE_Write`, `can_switch_online`.
     These are the things that actually run when the player leaves a bubble.
  3. **schemes** - `xr_logic` drives `update()` on scheme action classes from the
     binders.  Not an engine door, but reached without a make_callback.
  4. **time events / deferred calls** - `CreateTimeEvent`, `AddUniqueCall`,
     `level.add_call`, `ProcessEventQueue`: bodies that run per frame from a
     queue with no callback name attached to them.
  5. **config functors** - `.ltx` keys whose value names a `module.function`
     the engine or a UI resolves by name (`functor`, `script`, `*_functor`).
  6. **known engine globals** - a curated list of names the engine calls by
     name (`visual_memory_manager.get_visible_value` and friends), checked
     against the live tree so we only ever wrap something that exists.

Resolution is the live-winner rule, same as the gen-3/4/5 census tools:
enabled MO2 mods in modlist order, then loose `Anomaly/gamedata`, then the db.

    py -3.12 lab/tools/i062_engine_entry_census.py
    py -3.12 lab/tools/i062_engine_entry_census.py --json out.json --top 30
    py -3.12 lab/tools/i062_engine_entry_census.py --wrap-list      # for the overlay
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from i043_callback_census import live_scripts, read  # noqa: E402

LOOSE_CFG = Path(r"D:\GOG_Games\Gamma\S.T.A.L.K.E.R. GAMMA\Anomaly\gamedata\configs")
DB_CFG = Path(r"C:\code\GIT\anomaly_alao\extracted\vanilla_db\VANILLA_DB\gamedata\configs")
MODS_DIR = Path(r"D:\GOG_Games\Gamma\S.T.A.L.K.E.R. GAMMA\GAMMA\mods")
MODLIST = Path(r"D:\GOG_Games\Gamma\S.T.A.L.K.E.R. GAMMA\GAMMA\profiles\G.A.M.M.A\modlist.txt")

CLASS = re.compile(r'^\s*class\s*"([A-Za-z_][\w]*)"\s*\(\s*([\w.:]+)\s*\)', re.M)
METHOD = re.compile(r'^\s*function\s+([A-Za-z_][\w]*)\s*:\s*([A-Za-z_][\w]*)\s*\(', re.M)
TOPFUNC = re.compile(r'^\s*function\s+([A-Za-z_][\w]*)\s*\(', re.M)
BIND_OBJECT = re.compile(r':\s*bind_object\s*\(\s*([A-Za-z_][\w]*)\s*\(')
CREATE_TE = re.compile(r'\bCreateTimeEvent\s*\(')
UNIQUE_CALL = re.compile(r'\bAddUniqueCall\s*\(')
ADD_CALL = re.compile(r'\blevel\s*\.\s*add_call\s*\(')
PROCESS_EVQ = re.compile(r'\bProcessEventQueue\s*\(')
LTX_FUNCTOR = re.compile(r'^\s*([\w\.]*functor[\w\.]*|script)\s*=\s*([A-Za-z_][\w]*)\s*\.?\s*([A-Za-z_]?[\w]*)', re.M | re.I)

# The engine calls these by NAME on the module table, never through
# axr_main.make_callback.  Ranked by the beam's suspicion order for the walkout
# scene; each is only wrapped if the live tree actually defines it.
KNOWN_GLOBALS = [
    # per-NPC vision, I-056 found 8 uncached MCM reads per evaluation in here
    ("visual_memory_manager", "get_visible_value"),
    # the deferred-call queue; one frame's worth of time-event bodies
    ("_g", "ProcessEventQueue"),
    # online/offline switching of server objects
    ("sim_board", "update"),
    ("simulation_objects", "get_available_targets"),
    ("smart_terrain", "update"),
    ("se_smart_terrain", "update"),
    ("gulag_general", "update"),
    # scheme dispatch reached from binders
    ("xr_logic", "try_switch_to_another_section"),
    ("xr_logic", "issue_event"),
    ("xr_motivator", "motivator_binder"),
    # relations / tasks, recomputed on switch-online
    ("game_relations", "get_npcs_relation"),
    ("level_tasks", "process_task"),
    # restrictor schemes
    ("sr_light", "update"),
    ("sr_idle", "update"),
    ("bind_restrictor", "restrictor_binder"),
]

# binder / server-object methods the engine calls directly, in the order the
# walkout profiler wraps them.  `update` is last because it is the only one that
# is per-object per-frame.
BINDER_METHODS = ["net_spawn", "net_destroy", "reinit", "load", "save",
                  "net_save", "net_import", "net_export", "net_Relcase",
                  "shedule_update", "update"]

ONLINE_HINT = ("net_spawn", "net_destroy", "reinit", "on_register", "on_unregister",
               "can_switch_online", "can_switch_offline", "switch_online", "switch_offline",
               "STATE_Write", "STATE_Read")


def live_configs():
    """{lowercase relative path: (Path, tag)} for the .ltx the game loads."""
    winners = {}

    def take(root: Path, tag: str):
        if not root.is_dir():
            return
        for f in root.rglob("*.ltx"):
            winners.setdefault(f.relative_to(root).as_posix().lower(), (f, tag))

    sys.path.insert(0, str(HERE.parent / "coord"))
    from build_overlay import read_modlist
    for mod in read_modlist(MODLIST):
        take(MODS_DIR / mod / "gamedata" / "configs", "mod:" + mod)
    take(LOOSE_CFG, "loose")
    take(DB_CFG, "db")
    return winners


def census_scripts(winners) -> dict:
    binders, server, schemes = {}, {}, {}
    time_events = Counter()
    unique_calls = Counter()
    add_calls = Counter()
    procq = Counter()
    bind_sites = defaultdict(list)
    globals_found = {}
    module_funcs = {}

    for key, (p, src) in sorted(winners.items()):
        text = read(p)
        mod = key.rsplit(".", 1)[0]
        methods = defaultdict(set)
        for m in METHOD.finditer(text):
            methods[m.group(1)].add(m.group(2))
        module_funcs[mod] = {m.group(1) for m in TOPFUNC.finditer(text)}

        for m in CLASS.finditer(text):
            name, base = m.group(1), m.group(2)
            rec = {"file": key, "source": src, "base": base,
                   "methods": sorted(methods.get(name, ())),
                   "engine_methods": sorted(set(methods.get(name, ())) & set(BINDER_METHODS)),
                   "online_methods": sorted(set(methods.get(name, ())) & set(ONLINE_HINT))}
            if base == "object_binder":
                binders[name] = rec
            elif base.startswith("cse_") or base.startswith("se_"):
                server[name] = rec
            elif "action_base" in base or base.endswith("_evaluator") or base.endswith("_action"):
                schemes[name] = rec

        for m in BIND_OBJECT.finditer(text):
            bind_sites[m.group(1)].append(key)
        n = len(CREATE_TE.findall(text))
        if n:
            time_events[key] = n
        n = len(UNIQUE_CALL.findall(text))
        if n:
            unique_calls[key] = n
        n = len(ADD_CALL.findall(text))
        if n:
            add_calls[key] = n
        n = len(PROCESS_EVQ.findall(text))
        if n:
            procq[key] = n

    for mod, fn in KNOWN_GLOBALS:
        fns = module_funcs.get(mod)
        if fns is None:
            globals_found[f"{mod}.{fn}"] = {"present": False, "why": "no such live script"}
        elif fn in fns:
            globals_found[f"{mod}.{fn}"] = {"present": True,
                                            "source": winners[mod + ".script"][1]}
        else:
            # a class name rather than a function (motivator_binder etc.)
            globals_found[f"{mod}.{fn}"] = {"present": False, "why": "not a module-level function"}

    return {
        "binders": binders, "server_objects": server, "schemes": schemes,
        "bind_sites": {k: sorted(set(v)) for k, v in bind_sites.items()},
        "time_event_sites": dict(time_events.most_common()),
        "unique_call_sites": dict(unique_calls.most_common()),
        "level_add_call_sites": dict(add_calls.most_common()),
        "process_event_queue_sites": dict(procq.most_common()),
        "known_globals": globals_found,
    }


def census_configs(cfgs) -> dict:
    hits = Counter()
    where = defaultdict(set)
    for key, (p, _src) in sorted(cfgs.items()):
        try:
            text = read(p)
        except OSError:
            continue
        for m in LTX_FUNCTOR.finditer(text):
            mod, fn = m.group(2), m.group(3)
            if not mod:
                continue
            label = f"{mod}.{fn}" if fn else mod
            hits[label] += 1
            where[label].add(key)
    return {"functors": dict(hits.most_common()),
            "functor_files": {k: sorted(v)[:5] for k, v in where.items()},
            "config_files": len(cfgs)}


def rank(sc: dict) -> list:
    """Order the doors by how likely they are to fire on a walkout."""
    rows = []
    for name, rec in sc["binders"].items():
        score = 3 * len(rec["online_methods"]) + len(rec["engine_methods"])
        if "update" in rec["methods"]:
            score += 2
        rows.append({"kind": "binder", "name": name, "file": rec["file"],
                     "source": rec["source"], "score": score,
                     "methods": rec["engine_methods"] or rec["methods"][:6]})
    for name, rec in sc["server_objects"].items():
        score = 3 * len(rec["online_methods"]) + (2 if "update" in rec["methods"] else 0)
        rows.append({"kind": "server", "name": name, "file": rec["file"],
                     "source": rec["source"], "score": score,
                     "methods": rec["online_methods"] or rec["methods"][:6]})
    rows.sort(key=lambda r: (-r["score"], r["name"]))
    return rows


def wrap_list(sc: dict, top: int = 24) -> dict:
    """The list the walkout overlay should carry, as Lua-ready pairs."""
    ranked = rank(sc)
    binders = [{"module": r["file"].rsplit(".", 1)[0], "class": r["name"],
                "methods": [m for m in BINDER_METHODS if m in r["methods"]] or ["update"]}
               for r in ranked if r["kind"] == "binder"][:top]
    globs = [g for g, v in sc["known_globals"].items() if v.get("present")]
    return {"binders": binders, "globals": globs}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", type=Path)
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--wrap-list", action="store_true", help="print the overlay's wrap list only")
    ap.add_argument("--no-configs", action="store_true", help="skip the .ltx sweep (slow)")
    a = ap.parse_args(argv)

    winners = live_scripts()
    sc = census_scripts(winners)
    out = {"live_scripts": len(winners), "scripts": sc}
    if not a.no_configs:
        out["configs"] = census_configs(live_configs())

    if a.wrap_list:
        print(json.dumps(wrap_list(sc), indent=1))
        return 0

    print(f"live winner scripts: {len(winners)}")
    print(f"object_binder classes: {len(sc['binders'])}   "
          f"cse_/se_ server classes: {len(sc['server_objects'])}   "
          f"scheme action classes: {len(sc['schemes'])}")
    print(f"CreateTimeEvent sites: {sum(sc['time_event_sites'].values())} in "
          f"{len(sc['time_event_sites'])} files; AddUniqueCall "
          f"{sum(sc['unique_call_sites'].values())}; level.add_call "
          f"{sum(sc['level_add_call_sites'].values())}\n")

    print(f"{'#':>3} {'kind':7} {'class':30} {'file':30} {'src':22} {'sc':>3}  methods")
    for i, r in enumerate(rank(sc)[:a.top], 1):
        print(f"{i:3d} {r['kind']:7} {r['name'][:30]:30} {r['file'][:30]:30} "
              f"{r['source'][:22]:22} {r['score']:3d}  {','.join(r['methods'][:6])}")

    print("\nknown engine-called globals, against the live tree:")
    for g, v in sc["known_globals"].items():
        mark = "OK " if v.get("present") else "-- "
        print(f"  {mark}{g:48} {v.get('source') or v.get('why')}")

    print("\ndeferred-call sites (top 10 files):")
    for label, d in (("CreateTimeEvent", sc["time_event_sites"]),
                     ("AddUniqueCall", sc["unique_call_sites"]),
                     ("level.add_call", sc["level_add_call_sites"]),
                     ("ProcessEventQueue", sc["process_event_queue_sites"])):
        top = list(d.items())[:10]
        print(f"  {label}: " + (", ".join(f"{k}={v}" for k, v in top) or "none"))

    if "configs" in out:
        print(f"\n.ltx functor bindings over {out['configs']['config_files']} live config files "
              f"({len(out['configs']['functors'])} distinct):")
        for k, v in list(out["configs"]["functors"].items())[:a.top]:
            print(f"  {v:5d}  {k}")

    if a.json:
        a.json.write_text(json.dumps(out, indent=1), encoding="utf-8")
        print(f"\nwrote {a.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
