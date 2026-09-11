# docs/ — beam agent

Owned by the beam agent. Research notes and the ranked idea beam for AALO.

| File | Contents |
|---|---|
| `beam-ideas.md` | Install research findings, A/B methodology, and the ranked beam of 30 optimization ideas. |

The machine-readable beam lives in `../data/ideas.json` and follows the `CONTRACT.md`
schema exactly. `beam-ideas.md` and `ideas.json` must be updated together: the markdown
carries the reasoning, the JSON carries the state the framework and dashboard agents read.

Generation 1 is ideas `I-001` through `I-008`, status `queued`. Everything else is
`proposed`. Status transitions (`queued` -> `running` -> `kept` or `pruned`) are written by
the framework agent's A/B runner, not by hand.

The game install at `D:\GOG_Games\Gamma\S.T.A.L.K.E.R. GAMMA` is read-only to this agent.
Every current value quoted in `beam-ideas.md` was read from that install on 2026-09-10.
