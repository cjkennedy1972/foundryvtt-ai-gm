"""Regression test: whisper/prompt_player must not trust a hallucinated player_id.

Root cause this guards against: Foundry accepts any whisper target with no
validation, so when the LLM passes an actor ID instead of a real Foundry user
ID (because the player_actors mapping went stale after a campaign restart),
the relay reports success but the message renders for no one. When a known
mapping is available, an unrecognized player_id should go straight to a
public GM message instead of a doomed whisper.
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from actions.executors import execute_whisper, execute_prompt_player

REAL_USER_ID = "user_abc123"
HALLUCINATED_ACTOR_ID = "IMmMlM4zG7QSuMQ7"


def _stub_foundry():
    foundry = MagicMock()
    foundry.chat_message = AsyncMock(return_value={"success": True})
    return foundry


def _stub_app_state(mapping):
    app_state = MagicMock()
    app_state.state_tracker.state.player_actors = mapping
    return app_state


def test_whisper_skips_doomed_send_for_unrecognized_player_id():
    foundry = _stub_foundry()
    app_state = _stub_app_state({"Beringar": REAL_USER_ID})

    result = asyncio.run(execute_whisper(
        HALLUCINATED_ACTOR_ID, "Secret info.", foundry=foundry, app_state=app_state
    ))

    foundry.chat_message.assert_awaited_once()
    _, kwargs = foundry.chat_message.await_args
    assert not kwargs.get("whisper")
    assert kwargs.get("speaker") == "GM"
    assert HALLUCINATED_ACTOR_ID in foundry.chat_message.await_args.args[0]
    assert result["result"]["success"] is True


def test_whisper_sends_normally_for_known_player_id():
    foundry = _stub_foundry()
    app_state = _stub_app_state({"Beringar": REAL_USER_ID})

    asyncio.run(execute_whisper(
        REAL_USER_ID, "Secret info.", foundry=foundry, app_state=app_state
    ))

    foundry.chat_message.assert_awaited_once()
    _, kwargs = foundry.chat_message.await_args
    assert kwargs.get("whisper") == [REAL_USER_ID]


def test_prompt_player_skips_doomed_send_for_unrecognized_player_id():
    foundry = _stub_foundry()
    app_state = _stub_app_state({"Beringar": REAL_USER_ID})

    asyncio.run(execute_prompt_player(
        HALLUCINATED_ACTOR_ID, "Roll a check.", foundry=foundry, app_state=app_state
    ))

    foundry.chat_message.assert_awaited_once()
    _, kwargs = foundry.chat_message.await_args
    assert not kwargs.get("whisper")
    assert kwargs.get("speaker") == "GM"


def test_prompt_player_sends_normally_for_known_player_id():
    foundry = _stub_foundry()
    app_state = _stub_app_state({"Beringar": REAL_USER_ID})

    asyncio.run(execute_prompt_player(
        REAL_USER_ID, "Roll a check.", foundry=foundry, app_state=app_state
    ))

    foundry.chat_message.assert_awaited_once()
    _, kwargs = foundry.chat_message.await_args
    assert kwargs.get("whisper") == [REAL_USER_ID]
