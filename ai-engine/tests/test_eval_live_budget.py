"""Regression coverage for the live-run token spend gate (CKP-141).

The eval harness may place real LLM calls (--backend live, or --judge under
either backend). Those calls must never run uncapped: the invocation requires
EVAL_LIVE_BUDGET_CONFIRMED=true, and every call is charged against a hard
per-run token cap through the production llm.usage.TokenUsage preflight path.
"""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evals import replay as replay_mod
from evals.harness import MockDatabase
from evals.scenario import Scenario
from llm.usage import TokenBudgetExceeded, TokenUsage, Usage


# ---------------------------------------------------------------------------
# The confirmation gate
# ---------------------------------------------------------------------------

def test_live_backend_refused_without_confirmation(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv(replay_mod.LIVE_CONFIRM_ENV, raising=False)
    rc = replay_mod.main(["--backend", "live", "--out", str(tmp_path)])
    assert rc == 2
    assert replay_mod.LIVE_CONFIRM_ENV in capsys.readouterr().err


def test_judge_refused_without_confirmation(tmp_path, monkeypatch, capsys):
    """--judge places live calls even under the scripted backend."""
    monkeypatch.delenv(replay_mod.LIVE_CONFIRM_ENV, raising=False)
    rc = replay_mod.main(["--backend", "scripted", "--judge",
                          "--out", str(tmp_path)])
    assert rc == 2
    assert replay_mod.LIVE_CONFIRM_ENV in capsys.readouterr().err


def test_scripted_backend_needs_no_confirmation(monkeypatch):
    monkeypatch.delenv(replay_mod.LIVE_CONFIRM_ENV, raising=False)
    args = SimpleNamespace(backend="scripted", judge=False)
    assert replay_mod._live_gate_error(args) is None


def test_confirmation_admits_live_run(monkeypatch):
    monkeypatch.setenv(replay_mod.LIVE_CONFIRM_ENV, "true")
    args = SimpleNamespace(backend="live", judge=False)
    assert replay_mod._live_gate_error(args) is None


def test_invalid_budget_env_refused(monkeypatch):
    monkeypatch.setenv(replay_mod.LIVE_CONFIRM_ENV, "true")
    monkeypatch.setenv(replay_mod.LIVE_BUDGET_ENV, "lots")
    args = SimpleNamespace(backend="live", judge=False)
    assert replay_mod.LIVE_BUDGET_ENV in replay_mod._live_gate_error(args)


def test_budget_defaults_and_override(monkeypatch):
    monkeypatch.delenv(replay_mod.LIVE_BUDGET_ENV, raising=False)
    assert replay_mod._live_budget() == replay_mod.DEFAULT_LIVE_TOKEN_BUDGET
    monkeypatch.setenv(replay_mod.LIVE_BUDGET_ENV, "5000")
    assert replay_mod._live_budget() == 5000


def test_negative_budget_env_refused(monkeypatch):
    """A stray minus sign must fail loudly, never clamp to unlimited.

    Regression: ``max(0, int(raw))`` turned ``-100000`` into 0, which
    TokenUsage reads as *no cap* — a typo silently disabled the gate.
    """
    monkeypatch.setenv(replay_mod.LIVE_BUDGET_ENV, "-100000")
    with pytest.raises(ValueError, match=replay_mod.LIVE_BUDGET_ENV):
        replay_mod._live_budget()
    # The gate surfaces the rejection instead of admitting the run.
    monkeypatch.setenv(replay_mod.LIVE_CONFIRM_ENV, "true")
    args = SimpleNamespace(backend="live", judge=False)
    assert replay_mod.LIVE_BUDGET_ENV in replay_mod._live_gate_error(args)


def test_zero_budget_env_is_explicit_opt_out(monkeypatch):
    """The documented literal 0 remains the explicit cap disable."""
    monkeypatch.setenv(replay_mod.LIVE_BUDGET_ENV, "0")
    assert replay_mod._live_budget() == 0
    monkeypatch.setenv(replay_mod.LIVE_CONFIRM_ENV, "true")
    args = SimpleNamespace(backend="live", judge=False)
    assert replay_mod._live_gate_error(args) is None


# ---------------------------------------------------------------------------
# The cap itself: MockDatabase accounting + TokenUsage preflight
# ---------------------------------------------------------------------------

def test_mock_database_usage_accounting():
    async def run():
        db = MockDatabase()
        assert await db.get_llm_usage_total(session_id="s1") == 0
        await db.record_llm_usage("s1", "c", 100, 50, "m")
        await db.record_llm_usage("s1", "c", 25, 25, "m", call_type="judge")
        await db.record_llm_usage("s2", "c", 999, 999, "m")
        assert await db.get_llm_usage_total(session_id="s1") == 200
        assert await db.get_llm_usage_total(session_id="s2") == 1998

    asyncio.run(run())


def test_token_usage_cap_blocks_calls():
    """The production preflight path, backed by the harness store, hard-stops."""
    async def run():
        db = MockDatabase()
        tracker = TokenUsage(db, budget=100)
        await tracker.before_call("s1", "c", 60)
        await tracker.record("s1", "c", Usage(50, 10), "m")
        with pytest.raises(TokenBudgetExceeded):
            await tracker.before_call("s1", "c", 50)  # 60 used + 50 > 100

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Wiring: the live backend is actually attached to the cap
# ---------------------------------------------------------------------------

class _FakeLiveLLM:
    """Stands in for RecordingLLM(LLMManager) — records the tracker wiring."""

    def __init__(self):
        self.tracker = None
        self.usage_context = None
        self.model = "fake-live"

    def set_usage_tracker(self, tracker):
        self.tracker = tracker

    def set_usage_context(self, session_id, campaign=""):
        self.usage_context = (session_id, campaign)

    async def close(self):
        pass


def _minimal_scenario() -> Scenario:
    return Scenario(id="budget_probe", title="t", tags=[], setup={},
                    canon_facts=[], script=[], scripted_responses=[],
                    expect={})


def test_live_run_attaches_shared_tracker(monkeypatch):
    fake = _FakeLiveLLM()
    monkeypatch.setattr(replay_mod, "_live_llm", lambda scenario: fake)
    tracker = TokenUsage(MockDatabase(), budget=1234)

    asyncio.run(replay_mod.run_scenario(
        _minimal_scenario(), "live", tracker, "eval-run-test"))

    assert fake.tracker is tracker
    assert fake.usage_context == ("eval-run-test", "Eval Campaign")


def test_scripted_run_never_touches_tracker(monkeypatch):
    """The CI-safe backend must not grow a dependency on the usage path."""
    scenario = _minimal_scenario()
    scenario.scripted_responses = []
    called = []
    monkeypatch.setattr(replay_mod, "_scripted_llm",
                        lambda s: called.append(s) or _FakeLiveLLM())

    asyncio.run(replay_mod.run_scenario(scenario, "scripted", None, None))

    assert called == [scenario]


# ---------------------------------------------------------------------------
# The judge's direct calls are metered too
# ---------------------------------------------------------------------------

def test_metered_ask_charges_and_stops_at_cap():
    async def run():
        tracker = TokenUsage(MockDatabase(), budget=10)
        replies = []

        async def ask(system, user):
            replies.append((system, user))
            return "verdict"
        ask.close = None
        ask.model = "judge-model"

        metered = replay_mod._metered_ask(ask, tracker, "eval-run-test")
        # A short exchange fits; the estimate is prompt + 256 completion.
        with pytest.raises(TokenBudgetExceeded):
            await metered("system prompt", "user turn")
        assert replies == []  # refused before any live call was placed

    asyncio.run(run())


def test_metered_ask_records_successful_call():
    async def run():
        db = MockDatabase()
        tracker = TokenUsage(db, budget=100_000)

        async def ask(system, user):
            return "verdict"
        ask.close = None
        ask.model = "judge-model"

        metered = replay_mod._metered_ask(ask, tracker, "eval-run-test")
        assert await metered("sys", "usr") == "verdict"
        assert await db.get_llm_usage_total(session_id="eval-run-test") > 0

    asyncio.run(run())
