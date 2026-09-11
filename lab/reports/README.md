# reports/ — sanity agent

Read-only audits of the GAMMA install at `D:\GOG_Games\Gamma\S.T.A.L.K.E.R. GAMMA`.

| File | What it is |
|---|---|
| `install-sanity-report.md` | Full sanity check of the fresh GOG install, 2026-09-10. Verdict, engine and MO2 state, Grok installer state, integrity spot-check, host specs, A-Life knobs, and open issues. |
| `corpus-sanity-report.md` | ALAO regression sanity pass over the vanilla (66 file) and GAMMA (1503 file) script corpora, 2026-09-10. Parse/timeout/crash/compile results, top patterns, and the `table_insert_append` edit-drop bug. |

Nothing in this directory writes to or launches the game install. Re-run the audit by re-reading the install rather than by trusting these files, which capture a moment in time.
