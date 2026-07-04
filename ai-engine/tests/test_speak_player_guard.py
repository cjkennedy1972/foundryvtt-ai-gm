"""Regression test: execute_speak must refuse to voice a player-owned actor.

Root cause this guards against: the LLM narrating dialogue "as" the party's
own PC registers that name in ChatListener's AI-controlled-speaker set, which
then makes the echo guard treat the player's own real chat messages (posted
under their character's name) as AI echoes and silently drop them — the
player looks "ignored" by the GM.
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from actions.executors import execute_speak, _pc_names_cache, _pc_names_cache_at
import actions.executors as executors_module


def _stub_foundry(actors):
    foundry = MagicMock()
    foundry.get_actors = AsyncMock(return_value=actors)
    foundry.chat_message = AsyncMock(return_value={"success": True})
    return foundry


def _reset_pc_cache():
    executors_module._pc_names_cache = set()
    executors_module._pc_names_cache_at = 0.0


def test_refuses_to_speak_for_player_owned_actor():
    _reset_pc_cache()
    foundry = _stub_foundry([{"name": "Beringar", "has_player_owner": True}])

    result = asyncio.run(execute_speak("Beringar", "Keep your wits about you.", foundry=foundry))

    assert result["success"] is False
    foundry.chat_message.assert_not_awaited()


def test_allows_speaking_for_npc():
    _reset_pc_cache()
    foundry = _stub_foundry([
        {"name": "Beringar", "has_player_owner": True},
        {"name": "Elara the Lost", "has_player_owner": False},
    ])

    result = asyncio.run(execute_speak("Elara the Lost", "Step carefully.", foundry=foundry))

    assert "error" not in result
    foundry.chat_message.assert_awaited_once()
