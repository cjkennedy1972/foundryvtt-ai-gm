"""Replay the scenario corpus and emit a scored report.

One command:

    cd ai-engine
    python -m evals.replay --backend scripted          # deterministic, CI-safe
    python -m evals.replay --backend live              # real model from .env

What it does, per scenario: builds a ChatListener wired to a recording
Foundry mock, primes the LLM with the scenario's world/canon context, plays
the scenario's player-message script, and records every Foundry call and
every LLM exchange as the run's event log. The run is then scored
(``evals.score``) and a JSON + Markdown report is written.

Backends:

- ``scripted`` — plays each scenario's human-authored ``scripted_responses``
  through the pipeline. Deterministic; used to freeze baselines and to
  regression-test the harness itself in CI.
- ``live`` — a real ``llm.manager.LLMManager`` against the configured
  endpoint (``LLM_BASE_URL`` / ``MODEL`` / ``LLM_API_KEY``). Use ``--freeze``
  to promote a run's event logs to the reviewed baseline.

Live-call spend gate (CKP-141): any invocation that can place real LLM calls
(``--backend live``, or ``--judge`` under either backend) requires
``EVAL_LIVE_BUDGET_CONFIRMED=true`` in the environment, and every call is
charged against a hard per-run token cap enforced through the production
``llm.usage.TokenUsage`` preflight path. The cap defaults to
``DEFAULT_LIVE_TOKEN_BUDGET`` and can be overridden with
``EVAL_LIVE_TOKEN_BUDGET`` (0 disables the cap — the confirmation env var is
still required; negative values are rejected).

Useful flags:

    --scenario id[,id...]   replay a subset
    --freeze                write each run's event log to evals/baselines/
    --out DIR               report/artifact directory (default: evals/results/<timestamp>)
    --keep-going            don't stop at the first scenario error
    --judge                 LLM-audit every GM turn for contradictions the
                            deterministic detectors can't see (evals.judge)
    --record                append this run to evals/metrics/history.jsonl
    --tick-volume N         tag the recorded run with a world-tick volume
                            (the tick gate: rate must not rise with volume)
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evals import metrics as metrics_mod
from evals import score as score_mod
from evals.contradictions import spoken_turns
from evals.harness import (
    MockDatabase,
    MockFoundryClient,
    MockNPCRegistry,
    MockStateTracker,
    RecordingLLM,
    ScriptedLLM,
    build_listener,
)
from evals.scenario import Scenario, load_corpus

logger = logging.getLogger("evals.replay")


# ---------------------------------------------------------------------------
# Live-call spend gate (CKP-141)
# ---------------------------------------------------------------------------

#: Env var that must be "true" before any invocation that can place real LLM
#: calls is allowed to run. Unattended contexts (CI, scripts) never get live
#: calls by accident — someone must deliberately opt in.
LIVE_CONFIRM_ENV = "EVAL_LIVE_BUDGET_CONFIRMED"

#: Env var overriding the per-run token cap for live calls.
LIVE_BUDGET_ENV = "EVAL_LIVE_TOKEN_BUDGET"

#: Hard per-run token cap for live calls — matches the production default
#: session budget (config.settings.llm_token_budget).
DEFAULT_LIVE_TOKEN_BUDGET = 100_000


def _live_budget() -> int:
    """The run's token cap: env override or the hard-coded default.

    A negative override is rejected, not clamped: clamping would turn a typo
    like ``-100000`` into 0, which ``TokenUsage`` reads as *unlimited* —
    silently defeating the cap. The literal ``0`` stays the documented
    explicit opt-out.
    """
    raw = os.environ.get(LIVE_BUDGET_ENV)
    if not raw:
        return DEFAULT_LIVE_TOKEN_BUDGET
    try:
        budget = int(raw)
    except ValueError:
        raise ValueError(f"{LIVE_BUDGET_ENV} must be an integer, got {raw!r}")
    if budget < 0:
        raise ValueError(
            f"{LIVE_BUDGET_ENV} must be >= 0 (0 disables the cap), got {raw!r}")
    return budget


def _live_gate_error(args: argparse.Namespace) -> Optional[str]:
    """None if the invocation may proceed; otherwise the refusal reason."""
    if args.backend != "live" and not args.judge:
        return None
    if os.environ.get(LIVE_CONFIRM_ENV, "").strip().lower() != "true":
        trigger = "--backend live" if args.backend == "live" else "--judge"
        return (f"{trigger} places real LLM calls but {LIVE_CONFIRM_ENV}=true "
                f"is not set — refusing to run uncapped. Set it to acknowledge "
                f"the spend (cap: {_budget_display()}).")
    try:
        _live_budget()
    except ValueError as exc:
        return str(exc)
    return None


def _budget_display() -> str:
    try:
        return f"{_live_budget():,} tokens"
    except ValueError:
        return f"unparseable {LIVE_BUDGET_ENV}"


def _make_usage_tracker():
    """A production TokenUsage backed by the harness's in-memory store.

    One tracker (and one synthetic session id) is shared by every scenario
    and the judge, so the cap is per *run*, not per scenario.
    """
    from llm.usage import TokenUsage

    session_id = f"eval-run-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    return TokenUsage(MockDatabase(), _live_budget()), session_id


def _metered_ask(ask, tracker, session_id: str):
    """Wrap the judge's ask callable so its calls count toward the run cap.

    The judge talks to the endpoint directly (no LLMManager), so charge a
    conservative estimate: prompt tokens + its 256-token max completion.
    """
    from llm.usage import Usage
    from utils.token_counter import estimate_tokens

    async def metered(system: str, user: str) -> str:
        estimated = estimate_tokens(system) + estimate_tokens(user) + 256
        await tracker.before_call(session_id, "eval-judge", estimated)
        reply = await ask(system, user)
        await tracker.record(session_id, "eval-judge", Usage(estimated, 0),
                             getattr(ask, "model", None) or "judge",
                             call_type="judge")
        return reply

    metered.close = ask.close  # type: ignore[attr-defined]
    metered.model = getattr(ask, "model", None)  # type: ignore[attr-defined]
    return metered


# ---------------------------------------------------------------------------
# Event log
# ---------------------------------------------------------------------------

def _event_log(scenario: Scenario, backend: str, model: str,
               foundry_calls: List[Dict], llm_calls: List[Dict],
               elapsed_s: float,
               judge_calls: Optional[List[Dict]] = None) -> Dict:
    """The frozen artifact: one JSON document per scenario run."""
    return {
        "scenario": scenario.id,
        "backend": backend,
        "model": model,
        "frozen_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "elapsed_s": round(elapsed_s, 2),
        "foundry_calls": foundry_calls,
        "llm_calls": llm_calls,
        "judge_calls": judge_calls or [],
    }


def load_baseline(scenario: Scenario) -> Optional[List[Dict]]:
    path = scenario.baseline_path
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))["foundry_calls"]


# ---------------------------------------------------------------------------
# LLM backends
# ---------------------------------------------------------------------------

def _scripted_llm(scenario: Scenario) -> ScriptedLLM:
    if not scenario.scripted_responses:
        raise ValueError(
            f"{scenario.id}: scripted backend requires 'scripted_responses'")
    return ScriptedLLM(list(scenario.scripted_responses))


def _live_llm(scenario: Scenario) -> RecordingLLM:
    """A real LLMManager, wrapped so every exchange is recorded."""
    from llm.manager import LLMManager

    return RecordingLLM(LLMManager())


def _prime_llm(llm, scenario: Scenario) -> None:
    """Push the scenario's world into the LLM through the real context path."""
    ctx = scenario.setup.get("llm_context", {})
    for key, setter in (("canon", "set_dynamic_canon_context"),
                        ("world", "set_dynamic_world_context"),
                        ("npcs", "set_dynamic_npc_context"),
                        ("house_rules", "set_dynamic_house_rules_context")):
        if ctx.get(key) and hasattr(llm, setter):
            getattr(llm, setter)(ctx[key])
    history = scenario.setup.get("history")
    if history and hasattr(llm, "_conversation_history"):
        llm._conversation_history = [dict(m) for m in history]


# ---------------------------------------------------------------------------
# Scenario driver
# ---------------------------------------------------------------------------

async def run_scenario(scenario: Scenario, backend: str,
                       usage_tracker=None,
                       usage_session_id: Optional[str] = None) -> Dict:
    """Play one scenario's script; return its event log."""
    setup = scenario.setup
    foundry = MockFoundryClient(
        actors=setup.get("actors"),
        tokens=setup.get("tokens"),
        scenes=setup.get("scenes"),
        scene_name=setup.get("scene", "The Sunken Crypt"),
    )
    db = MockDatabase()
    state = MockStateTracker(
        mode=setup.get("mode", "exploration"),
        scene=setup.get("scene", "The Sunken Crypt"),
    )
    npc_registry = MockNPCRegistry(setup.get("npcs"))

    if backend == "scripted":
        llm = _scripted_llm(scenario)
    else:
        llm = _live_llm(scenario)
        if usage_tracker is not None:
            # Charge every live call to the run's shared cap through the
            # production preflight path (RecordingLLM delegates these to the
            # wrapped LLMManager).
            llm.set_usage_tracker(usage_tracker)
            llm.set_usage_context(
                usage_session_id or f"eval-{scenario.id}",
                setup.get("campaign", "Eval Campaign"))
    _prime_llm(llm, scenario)

    listener = build_listener(llm, foundry, db, state, npc_registry=npc_registry)
    await db.create_session(f"eval-{scenario.id}", setup.get("campaign", "Eval Campaign"))
    listener._running = True

    start = time.perf_counter()
    try:
        for step in scenario.script:
            event = step["event"]
            if event == "session_start":
                await listener._process_proactive_action(reason="session_start")
            elif event in ("idle", "pacing"):
                # Fire the beat directly instead of waiting out the real timer.
                # Zero the anti-stacking clock so scripted back-to-back beats
                # aren't dropped by the 15s production gap — the corpus
                # exercises beat content, not wall-clock pacing.
                listener._last_proactive_beat_at = 0.0
                await listener._process_proactive_action(reason=event)
            elif event == "player_message":
                await listener._handle_chat_event({
                    "speaker": step.get("speaker", "Aria"),
                    "message": step["message"],
                    "type": "general",
                })
            elif event == "hook":
                await listener._handle_hook_event({
                    "hook": step["hook"], "data": step.get("data", {}),
                })
            # Cancel any idle timer the last message armed so it can't leak
            # into the next scenario's event log.
            if listener._idle_timer_task and not listener._idle_timer_task.done():
                listener._idle_timer_task.cancel()
    finally:
        if listener._idle_timer_task and not listener._idle_timer_task.done():
            listener._idle_timer_task.cancel()
        await llm.close()

    model = getattr(llm, "model", "unknown")
    return _event_log(
        scenario, backend, model,
        foundry_calls=list(foundry.calls),
        llm_calls=list(getattr(llm, "calls", [])),
        elapsed_s=time.perf_counter() - start,
    )


# ---------------------------------------------------------------------------
# LLM judge
# ---------------------------------------------------------------------------

async def _judge_run(scenario: Scenario, foundry_calls: List[Dict],
                     ask) -> Dict:
    """Audit every GM-spoken turn; shape verdicts for the event log."""
    from evals import judge as judge_mod

    turns = spoken_turns(foundry_calls)
    canon = [cf.fact for cf in scenario.canon_facts]
    result = await judge_mod.judge_turns(turns, canon, ask)
    result["judge_calls"] = [
        {"turn_index": i, "turn": turn,
         "verdict": (None if v is None else
                     {"contradiction": v.contradiction, "reason": v.reason})}
        for i, (turn, v) in enumerate(zip(turns, result["verdicts"]))
    ]
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="python -m evals.replay",
        description="Replay the scenario corpus and emit a scored report.")
    p.add_argument("--backend", choices=["scripted", "live"], default="scripted",
                   help="scripted = frozen golden responses (CI); live = real model "
                        f"(requires {LIVE_CONFIRM_ENV}=true, capped at "
                        f"{LIVE_BUDGET_ENV} tokens)")
    p.add_argument("--scenario", help="comma-separated scenario ids to replay (default: all)")
    p.add_argument("--freeze", action="store_true",
                   help="promote this run's event logs to evals/baselines/")
    p.add_argument("--out", type=Path,
                   help="report directory (default: evals/results/<timestamp>)")
    p.add_argument("--corpus", type=Path, help="scenario directory override")
    p.add_argument("--keep-going", action="store_true",
                   help="continue after a scenario raises")
    p.add_argument("--judge", action="store_true",
                   help="LLM-audit every GM turn for contradictions "
                        "(one extra call per turn; uses the configured endpoint; "
                        f"requires {LIVE_CONFIRM_ENV}=true)")
    p.add_argument("--record", action="store_true",
                   help="append this run's metrics to evals/metrics/history.jsonl")
    p.add_argument("--tick-volume", type=int, default=None,
                   help="tick volume tag for --record (world-tick gate input)")
    p.add_argument("--verbose", "-v", action="store_true")
    return p.parse_args(argv)


async def _amain(args: argparse.Namespace) -> int:
    if args.tick_volume is not None and not args.record:
        print("[eval] --tick-volume only makes sense with --record", file=sys.stderr)
        return 2
    gate_error = _live_gate_error(args)
    if gate_error:
        print(f"[eval] {gate_error}", file=sys.stderr)
        return 2
    only = args.scenario.split(",") if args.scenario else None
    scenarios = load_corpus(args.corpus, only=only)

    out_dir = args.out or (
        Path(__file__).parent / "results"
        / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    out_dir.mkdir(parents=True, exist_ok=True)

    # One shared tracker → the cap is per run across scenarios and judge.
    usage_tracker, usage_session_id = (
        _make_usage_tracker() if args.backend == "live" or args.judge
        else (None, None))

    ask = None
    if args.judge:
        from evals import judge as judge_mod
        ask = _metered_ask(judge_mod.make_ask(), usage_tracker, usage_session_id)

    scores = []
    try:
        for scenario in scenarios:
            print(f"[eval] {scenario.id} ({args.backend}) … ", end="", flush=True)
            error = None
            judge_result = None
            try:
                log = await run_scenario(scenario, args.backend,
                                         usage_tracker, usage_session_id)
            except Exception as exc:  # noqa: BLE001 — an eval must report, not crash
                logger.exception("scenario %s failed", scenario.id)
                log = {"foundry_calls": [], "llm_calls": []}
                error = f"{type(exc).__name__}: {exc}"
                if not args.keep_going and args.backend == "scripted":
                    print(f"ERROR — {error}")
                    scores.append(score_mod.score_run(
                        scenario, [], [], None, args.backend, error=error))
                    break
            if error:
                print(f"ERROR — {error}")
            else:
                print(f"{len(log['foundry_calls'])} foundry calls, "
                      f"{len(log['llm_calls'])} llm calls, {log['elapsed_s']}s")

            if ask is not None and not error:
                judge_result = await _judge_run(
                    scenario, log.get("foundry_calls", []), ask)
                log["judge_calls"] = judge_result["judge_calls"]

            (out_dir / f"{scenario.id}.events.json").write_text(
                json.dumps(log, indent=2) + "\n", encoding="utf-8")
            if args.freeze and not error:
                scenario.baseline_path.parent.mkdir(parents=True, exist_ok=True)
                scenario.baseline_path.write_text(
                    json.dumps(log, indent=2) + "\n", encoding="utf-8")

            baseline = None if args.freeze else load_baseline(scenario)
            scores.append(score_mod.score_run(
                scenario,
                log.get("foundry_calls", []),
                log.get("llm_calls", []),
                baseline,
                args.backend,
                error=error,
                judge_result=judge_result,
            ))
    finally:
        if ask is not None:
            await ask.close()

    meta = {
        "backend": args.backend,
        "model": None,  # filled below from the first event log
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "frozen": bool(args.freeze),
        "judged": bool(args.judge),
    }
    # Surface the model actually used (first non-empty event log).
    for events_path in sorted(out_dir.glob("*.events.json")):
        try:
            meta["model"] = json.loads(events_path.read_text())["model"]
            break
        except Exception:
            pass

    paths = score_mod.write_reports(scores, meta, out_dir)
    summary = score_mod.corpus_summary(scores)
    print(f"\n[eval] {summary['passed']}/{summary['scenarios']} passed · "
          f"hard-check failures: {summary['hard_check_failures']} · "
          f"contradictions: {summary['contradictions']} "
          f"(rate {summary['contradiction_rate']})")
    if summary["contradictions_by_source"]:
        breakdown = ", ".join(f"{k}: {v}" for k, v in
                              sorted(summary["contradictions_by_source"].items()))
        print(f"[eval] contradiction sources: {breakdown}")
    if summary["judged_turns"] or summary["judge_errors"]:
        print(f"[eval] judge: {summary['judged_turns'] - summary['judge_errors']}"
              f"/{summary['spoken_turns']} turns audited, "
              f"{summary['judge_errors']} errors")
    if summary["mean_drift"] is not None:
        print(f"[eval] mean drift vs baseline: {summary['mean_drift']}")
    print(f"[eval] report: {paths['markdown']}")

    if args.record:
        report = json.loads(paths["json"].read_text(encoding="utf-8"))
        record = metrics_mod.build_record(report, tick_volume=args.tick_volume)
        metrics_mod.append_record(record)
        print(f"[eval] recorded contradiction rate "
              f"{record['contradiction_rate']} → {metrics_mod.HISTORY_PATH}")

    # Gate: scripted runs must be green; live runs fail only on hard checks
    # or contradictions (drift is reported, not gated).
    return 0 if summary["failed"] == 0 else 1


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(name)s %(levelname)s %(message)s")
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    sys.exit(main())
