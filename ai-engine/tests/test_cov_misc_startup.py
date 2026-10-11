"""api.startup.build_chat wiring callbacks and a shutdown with nothing built."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from api import startup


@pytest.mark.asyncio
async def test_build_chat_wires_budget_and_combat_callbacks(monkeypatch):
    reinforcement = MagicMock()
    reinforcement.start = AsyncMock()
    reinforcement.on_combat_start = AsyncMock()
    reinforcement.on_combat_end = AsyncMock()
    listener = MagicMock()
    listener.handle_budget_exhausted = AsyncMock()
    monkeypatch.setattr(startup, "ContextReinforcementManager", lambda **kw: reinforcement)
    monkeypatch.setattr(startup, "CampaignMemory", lambda *a, **kw: "memory")
    monkeypatch.setattr(startup, "ChatListener", lambda **kw: listener)
    import tts.playback as playback
    seen = []
    monkeypatch.setattr(playback, "set_chat_listener", seen.append)

    state = SimpleNamespace(
        llm_manager=MagicMock(), state_tracker=MagicMock(), foundry_client=MagicMock(),
        scene_awareness=MagicMock(), campaign_loader=MagicMock(), db=MagicMock(),
        combat_loop=MagicMock(), action_dispatcher=MagicMock(), npc_registry=MagicMock(),
        personality_engine=MagicMock(), ambient_manager=MagicMock(), effects_manager=MagicMock(),
        vision_manager=MagicMock(), npc_llm_manager=MagicMock(), semantic_rag=MagicMock(),
        token_usage=MagicMock())
    on_results = object()
    await startup.build_chat(state, on_results)

    reinforcement.start.assert_awaited_once()
    assert state.campaign_memory == "memory" and state.chat_listener is listener
    listener.set_results_callback.assert_called_once_with(on_results)
    assert seen == [listener]

    err = RuntimeError("budget")
    await state.token_usage.on_exhausted(err)
    listener.handle_budget_exhausted.assert_awaited_once_with(err)

    start_cb = state.combat_loop.set_combat_start_callback.call_args.args[0]
    end_cb = state.combat_loop.set_combat_end_callback.call_args.args[0]
    await start_cb(["t1"])
    reinforcement.on_combat_start.assert_awaited_once_with(["t1"])
    await end_cb()
    reinforcement.on_combat_end.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_shutdown_skips_components_that_were_never_built():
    await startup.shutdown(SimpleNamespace())            # nothing to close, nothing raised
    closed = []
    only_db = SimpleNamespace(db=SimpleNamespace(close=AsyncMock(side_effect=lambda: closed.append("db"))))
    await startup.shutdown(only_db)
    assert closed == ["db"]
