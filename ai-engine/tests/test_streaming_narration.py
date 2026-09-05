#!/usr/bin/env python3
"""Tests for CKP-92 — streamed narration via ``generate_stream``.

The key behavioral contract: the LLM still emits exactly the same
``{"actions": [...]}`` JSON it always has (so action quality is unchanged),
but a ``narrate``/``speak`` action is delivered to the player the moment its
object completes in the stream — before the rest of the turn has finished
generating. Mechanical actions keep their original referee → dispatcher
pipeline, dispatched once the stream ends.

Run:
    cd ai-engine && python -m pytest tests/test_streaming_narration.py -v
"""

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from evals.harness import (
    MockDatabase,
    MockFoundryClient,
    MockNPCRegistry,
    MockStateTracker,
    ScriptedLLM,
    build_listener,
)

logging = __import__("logging")
logging.basicConfig(level=logging.WARNING)


class GatedStreamLLM(ScriptedLLM):
    """ScriptedLLM whose stream halts after the first complete action object
    until ``_gate`` is set — lets a test prove narration was delivered before
    the turn finished generating."""

    def __init__(self, responses):
        super().__init__(responses)
        self._gate = asyncio.Event()
        self._cutoff = None

    async def generate_stream(self, user_message: str, game_state_summary: str = "",
                              extra_context: str = ""):
        self.calls.append(user_message)
        resp = self._responses[min(self._idx, len(self._responses) - 1)]
        self._idx += 1
        text = json.dumps(resp, ensure_ascii=False)

        # Cut after the first complete action object so the incremental
        # decoder can extract it while the rest of the turn is still "on the
        # wire".
        key = '"actions"'
        start = text.find(key)
        colon = text.find(":", start + len(key))
        j = colon + 1
        while j < len(text) and text[j] in " \t\r\n":
            j += 1
        assert text[j] == "["
        j += 1
        decoder = json.JSONDecoder()
        _, end = decoder.raw_decode(text[j:])
        cutoff = j + end

        yield text[:cutoff]
        await self._gate.wait()
        yield text[cutoff:]


def _listener(llm):
    foundry = MockFoundryClient()
    db = MockDatabase()
    state = MockStateTracker()
    listener = build_listener(llm, foundry, db, state)
    return foundry, db, listener


# ---------------------------------------------------------------------------
# Incremental decoder units
# ---------------------------------------------------------------------------

def test_find_actions_array_start():
    llm = ScriptedLLM([{"actions": []}])
    _, _, listener = _listener(llm)

    text = '{"actions": [{"type": "narrate", "text": "hi"}'
    pos = listener._find_actions_array_start(text)
    assert pos is not None
    # Points just past the '['
    assert text[pos - 1] == "["
    assert text[:pos] == '{"actions": ['

    # Not there yet → None
    assert listener._find_actions_array_start('{"act') is None
    # Finds it even with whitespace around the colon
    assert listener._find_actions_array_start('{"actions"\n :\n [') is not None


def test_decode_next_action_partial_and_complete():
    llm = ScriptedLLM([{"actions": []}])
    _, _, listener = _listener(llm)

    text = '{"actions":[{"type":"narrate","text":"the goblin lunges"'
    pos = listener._find_actions_array_start(text)
    assert pos is not None
    # Incomplete object → None, decoder waits for more tokens
    action, new_pos = listener._decode_next_action(text, pos)
    assert action is None

    # Complete the object (plus the start of a second, incomplete one)
    text += '},{"type":"roll"'
    action, new_pos = listener._decode_next_action(text, pos)
    assert action == {"type": "narrate", "text": "the goblin lunges"}
    assert text[pos:new_pos] == '{"type":"narrate","text":"the goblin lunges"}'
    # Next element is incomplete → None again
    action, _ = listener._decode_next_action(text, new_pos)
    assert action is None

    # Strings containing braces/colons must not confuse the decoder
    tricky = '{"actions":[{"type":"speak","npc_name":"Goblin","text":"a } b: c {"},'
    pos = listener._find_actions_array_start(tricky)
    action, _ = listener._decode_next_action(tricky, pos)
    assert action == {"type": "speak", "npc_name": "Goblin", "text": "a } b: c {"}


# ---------------------------------------------------------------------------
# End-to-end streaming behavior
# ---------------------------------------------------------------------------

def test_narration_delivered_before_turn_completes():
    """The headline CKP-92 behavior: narration reaches the player while the
    model is still generating the rest of the turn."""
    llm = GatedStreamLLM([{
        "actions": [
            {"type": "narrate", "text": "The goblin snarls and charges."},
            {"type": "roll", "formula": "1d20+3", "speaker": "Goblin"},
        ]
    }])
    foundry, db, listener = _listener(llm)
    asyncio.run(_run_narration_before_complete(llm, foundry, db, listener))


async def _run_narration_before_complete(llm, foundry, db, listener):
    await db.create_session("ses001", "Test")
    listener._running = True
    task = asyncio.create_task(listener._process_player_input(
        "I raise my shield.", "Aria", "game state", "ctx"))

    # Wait until the narration chat message appears — while the stream is
    # still gated (the roll has NOT been dispatched yet).
    for _ in range(500):
        chats = foundry.calls_of("chat_message")
        if any("snarls" in c.get("text", "") for c in chats):
            break
        await asyncio.sleep(0.01)
    else:
        raise AssertionError("narration never reached the player")

    # The mechanical action must still be pending (stream not complete).
    assert not foundry.calls_of("roll_dice") and not foundry.calls_of("roll"), (
        "mechanical action dispatched before the stream completed"
    )

    # Release the rest of the turn.
    llm._gate.set()
    actions, results = await asyncio.wait_for(task, timeout=10)

    assert [a["type"] for a in actions] == ["narrate", "roll"]
    roll_calls = foundry.calls_of("roll_dice") or foundry.calls_of("roll")
    assert roll_calls, "roll mechanical action never dispatched"

    metrics = listener._last_stream_metrics
    assert metrics["first_narration_s"] is not None
    assert metrics["tokens"] >= 1


def test_streaming_turn_full_pipeline():
    """A normal scripted turn through the streaming path must dispatch every
    action (narration + mechanical) exactly like the blocking path did."""
    llm = ScriptedLLM([{
        "actions": [
            {"type": "narrate", "text": "The goblin eyes you from the shadows."},
            {"type": "speak", "npc_name": "Goblin", "text": "Who goes there?!"},
            {"type": "roll", "formula": "1d20+3", "speaker": "Goblin"},
        ]
    }])
    foundry, db, listener = _listener(llm)

    async def run():
        await db.create_session("ses002", "Test")
        actions, results = await listener._process_player_input(
            "I step into the room.", "Aria", "game state", "ctx")
        return actions, results

    actions, results = asyncio.run(run())

    types = [a["type"] for a in actions]
    assert types == ["narrate", "speak", "roll"], types

    texts = [c.get("text", "") for c in foundry.calls_of("chat_message")]
    assert any("shadows" in t for t in texts), "narration missing from chat"
    assert any("Who goes there" in t for t in texts), "speak missing from chat"
    assert foundry.calls_of("roll_dice") or foundry.calls_of("roll"), "roll missing"
    assert results, "expected dispatcher results"

    # TTS/echo bookkeeping must have run for the narrated text.
    assert listener._last_stream_metrics["first_narration_s"] is not None


def test_stream_failure_falls_back():
    """A failing stream must not crash the turn — the table gets the neutral
    fallback narration, exactly as the blocking path did."""

    class FailingStreamLLM(ScriptedLLM):
        async def generate_stream(self, user_message, game_state_summary="", extra_context=""):
            raise RuntimeError("relay dropped the connection")
            yield  # pragma: no cover - makes this a true async generator

    llm = FailingStreamLLM([{"actions": []}])
    foundry, db, listener = _listener(llm)

    async def run():
        await db.create_session("ses003", "Test")
        actions, results = await listener._process_player_input(
            "Hello?", "Aria", "game state", "ctx")
        return actions, results

    actions, results = asyncio.run(run())
    assert actions == []
    texts = [c.get("text", "") for c in foundry.calls_of("chat_message")]
    assert any("holding its breath" in t for t in texts), "no fallback narration"


def test_parse_actions_falls_back_to_extract_json():
    """When a model prepends thinking text, the full-parse must still recover
    the action JSON (mirrors LLMManager._extract_json behavior)."""
    llm = ScriptedLLM([{"actions": []}])
    _, _, listener = _listener(llm)

    # No _extract_json on the scripted mock → invalid content must raise.
    try:
        listener._parse_actions("some thinking text not json")
        raised = False
    except ValueError:
        raised = True
    assert raised, "unparseable stream should raise ValueError"

    # Valid JSON parses directly.
    assert listener._parse_actions(
        '{"actions": [{"type": "narrate", "text": "ok"}]}'
    ) == [{"type": "narrate", "text": "ok"}]


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))