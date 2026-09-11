# tools

Thin CLIs over the `aalo` package in `../framework`. Each adds that directory to
`sys.path` itself, so they run from anywhere:

```
py -3.12 tools/tail_xray_log.py [--errors] [FILE]
py -3.12 tools/presentmon_check.py [--quiet]
py -3.12 tools/seed_demo_runs.py [--count N] [--clean]
```

- **tail_xray_log.py** follows the newest `xray_*.log` in `Anomaly/appdata/logs`
  and switches files when the engine starts a new one, so it survives a restart
  of the game. `--errors` shows only warnings, errors and crashes.
- **presentmon_check.py** reports whether real per-frame capture is available.
  Exit code 1 plus an install hint when it is not; the framework then falls back
  to a psutil CPU/RSS sampler and reports `fps_avg` as null.
- **seed_demo_runs.py** writes synthetic dry-runs so the dashboard has data
  before the first real measurement. `--count N` covers N ideas and writes a
  baseline and a variant run for each, so the summary has deltas. Runs are
  marked `"demo": true` in their manifest and attach to existing ideas;
  `--clean` removes earlier demo runs. It only invents placeholder ideas when
  `data/ideas.json` is empty.

Everything here is read-only against the game install except the profile copies
the framework makes under `profiles/aalo-*`. See `../framework/README.md` for
the elevation caveat that applies to real runs.
