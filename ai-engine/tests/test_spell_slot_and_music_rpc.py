#!/usr/bin/env python3
"""cast_spell and play_music sent RPC types the relay rejects outright.

FoundryClient.use_spell_slot sent "use-spell-slot" and play_playlist sent
"play-playlist"; neither is in the relay's PendingRequestTypes, so the relay
answered {"type":"error","error":"Unknown message type"} and _send raised.
Every non-ritual cast_spell and every play_music therefore failed.

The relay does implement playlists, under "playlist-play" with a
`playlistName` parameter and volume as a separate "playlist-volume" call.
It has no spell-slot endpoint at all, so slot consumption goes through
execute-js like the rest of the sheet writes (scripts.spend_legendary_resistance
is the same shape, and scripts.get_spell_slots already reads the other half).

Run:
    cd ai-engine && python -m pytest tests/test_spell_slot_and_music_rpc.py -v
"""

import os
import sys
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from foundry import scripts
from foundry.client import FoundryClient


def _client():
    c = FoundryClient()
    c._send = AsyncMock(return_value={"data": {}})
    c.execute_js = AsyncMock(return_value={"result": {"ok": True, "used": True, "remaining": 2}})
    return c


# ── spell slots ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_spending_a_slot_does_not_use_a_message_type_the_relay_rejects():
    client = _client()

    await client.use_spell_slot("Actor.wizard", 3)

    sent = [c.args[0] for c in client._send.await_args_list if c.args]
    assert "use-spell-slot" not in sent, "the relay answers this with Unknown message type"


@pytest.mark.asyncio
async def test_spending_a_slot_decrements_the_sheet():
    client = _client()

    result = await client.use_spell_slot("Actor.wizard", 3)

    client.execute_js.assert_awaited_once()
    js = client.execute_js.await_args.args[0]
    assert "spell3" in js, "the level must reach the sheet path"
    assert "Actor.wizard" in js
    assert result.get("used") is True


def test_the_spend_script_targets_the_same_sheet_path_the_read_does():
    """get_spell_slots reads system.spells.spell<N>.value; a spend that wrote
    anywhere else would read back unchanged."""
    read = scripts.get_spell_slots("Actor.a")
    spend = scripts.spend_spell_slot("Actor.a", 2)

    assert "'spell' + lvl" in read, "the read no longer walks system.spells.spell<N>"
    # The write target specifically, not just any mention of the path: the
    # script reads system.spells too, so a substring check passes even when
    # actor.update() writes somewhere else entirely.
    update = spend.split("actor.update(", 1)[1]
    assert "'system.spells.'" in update, f"spend writes outside system.spells: {update.splitlines()[0]}"
    assert '"spell2"' in spend


def test_a_pact_slot_is_spent_from_the_pact_pool():
    """Warlocks have no spell1..9 entries; get_spell_slots reports "pact"."""
    js = scripts.spend_spell_slot("Actor.warlock", "pact")

    assert '"pact"' in js
    assert "spellpact" not in js, "a naive spell{level} would write system.spells.spellpact"


def test_spending_reports_when_no_slot_was_available():
    """A cast that silently succeeded on an empty pool would let the model
    cast forever."""
    js = scripts.spend_spell_slot("Actor.a", 1)

    # Specifically the empty-pool guard. A bare "used: false" also matches the
    # missing-actor line above it, which the mutant leaves in place.
    assert "current <= 0" in js, "nothing stops the decrement when the pool is empty"
    guard = js.split("current <= 0", 1)[1].splitlines()[0]
    assert "used: false" in guard, f"the empty-pool branch does not report it: {guard}"


# ── music ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_playing_a_playlist_uses_the_relays_own_message_type():
    client = _client()

    await client.play_playlist("Tavern", volume=0.4)

    sent = [c.args[0] for c in client._send.await_args_list if c.args]
    assert "play-playlist" not in sent, "the relay answers this with Unknown message type"
    assert "playlist-play" in sent


@pytest.mark.asyncio
async def test_playing_a_playlist_names_it_the_way_the_relay_expects():
    """The relay's playlist-play takes playlistName; `name` is ignored."""
    client = _client()

    await client.play_playlist("Tavern")

    play = next(c for c in client._send.await_args_list if c.args and c.args[0] == "playlist-play")
    assert play.kwargs.get("playlistName") == "Tavern"
    assert "name" not in play.kwargs


@pytest.mark.asyncio
async def test_the_requested_volume_is_actually_applied():
    """playlist-play takes no volume, so a play alone leaves the previous
    level — a combat track would come in at the last scene's volume."""
    client = _client()

    await client.play_playlist("Battle", volume=0.9)

    vol = [c for c in client._send.await_args_list if c.args and c.args[0] == "playlist-volume"]
    assert vol, "volume was requested and never sent"
    assert vol[0].kwargs.get("volume") == 0.9
    assert vol[0].kwargs.get("playlistName") == "Battle"
