# AALO Lab dashboard

A local, read-mostly web view over the shared `data/` directory: ALAO corpus
regression runs (the primary view), the idea beam, the end-to-end FPS run table
with per-run frametime charts, and the archived game-knob ideas.

Python 3.12 standard library only (`http.server`, `json`, `csv`). No pip
dependencies, no CDN, no build step. Binds `127.0.0.1` only.

## Run

```
py -3.12 dashboard/server.py --port 8765 --data C:\code\GIT\anomaly_alao\lab\data
```

Then open <http://127.0.0.1:8765/>.

Flags:

| flag | default | meaning |
| --- | --- | --- |
| `--data` | `../data` (relative to `server.py`) | the shared data directory |
| `--port` | `8765` | TCP port |
| `--host` | `127.0.0.1` | bind address; keep it local |
| `--quiet` | off | suppress the per-request access log |

The server never fails on missing or malformed input. A missing `data/`
directory, an unparsable `ideas.json`, a truncated `samples.csv`: each yields an
empty or partial result plus a `[dashboard]` warning on stderr.

To demo without any real runs, point it at the bundled fixtures (2 ideas,
3 FPS runs with ~40k CSV samples between them, 3 corpus runs across 2 fake ALAO
commits, and 2 archived knob ideas):

```
py -3.12 dashboard/server.py --data dashboard/fixtures
```

Note that the status and score endpoints write to `<data>/ideas.json`, so
pointing `--data` at the fixtures will edit the fixture file.

## API

All responses are JSON, `Cache-Control: no-store`.

| method | path | returns |
| --- | --- | --- |
| GET | `/api/corpus` | `{"runs": [...]}` manifest+results merged per corpus run, newest `started` first |
| GET | `/api/corpus/<run_id>` | one run in full: `findings_by_pattern`, `patterns` (sorted desc), severity, and every failure list |
| GET | `/api/corpus/compare?a=&b=` | per-pattern deltas, failure-list diffs, timing deltas; defaults `a`=latest, `b`=previous |
| GET | `/api/ideas` | `{"ideas": [...]}` verbatim idea records |
| GET | `/api/ideas-archive` | `{"ideas": [...], "source": "..."}` read-only `ideas-game-knobs.json` |
| GET | `/api/runs` | `{"runs": [...]}` manifest fields merged with metrics, newest `started` first |
| GET | `/api/runs/<run_id>` | `{manifest, metrics, samples, sample_count, downsampled}` |
| GET | `/api/summary` | a `corpus` block (see below) plus counts by status, best run overall and per idea, baseline deltas |
| GET | `/api/health` | `{"ok": true, "data_dir": "..."}` |
| POST | `/api/ideas/<id>/status` | body `{"status": "proposed\|queued\|running\|kept\|pruned"}` |
| POST | `/api/ideas/<id>/score` | body `{"score": 0.72}` or `{"score": null}` |

Errors return `{"error": "..."}` with 400 (bad body or value), 403 (path
escape), 404 (unknown idea, run, or endpoint).

Both POST endpoints patch a single field of a single idea and rewrite
`<data>/ideas.json` atomically: a temp file in the same directory, `fsync`, then
`os.replace`. Every other field of the record is preserved. Writes are guarded
by a process-level lock, so concurrent edits from the UI cannot interleave.

`samples` is downsampled to at most 2000 points. Downsampling is bucketed and
peak-preserving: the range is split into 2000 equal buckets and the worst
frametime in each bucket is kept, so stutter spikes survive the reduction.
`sample_count` is the true row count; `downsampled` says whether any rows were
dropped.

### Corpus runs

`data/corpus/<run_id>/manifest.json` + `results.json` (CONTRACT v2) are merged
into one flat record per run. Derived fields the raw files do not carry:

| field | meaning |
| --- | --- |
| `alao_commit_short` | first 8 chars of `alao_commit` |
| `findings_total` | sum of `findings_by_pattern` |
| `pattern_count` | number of distinct patterns |
| `parse_failures`, `compile_failures_after_fix`, `idempotence_violations`, `timeouts`, `crashes`, `differential_failures` | **counts**, not lists; the lists live on the detail endpoint as `*_list` |
| `health` | `fail` for any compile failure, idempotence violation, crash or differential failure; `warn` for any parse failure or timeout; `unknown` while `results.json` is missing; else `ok` |
| `has_results` / `has_manifest` | false while the run is still being written |

`/api/corpus/compare` treats `a` as the newer run and reports `a - b`:
`patterns` (sorted by biggest absolute delta, each flagged `new`/`gone`),
`severity`, `findings_total`, `timing.analyze_s` / `timing.fix_s`, and
`failures.<kind>` as a set diff of file paths (`added` regressed, `removed`
fixed). With fewer than two runs it returns a null `a`/`b` and a `note` rather
than an error; an unknown run id is a 404.

A run whose `manifest.json` is not on disk yet still lists: it sorts by the
`YYYYMMDD-HHMMSS` prefix of its id, reports `health: unknown` rather than a
false `ok`, and is never chosen as a compare default.

`/api/summary` gains a `corpus` block: `runs_total`, `pending` (runs without
results yet), the whole `latest` *completed* run record, `health`, `findings_total`, `parse_failures`,
`compile_failures_after_fix`, `idempotence_violations`, and a `trend` list
(oldest first) of per-run findings and failure counts for the trend strip. A
missing `data/corpus/` directory yields `runs_total: 0` and `health: unknown`.

### Baseline vs variant

`/api/summary` reports a `deltas` list comparing each idea's best run against a
baseline. A run counts as a baseline when it has no `idea_id`, or its
`manifest.notes` start with `baseline` or contain `baseline=true`. Notes
mentioning `variant` are never treated as a baseline, so prose such as
"measured against the baseline" does not misfire. A variant is compared against
a baseline carrying the same `idea_id` if one exists, otherwise the newest
global baseline. Crashed runs are excluded from every "best" calculation.

## UI

Single page at `/`, served from `static/`: `index.html`, `app.js`, `style.css`.
Vanilla JS, dark theme, laid out for 1080p, tested in Firefox and Chrome. Four
tabs, `Corpus` first and default; the active tab is mirrored in the URL hash
(`#corpus`, `#ideas`, `#runs`, `#archive`) so a view survives a reload.

**Corpus** (primary)

- **Tiles** - health, latest run id with commit short sha and a `dirty` flag,
  files analyzed, findings total, parse failures, compile failures after
  `--fix`, idempotence violations, and `analyze_s`/`fix_s`.
- **Trend strip** - inline SVG, no library: a findings-total line across runs
  (oldest first) with failure bars on a second scale and a health-coloured dot
  per run.
- **Runs table** - sortable; click a row for the detail panel.
- **Run detail** - `findings_by_pattern` as a horizontal bar list sorted
  descending, the severity breakdown, the run's manifest, and collapsible
  lists of parse failures, compile failures and idempotence violations (file
  plus first line of the error).
- **Compare** - two run pickers (defaulting to latest vs previous) over
  `/api/corpus/compare`: headline deltas, the per-pattern delta table, the
  failure-list diff and the severity delta. Colour follows intent, not sign:
  more findings is green (wider ALAO coverage), more failures or more seconds
  is red.

**Ideas / FPS runs / Archived knobs**

- **Summary tiles** - ideas by status, runs done/failed/crashed, best `fps_avg`
  run, baseline, and per-idea deltas.
- **Ideas beam table** - generation, id, title, category, expected gain, risk,
  status, score. Click any header to sort. The category filter is built from
  whatever categories the data actually contains, so the CONTRACT v2 set
  (`pattern|analyzer|transformer|safety|alao-perf|tooling|corpus|other`) and
  any later addition both work with no code change. Status is a dropdown and score is a
  number field; both POST on change and flash green on success, red on failure.
- **Runs table** - run id, idea, status, started, duration, `fps_avg`, 1% low,
  p99 frametime, crashed. Sortable; click a row to open the detail panel.
- **Archived knobs** - read-only table over `/api/ideas-archive`, the 30
  pre-CONTRACT-v2 game-knob ideas.
- **Run detail** - an inline SVG frametime line chart drawn from the raw path
  data (no charting library), plus the full manifest, metrics, and
  `config_diff` rendered as `old -> new`.

The page refreshes every 10 seconds with `fetch`, never a reload. It skips
re-rendering the ideas table while a cell is focused, so an in-progress edit is
never clobbered. Refresh pauses while the tab is hidden.

## Data schema

The canonical schema lives in [../CONTRACT.md](../CONTRACT.md) and this
dashboard follows it exactly:

```
data/ideas.json
data/ideas-game-knobs.json          archived, read-only
data/corpus/<run_id>/manifest.json
data/corpus/<run_id>/results.json
data/runs/<run_id>/manifest.json
data/runs/<run_id>/metrics.json
data/runs/<run_id>/samples.csv    t_s,frametime_ms,fps
data/runs/<run_id>/xray.log
```

The CSV reader is lenient about column order and case, accepts `t`/`frametime`
as aliases, derives `fps` from `frametime_ms` (or the reverse) when one is
missing, and falls back to the row index when `t_s` is absent.

## Tests

```
py -3.12 -m pytest tests/test_dashboard.py -q
```

24 tests. The suite starts the server on a free port against a temp copy of
`dashboard/fixtures/`, then asserts every endpoint's shape, the write path,
input validation, path-traversal rejection, and graceful handling of corrupt
files. The corpus fixtures cover the three interesting shapes: a `fail` run
(parse failures, compile failures after `--fix`, idempotence violations), a
`warn` run (parse failures only) and a clean `ok` run, spread over two fake
ALAO commits. Install pytest with `py -3.12 -m pip install pytest` if needed.
