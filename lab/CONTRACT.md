# ALAO Lab — shared contract v2 (read before writing anything)

GOAL: improve ALAO (Anomaly Lua Auto Optimizer, repo C:\code\GIT\anomaly_alao, read its CLAUDE.md and
README.md first). ALAO parses Anomaly mod scripts (Lua 5.1 / LuaJIT 2.0.4, *.script) into an AST,
reports perf/safety findings, and rewrites scripts in place. The lab exists to make ALAO better:
correctness of its transforms, coverage of new patterns, safety, and its own speed.
We are NOT hunting for a magic GAMMA settings config. Game knobs are irrelevant except as an
end-to-end FPS harness that proves ALAO's rewrites help (that harness already exists in framework/).

Locations:
  Repo (code under test, git):   C:\code\GIT\anomaly_alao      -> tests/ and tools/ additions go HERE
  Lab (data + dashboard, no git): C:\code\GIT\anomaly_alao\lab  -> data/, dashboard/, docs/, reports/
  Game install (READ-ONLY):      D:\GOG_Games\Gamma\S.T.A.L.K.E.R. GAMMA
     corpus = GAMMA/mods/*/gamedata/scripts/*.script (2108 files across all mods; 1503 files from 577 enabled mods is the working corpus; many CP1251)
     plus Anomaly/gamedata/scripts (66 vanilla files). NEVER run --fix against the install. Copy first.
  Scratch corpora: C:\code\GIT\anomaly_alao\test\ or extracted\ (gitignored) or the lab's data/corpus/.
Toolchain: `py -3.12` has luaparser 4.2.0 + jinja2. `py -3.12 -m pip install lupa pytest` is allowed
  (lupa bundles LuaJIT: use it for compile-checking ALAO output and differential execution tests).
  No standalone luajit.exe on the machine.

Data schema (JSON, UTF-8):
  data/ideas.json -> { "ideas": [ { "id": "I-001", "title", "category":
      "pattern|analyzer|transformer|safety|alao-perf|tooling|corpus|other",
      "hypothesis", "change" (what to change in which ALAO module), "measure" (which test/metric proves it),
      "expected_gain": "low|med|high", "risk": "low|med|high",
      "status": "proposed|queued|running|kept|pruned", "score": float|null, "parent": id|null,
      "generation": int, "notes": str } ] }
  data/corpus/<run_id>/manifest.json -> { "run_id": "YYYYMMDD-HHMMSS-<slug>", "alao_commit": sha,
      "alao_dirty": bool, "corpus": "gamma-0.9.4|vanilla-1.5.3|fixtures", "corpus_files": int,
      "flags": [..], "started", "finished", "status": "done|failed", "notes" }
  data/corpus/<run_id>/results.json -> { "analyze_s": float, "fix_s": float|null,
      "parse_failures": [{file, error}], "timeouts": [file], "crashes": [{file, traceback}],
      "findings_by_pattern": {pattern: count}, "findings_by_severity": {GREEN|YELLOW|RED|DEBUG: count},
      "files_modified": int, "edits_applied": int, "edits_dropped_overlap": int,
      "compile_failures_after_fix": [{file, error}]   (lupa compile of each rewritten file),
      "idempotence_violations": [file]  (second --fix pass still changed the file),
      "differential_failures": [{file, detail}] (optional), "extra": {} }
  data/runs/<run_id>/ (existing end-to-end FPS schema, unchanged; see framework/README.md)
  data/ideas-game-knobs.json -> the previous 30 game-knob ideas, archived; not the beam.

Rules: never write inside the game install; never git commit; tests must pass with
`py -3.12 -m pytest` run from the repo root; keep Priler's casual comment tone in repo code.
