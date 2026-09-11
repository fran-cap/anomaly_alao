# Lab

The test/benchmark lab for this repo lives in `lab/` (contract: `lab/CONTRACT.md`).

- Dashboard: `py -3.12 lab/dashboard/server.py --port 8765` then open http://127.0.0.1:8765
- Corpus regression: `py -3.12 tools/corpus_extract.py --corpus gamma` then `py -3.12 tools/corpus_run.py ...` (see tools/README.md)
- In-game FPS harness: `lab/framework` (needs an elevated terminal; see lab/framework/README.md)
- Lab tests: `py -3.12 -m pytest lab/tests -q`
