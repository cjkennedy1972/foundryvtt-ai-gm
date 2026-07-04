"""Regression test: register_ai_speaker must never register a PC name.

Root cause: execute_speak's own player-character guard (test_speak_player_guard.py)
only blocks the chat message from ChatListener._record_actions — it runs AFTER
_record_actions has already called register_ai_speaker for every "speak" action,
and _place_referenced_combatants registers roll-referenced actor names with no
player check at all. Either path poisons _ai_controlled_speakers with a PC's
name, which then makes the echo guard silently drop that player's real chat.
The fix belongs inside register_ai_speaker itself so every call site is covered.
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

import actions.executors as executors_module
from foundry.chat_listener import ChatListener


def _reset_pc_cache():
    executors_module._pc_names_cache = set()
    executors_module._pc_names_cache_at = 0.0


def _make_listener(actors):
    foundry = MagicMock()
    foundry.get_actors = AsyncMock(return_value=actors)
    return ChatListener(
        foundry=foundry, llm=MagicMock(), dispatcher=MagicMock(),
        state_tracker=MagicMock(), db=MagicMock(),
    )


def test_register_ai_speaker_refuses_player_owned_actor():
    _reset_pc_cache()
    listener = _make_listener([{"name": "Beringar", "has_player_owner": True}])

    asyncio.run(listener.register_ai_speaker("Beringar"))

    assert "Beringar" not in listener._ai_controlled_speakers


def test_register_ai_speaker_allows_npc():
    _reset_pc_cache()
    listener = _make_listener([{"name": "Beringar", "has_player_owner": True}])

    asyncio.run(listener.register_ai_speaker("Elara the Lost"))

    assert "Elara the Lost" in listener._ai_controlled_speakers


def test_record_actions_does_not_poison_pc_name():
    """A 'speak' action naming a PC must not reach _ai_controlled_speakers,
    even though _record_actions runs before the dispatcher's own guard."""
    _reset_pc_cache()
    listener = _make_listener([{"name": "Beringar", "has_player_owner": True}])

    asyncio.run(listener._record_actions([
        {"type": "speak", "npc_name": "Beringar", "text": "Keep your wits about you."}
    ]))

    assert "Beringar" not in listener._ai_controlled_speakers
