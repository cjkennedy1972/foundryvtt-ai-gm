#!/usr/bin/env python3
"""
Tests for the upstream triggers behind the retry/re-narration loop:

  * update_hp against a hallucinated/stale actor uuid now resolves the actor
    against the live actor list (by uuid, name, or trailing id) and retries,
    instead of failing and kicking off a re-narrating retry turn.
  * prompt_player whispered to a non-existent user (the LLM passes display
    names like "Player1") now falls back to a public GM message.

Run:
    cd ai-engine && python -m pytest tests/test_actor_resolution.py -v
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from actions.executors import execute_update_hp, execute_prompt_player

VALID = "Actor.IMmMlM4zG7QSuMQ7"
NAME = "Beringar"


class MockFoundry:
    def __init__(self):
        self.attr_calls = []
        self.chat_calls = []

    async def get_actors(self, world_only=False):
        return [{"uuid": VALID, "name": NAME, "hp": 10, "max_hp": 20}]

    async def _attr(self, kind, amount, uuid):
        self.attr_calls.append((kind, uuid, amount))
        if uuid == VALID:
            return {"success": True, "uuid": uuid}
        return {"success": False, "error": "actor not found"}

    async def decrease_attribute(self, path, amount, uuid):
        return await self._attr("decrease", amount, uuid)

    async def increase_attribute(self, path, amount, uuid):
        return await self._attr("increase", amount, uuid)

    async def chat_message(self, text, speaker="", whisper=None):
        self.chat_calls.append({"text": text, "speaker": speaker, "whisper": whisper})
        if whisper:  # whisper target invalid → relay reports failure
            return {"success": False, "error": "Failed to create chat message"}
        return {"success": True}


def test_update_hp_valid_uuid_single_call():
    async def run():
        f = MockFoundry()
        res = await execute_update_hp(VALID, 5, foundry=f)
        assert res.get("success") is not False
        assert res["actor_uuid"] == VALID
        assert f.attr_calls == [("decrease", VALID, 5)], "should not re-resolve a valid uuid"
    asyncio.run(run())


def test_update_hp_resolves_by_name():
    async def run():
        f = MockFoundry()
        # LLM passed the display name instead of a uuid.
        res = await execute_update_hp(NAME, 8, foundry=f)
        assert res.get("success") is not False
        assert res["actor_uuid"] == VALID, "should retry against the resolved uuid"
        assert f.attr_calls[-1] == ("decrease", VALID, 8)
    asyncio.run(run())


def test_update_hp_hallucinated_uuid_clear_error():
    async def run():
        f = MockFoundry()
        res = await execute_update_hp("Actor.9x8Y7v6u5t4s3r2q", 5, foundry=f)
        assert res.get("success") is False
        assert "uuid" in res.get("error", "").lower()
    asyncio.run(run())


def test_update_hp_healing_resolves():
    async def run():
        f = MockFoundry()
        res = await execute_update_hp(NAME, -6, foundry=f)
        assert res.get("success") is not False
        assert res["actor_uuid"] == VALID
        assert f.attr_calls[-1] == ("increase", VALID, 6)
    asyncio.run(run())


class TransientFoundry(MockFoundry):
    """A valid actor whose HP write fails transiently (relay glitch), not because
    the identifier is wrong."""

    async def _attr(self, kind, amount, uuid):
        self.attr_calls.append((kind, uuid, amount))
        return {"success": False, "error": "relay timeout"}


def test_update_hp_transient_failure_on_valid_uuid_not_retried():
    async def run():
        f = TransientFoundry()
        res = await execute_update_hp(VALID, 5, foundry=f)
        # The uuid is valid, so the error must not blame a hallucinated id...
        assert res.get("success") is False
        assert "no actor matches" not in res.get("error", "").lower()
        assert "transient" in res.get("error", "").lower()
        # ...and a non-idempotent HP change must not be re-applied.
        assert len(f.attr_calls) == 1, "valid-uuid transient failure must not double-apply"
    asyncio.run(run())


def test_prompt_player_falls_back_to_public():
    async def run():
        f = MockFoundry()
        res = await execute_prompt_player("Player1", "Climb the catwalk?", foundry=f)
        assert res.get("success") is not False
        assert len(f.chat_calls) == 2, "should attempt whisper then public fallback"
        first, second = f.chat_calls
        assert first["whisper"] == ["Player1"]
        assert second["speaker"] == "GM" and not second["whisper"]
        assert "Player1" in second["text"] and "Climb the catwalk?" in second["text"]
    asyncio.run(run())


if __name__ == "__main__":
    test_update_hp_valid_uuid_single_call()
    test_update_hp_resolves_by_name()
    test_update_hp_hallucinated_uuid_clear_error()
    test_update_hp_healing_resolves()
    test_update_hp_transient_failure_on_valid_uuid_not_retried()
    test_prompt_player_falls_back_to_public()
    print("All actor-resolution tests passed.")
