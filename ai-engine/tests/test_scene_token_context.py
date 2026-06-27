#!/usr/bin/env python3
"""
Regression test: live token state must be injected into the per-turn context.

Without token_id + pixel coordinates in context, the LLM cannot call move_token
or precisely target tokens, so the map and tokens end up purely decorative
(observed in live play: 1 place_token at session start, 0 move_token all session).

_get_npc_context must surface each on-scene token's id, disposition and position
plus the move_token/place_token guidance.

Run:
    cd ai-engine && python -m pytest tests/test_scene_token_context.py -v
"""

import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from foundry.chat_listener import ChatListener


def _make_listener(scene_tokens):
    foundry = MagicMock()
    foundry._ai_name = None
    foundry.get_actors = AsyncMock(return_value=[])
    foundry.get_scene_tokens = AsyncMock(return_value=scene_tokens)
    return ChatListener(
        foundry=foundry,
        llm=MagicMock(),
        dispatcher=MagicMock(),
        state_tracker=MagicMock(),
        db=MagicMock(),
    )


def test_tokens_injected_with_id_and_position():
    listener = _make_listener([
        {"id": "QWLZSgTLSgbtrGu7", "name": "Beringar", "disposition": 1, "x": 300, "y": 300},
        {"id": "DK99", "name": "Death Knight", "disposition": -1, "x": 700, "y": 400},
    ])
    # No campaign loader / encounter context to keep the output focused.
    listener._campaign_loader = None
    listener.state_tracker.get_encounter_context = MagicMock(return_value="")
    ctx = asyncio.run(listener._get_npc_context())

    assert "TOKENS ON THE CURRENT MAP" in ctx
    assert "token_id: QWLZSgTLSgbtrGu7" in ctx and "(300, 300)" in ctx
    assert "token_id: DK99" in ctx and "hostile" in ctx
    assert "move_token" in ctx and "place_token" in ctx


def test_no_tokens_no_block():
    listener = _make_listener([])
    listener._campaign_loader = None
    listener.state_tracker.get_encounter_context = MagicMock(return_value="")
    ctx = asyncio.run(listener._get_npc_context())
    assert "TOKENS ON THE CURRENT MAP" not in ctx


if __name__ == "__main__":
    test_tokens_injected_with_id_and_position()
    print("PASS  live tokens injected with id + position + guidance")
    test_no_tokens_no_block()
    print("PASS  no token block when scene is empty")
    print("All scene-token-context tests passed.")
