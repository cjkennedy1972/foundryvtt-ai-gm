"""A relay failure must not be read as "this name is not a player character".

_is_player_character documents a tri-state: True, False, or None for "the
lookup failed". Its own except branch was unreachable, because get_actors
caught transport errors and returned []. An empty list cached as "no PCs
exist", and all three call sites read None as False. The visible symptom was
permanent: register_ai_speaker would add a real PC name to
_ai_controlled_speakers, which nothing prunes, and _is_player_message then
dropped that player's chat for the rest of the process lifetime.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

import actions.executors as executors
from actions.executors import _is_player_character


@pytest.fixture(autouse=True)
def clear_pc_cache():
    executors._pc_names_cache = None
    executors._pc_names_cache_at = 0.0
    yield
    executors._pc_names_cache = None
    executors._pc_names_cache_at = 0.0


@pytest.fixture
def foundry():
    client = AsyncMock()
    client.is_connected = True
    return client


@pytest.mark.asyncio
async def test_transport_failure_reports_unknown(foundry):
    foundry.get_actors.side_effect = ConnectionError("relay went away")

    assert await _is_player_character("Thalia", foundry) is None


@pytest.mark.asyncio
async def test_failure_is_not_cached_so_the_next_call_retries(foundry):
    foundry.get_actors.side_effect = ConnectionError("relay went away")
    await _is_player_character("Thalia", foundry)

    foundry.get_actors.side_effect = None
    foundry.get_actors.return_value = [
        {"name": "Thalia", "has_player_owner": True, "uuid": "Actor.1"},
    ]

    assert await _is_player_character("Thalia", foundry) is True


@pytest.mark.asyncio
async def test_a_world_with_no_pcs_is_a_definite_no(foundry):
    """Distinct from failure: NPC rolls must still work in a PC-less world."""
    foundry.get_actors.return_value = [
        {"name": "Goblin", "has_player_owner": False, "uuid": "Actor.9"},
    ]

    assert await _is_player_character("Goblin", foundry) is False


@pytest.mark.asyncio
async def test_strict_mode_is_what_surfaces_the_failure(foundry):
    """Without strict=True the error never reaches this function."""
    foundry.get_actors.side_effect = ConnectionError("relay went away")
    await _is_player_character("Thalia", foundry)

    assert foundry.get_actors.await_args.kwargs["strict"] is True


@pytest.mark.asyncio
async def test_unknown_lookup_does_not_mute_a_player():
    """register_ai_speaker must not claim a name it could not resolve."""
    from foundry.chat_listener import ChatListener

    listener = ChatListener.__new__(ChatListener)
    listener.foundry = AsyncMock()
    listener.foundry.get_actors.side_effect = ConnectionError("relay went away")
    listener._ai_controlled_speakers = set()

    await ChatListener.register_ai_speaker(listener, "Thalia")

    assert "Thalia" not in listener._ai_controlled_speakers


class TestAiSpeakerSetLifecycle:
    """_ai_controlled_speakers decides whose chat is treated as the AI's echo.

    It only ever grew. A name in it is a name whose messages get dropped, so
    entries surviving a campaign switch meant NPC names from the last campaign
    silently muting a player in the next one who shared a name.
    """

    @pytest.fixture(autouse=True)
    def _fixed_ai_name(self, monkeypatch):
        """settings.ai_name is operator-configurable and differs between a
        developer .env and CI, so pin it rather than assert on whatever is
        ambient."""
        from config import settings
        monkeypatch.setattr(settings, "ai_name", "Sage")

    def _listener(self):
        from foundry.chat_listener import ChatListener

        listener = ChatListener.__new__(ChatListener)
        listener.foundry = MagicMock()
        listener.foundry._ai_name = "Sage"
        listener._ai_controlled_speakers = {"Sage"}
        return listener

    def test_reset_drops_learned_npc_names(self):
        listener = self._listener()
        listener._ai_controlled_speakers |= {"Borin", "Mara", "The Innkeeper"}

        listener.reset_ai_speakers()

        assert listener._ai_controlled_speakers == {"Sage"}
        assert "Borin" not in listener._ai_controlled_speakers

    def test_reset_keeps_the_ai_speaking_as_itself(self):
        """Dropping the AI's own name would let its narration echo back in."""
        listener = self._listener()

        listener.reset_ai_speakers()

        assert "Sage" in listener._ai_controlled_speakers

    @pytest.mark.asyncio
    async def test_a_name_registered_after_a_reset_is_suppressed_again(self):
        from foundry.chat_listener import ChatListener

        listener = self._listener()
        listener.foundry = AsyncMock()
        listener.foundry._ai_name = "Sage"
        listener.foundry.get_actors = AsyncMock(return_value=[])
        listener.reset_ai_speakers()

        await ChatListener.register_ai_speaker(listener, "Borin")

        assert "Borin" in listener._ai_controlled_speakers
