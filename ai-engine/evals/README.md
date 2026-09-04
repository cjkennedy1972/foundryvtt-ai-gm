# Eval harness — replay scenarios against a real model

124+ test files prove the engine's plumbing works. This harness answers the
question none of them can: **is the GM any good?** It replays a frozen corpus
of scenarios through the full pipeline (session start, player messages,
encounters, idle pacing) against a *real model* and emits a scored report.

## One command

```bash
cd ai-engine

# Deterministic self-check (no model, no credentials — what CI runs)
python -m evals.replay --backend scripted

# Measure the real model (reads LLM_BASE_URL / MODEL / LLM_API_KEY from .env)
python -m evals.replay --backend live
```

Each run writes `evals/results/<timestamp>/` with one event log per scenario
(`<id>.events.json` — every Foundry call and every LLM exchange) plus
`report.json` and a human-readable `report.md`. The command exits non-zero if
any scenario fails a hard check or contradicts canon.

## The contradiction metric (the one number we optimise)

CKP-97: does turn N contradict a canonised fact or a prior event? Three
detectors, each hit tagged by source in the report:

- **`[canon]`** — scenario-authored `contradiction_patterns` regexed over
  GM-spoken text. Highest precision; only catches what an author predicted.
- **`[event]`** — the event-log vitality detector (`evals/contradictions.py`).
  "Brother Fenwick is dead" is extracted from canon fact *text* and enforced
  against the run's own event log: a dead NPC may not speak (`speaker=`) and
  may not be narrated acting alive ("the ghost of Fenwick" and other remnant
  mentions are excluded). No per-scenario authoring needed.
- **`[judge]`** — opt-in LLM auditor (`--judge`), one temperature-0 call per
  GM turn against the same configured endpoint, judging each turn against
  canon facts and prior turns. Catches novel phrasing the deterministic
  detectors can't. Judge coverage is printed in the report — a judge that
  errors records `judge_errors`, so a dead judge can never greenwash a run.

All three are precision-first: a false positive fails a good run and erodes
trust in the gate; a false negative is caught by the next corpus scenario.
The corpus is the ratchet — add a scenario per observed failure.

## Tracking across builds

```bash
python -m evals.replay --backend live --judge --record   # measure and record
python -m evals.metrics trend                            # the series
python -m evals.metrics publish                          # regenerate METRICS.md
```

`--record` appends to `evals/metrics/history.jsonl` (committed; git SHA,
backend, model, rate, per-source breakdown). `evals/METRICS.md` is the
published number — release criterion #5 tracks it trending down.

## The off-session tick gate (CKP-101 contract)

The world tick must not poison its own lore: **contradiction rate must not
rise with tick volume.** The mechanism is ready now:

1. Before delivering a tick's output, audit it with
   `evals.contradictions.scan_turns(turns, canon_facts, prior_events)` —
   plain strings in, structured contradictions out. Undelivered output can't
   contradict anything.
2. After a tick batch, record it:
   `python -m evals.metrics append <report.json> --tick-volume <days>`.
3. Enforce: `python -m evals.metrics gate` exits 1 if any recorded tick run's
   rate exceeds the best rate at a strictly lower volume. With fewer than two
   tick records the gate passes *unproven* — it binds from the second run on.

## The corpus

`evals/scenarios/*.json` — 30 scenarios, one file each, reviewable in a diff.
A scenario is:

- **`script`** — the player-message script (plus `session_start`, `idle`,
  `pacing`, and `hook` events). A step with `"dropped": true` is expected to
  never reach the LLM (e.g. a message sent while paused).
- **`setup`** — the world the GM believes it's in: scene, mode, actor/token
  fixtures, NPC personalities, and `llm_context` (canon/world/npc/house-rule
  text injected through the real context path), plus optional `history`.
- **`canon_facts`** — established truths with `contradiction_patterns`
  (regexes). Every pattern hit in GM-spoken text counts one contradiction.
  The corpus **contradiction rate** is the v2.0 north-star metric.
- **`expect`** — hard gates: `must_call` / `must_not_call` (supports
  `method:key=value`, e.g. `roll:speaker=Aria`), `must_mention` /
  `must_not_mention` (a list entry is any-of alternatives), `min/max_llm_calls`.
- **`scripted_responses`** — the human-authored golden run: what a good GM
  does in this scene, as action JSON.

`evals/baselines/<id>.events.json` — the frozen event log of the golden run.
Review it alongside the scenario; it's what "good" looked like when the
scenario was authored.

## Freezing and diffing

```bash
# Re-freeze baselines (after changing scripted_responses or the harness)
python -m evals.replay --backend scripted --freeze

# Replay a subset
python -m evals.replay --backend live --scenario canon_locked_door,secret_villain_identity
```

A live replay diffs each run against its frozen baseline and scores:

- **Hard checks** — the `expect` block. Failures mean the run is broken.
- **Contradictions** — canon/event-log/judge hits in GM-spoken text. Gated.
- **Drift** — action-sequence similarity to the baseline (1.0 = identical),
  plus narrated-text keyword overlap. *Not* gated: a better-but-different run
  drifts too. Read the event log diff when drift moves.

## What the corpus covers

Session flow (start, idle pacing, pause/resume) · NPC dialogue and memory ·
secret-keeping (unrevealed villain, undetected trap) · canon consistency
(locked door, dead NPC, burned bridge, completed quest, weather, PC state) ·
combat (ambush, de-escalation, solo death-as-setback, NPC personality
tactics, players-roll-their-own-dice) · skill checks · rests, treasure,
commerce, scene changes.

## Adding a scenario

1. Write `evals/scenarios/my_scenario.json` (copy the nearest neighbour).
2. `python -m evals.replay --backend scripted --freeze --scenario my_scenario`
3. Review the frozen baseline, commit scenario + baseline together.
4. `tests/test_eval_harness.py` enforces corpus integrity in CI.

## Live runs and cost

A live replay runs one LLM call per script beat (~35 calls for the full
corpus) against whatever endpoint `LLM_BASE_URL` points at — the same
configuration the engine uses. It is an *attended* tool: the replay's
`LLMManager` is created without a session `TokenUsage` tracker, so the
session spend cap does not apply here. Point it at your local model.
