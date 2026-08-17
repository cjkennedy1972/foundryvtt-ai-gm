"""Tests that ChatListener's optional npc_llm param actually reaches
ModelRouter — the plumbing main.py uses to give NPC turns a distinct,
cheaper model once one is configured (settings.npc_agent_model)."""

from unittest.mock import MagicMock

from foundry.chat_listener import ChatListener


def _make_listener(**overrides):
    kwargs = dict(
        foundry=MagicMock(),
        llm=MagicMock(name="frontier-llm"),
        dispatcher=MagicMock(),
        state_tracker=MagicMock(),
        db=MagicMock(),
    )
    kwargs.update(overrides)
    return ChatListener(**kwargs)


def test_no_npc_llm_routes_npc_tier_to_frontier():
    frontier = MagicMock(name="frontier-llm")
    listener = _make_listener(llm=frontier)
    assert listener._model_router.get("npc") is frontier
    assert listener._model_router.get("frontier") is frontier


def test_npc_llm_routes_npc_tier_to_distinct_model():
    frontier = MagicMock(name="frontier-llm")
    npc_llm = MagicMock(name="npc-llm")
    listener = _make_listener(llm=frontier, npc_llm=npc_llm)
    assert listener._model_router.get("npc") is npc_llm
    assert listener._model_router.get("frontier") is frontier
