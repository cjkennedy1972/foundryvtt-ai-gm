# GM quality metrics

The contradiction rate is the one number v2.0 optimises (CKP-97):
how often a generated turn contradicts canonised fact or prior
events, per scenario-run over the frozen 30-scenario corpus.
Everything else is a dashboard. History: `metrics/history.jsonl`;
regenerate this file with `python -m evals.metrics publish`.

## Current

| Backend | Model | Contradiction rate | Pass | Build | Recorded |
|---|---|---|---|---|---|
| scripted | mock-model | **0.0000** | 30/30 | `4dae333` | 2026-09-04T16:28:31+00:00 |

## Off-session tick gate

Gate: **PASS** — unproven: 0 tick record(s) — the gate binds once runs at two or more volumes are recorded.

## History

| Recorded | Backend | Model | Rate | By source | Pass | Build |
|---|---|---|---|---|---|---|
| 2026-09-04T16:28:31+00:00 | scripted | mock-model | 0.0000 | — | 30/30 | `4dae333` |
