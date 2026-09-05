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


# ---------------------------------------------------------------------------
# CKP-147: Usage context rebinding guard
# ---------------------------------------------------------------------------

def test_usage_context_re_asserted_after_gm_start_session(monkeypatch):
    """A scripted /gm start session cannot move usage accounting off the run-wide session id.

    The live-eval cap binds to a run-wide session id. Production code paths like
    _cmd_start_session re-point the usage context via set_usage_context(new_id, ...).
    After each script step, run_scenario re-asserts the run-wide session id to keep
    the cap binding intact. This test verifies both the re-pointing happens AND the
    guard restores the binding.
    """
    async def run():
        # Track all set_usage_context calls to verify the guard works.
        context_calls = []

        class _TestLiveLLM:
            """Records usage context changes."""
            def __init__(self):
                self.model = "test-live"

            def set_usage_tracker(self, tracker):
                pass

            def set_usage_context(self, session_id, campaign=""):
                context_calls.append({
                    "session_id": session_id,
                    "campaign": campaign,
                })

            async def close(self):
                pass

            async def generate(self, *args, **kwargs):
                return {"narration": "Test response"}

        monkeypatch.setattr(replay_mod, "_live_llm", lambda scenario: _TestLiveLLM())

        # Monkeypatch MockDatabase.get_active_session to return None for the duration
        # of the scenario, so _cmd_start_session can proceed and re-point the context.
        # This allows the production code path (set_usage_context with new session id)
        # to execute, which the post-step re-assertion then guards against.
        original_get_active_session = MockDatabase.get_active_session

        async def patched_get_active_session(self):
            # Return None to allow /gm start session to proceed.
            return None

        monkeypatch.setattr(
            MockDatabase, "get_active_session", patched_get_active_session
        )

        # Patch build_listener to set up GM user IDs so our test author is recognized.
        # This allows the /gm command in our test to be authorized and executed.
        from evals import harness
        original_build_listener = harness.build_listener

        def patched_build_listener(llm, foundry, db, state, npc_registry=None):
            listener = original_build_listener(llm, foundry, db, state, npc_registry=npc_registry)
            # Accept our test GM user id as a GM-tier user.
            listener._gm_user_ids = {"gm1"}
            return listener

        monkeypatch.setattr(harness, "build_listener", patched_build_listener)

        # Create a scenario with a /gm start session command (from a GM-tier sender).
        scenario = Scenario(
            id="usage_context_guard",
            title="Usage Context Guard",
            tags=[],
            setup={"campaign": "Test Campaign"},
            canon_facts=[],
            script=[
                {
                    "event": "player_message",
                    "speaker": "GM",
                    "message": "/gm start session Another Campaign",
                    "author": {"name": "Gamemaster", "id": "gm1"},
                },
            ],
            scripted_responses=[],
            expect={},
        )

        db = MockDatabase()
        tracker = TokenUsage(db, budget=100_000)

        # Patch step_index increment in run_scenario loop. We need to track the step.
        # Since we can't easily hook into the loop, we'll use the context_calls list
        # to infer: after the /gm start session step, we expect a re-assertion.

        # Run the scenario with a tracked usage context.
        await replay_mod.run_scenario(
            scenario, "live", usage_tracker=tracker, usage_session_id="eval-run-guard"
        )

        # Assertions: verify both halves of the guard
        assert len(context_calls) >= 2, (
            f"Expected at least 2 context calls (initial + re-assertion), "
            f"got {len(context_calls)}: {context_calls}"
        )

        # (a) Verify _cmd_start_session actually re-pointed the context:
        # At least one mid-step call should have a session id ≠ the run-wide id
        # (it's a fresh uuid from _cmd_start_session)
        assert any(c["session_id"] != "eval-run-guard" for c in context_calls), (
            f"Expected _cmd_start_session to re-point context to a new session id, "
            f"but all calls were to run-wide id. Calls: {context_calls}"
        )

        # (b) Verify the re-assertion after the step restored the binding:
        # The final call must be to the run-wide session id
        assert context_calls[-1]["session_id"] == "eval-run-guard", (
            f"Expected final context call to restore run-wide session id, "
            f"but got {context_calls[-1]}"
        )
        assert context_calls[-1]["campaign"] == "Test Campaign"

    asyncio.run(run())
