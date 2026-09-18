"""LLM_TOKEN_BUDGET must bind on the path player turns actually take.

generate_stream was the only LLM entry point that skipped _before_llm_call,
and ChatListener._process_player_input streams every player turn through it.
TokenUsage.before_call is the only thing that fires on_exhausted and raises,
so the cap applied to combat NPC turns (which use generate) and to nothing
else: usage was recorded after each call but never checked before one, and a
session ran arbitrarily far past its budget without degraded mode engaging.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from llm.manager import LLMManager
from llm.usage import TokenBudgetExceeded, TokenUsage


@pytest.fixture
def manager(monkeypatch):
    mgr = LLMManager.__new__(LLMManager)
    mgr.model = "test-model"
    mgr._temperature = 0.7
    mgr._max_tokens = 100
    mgr._history_lock = __import__("asyncio").Lock()
    mgr._usage_session_id = "session-1"
    mgr._usage_campaign = "camp"
    mgr._acquire_rate_limit = AsyncMock()
    mgr._build_prompt_messages = MagicMock(
        return_value=[{"role": "user", "content": "the party opens the door"}]
    )
    return mgr


@pytest.mark.asyncio
async def test_streaming_turn_is_refused_once_the_budget_is_spent(manager):
    db = AsyncMock()
    db.get_llm_usage_total.return_value = 100_000
    announced = []
    usage = TokenUsage(db, budget=100_000, on_exhausted=lambda e: announced.append(e) or _noop())
    manager._usage = usage

    with pytest.raises(TokenBudgetExceeded):
        async for _ in manager.generate_stream("the party opens the door"):
            pass

    assert announced, "on_exhausted must fire so the table enters degraded mode"


@pytest.mark.asyncio
async def test_streaming_turn_proceeds_while_budget_remains(manager, monkeypatch):
    db = AsyncMock()
    db.get_llm_usage_total.return_value = 0
    manager._usage = TokenUsage(db, budget=100_000)

    # Fail after the preflight so we observe the check passing, not the HTTP call.
    monkeypatch.setattr(manager, "_client", None, raising=False)
    with pytest.raises(Exception) as caught:
        async for _ in manager.generate_stream("the party opens the door"):
            pass

    assert not isinstance(caught.value, TokenBudgetExceeded)
    db.get_llm_usage_total.assert_awaited()


async def _noop():
    return None
