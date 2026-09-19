#!/usr/bin/env python3
"""Two ways the prompt grew past what the budget thought it was managing.

_trim_history sized the conversation history against max_context minus the
system prompt and the reserved output, and ignored the two blocks
_build_prompt_messages sends on every single turn beside them: CURRENT GAME
STATE and ADDITIONAL CONTEXT. Filling history to that budget and then adding
those put a light turn 238 tokens over a 50,000 cap, and ADDITIONAL CONTEXT
alone measures 1,472 tokens on a busy scene.

CombatLoop is handed state.llm_manager, the same instance the chat listener
narrates with, and generate() appends to history unconditionally. A
five-round fight with six NPCs is thirty calls, so sixty messages of
"Goblin's turn. Decide their action." plus an actions JSON each: roughly
10,500 tokens, a quarter of the history budget, spent evicting the story to
hold turn minutiae.

Run:
    cd ai-engine && python -m pytest tests/test_context_budget_and_combat_history.py -v
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from llm.manager import LLMManager
from utils.token_counter import estimate_message_tokens, estimate_tokens


def _manager(max_context=20000):
    manager = LLMManager()
    manager._max_history_tokens = max_context
    manager._max_tokens = 1000
    manager._custom_system_prompt = "SYSTEM. " * 100      # ~200 tokens
    manager._system_prompt_cache = manager._custom_system_prompt
    manager._reinforcer = None
    return manager


def _fill_history(manager, pairs=400):
    manager._conversation_history = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": "word " * 200}
        for i in range(pairs)
    ]


# ── 1. the budget has to cover everything the turn sends ──────────────────

def test_the_assembled_prompt_stays_within_the_context_cap():
    manager = _manager()
    _fill_history(manager)
    game_state = "state " * 300      # ~300 tokens
    extra = "context " * 1200        # ~1,200 tokens

    messages = manager._build_prompt_messages(
        user_message="What do I see?",
        game_state_summary=game_state,
        extra_context=extra,
    )

    total = estimate_message_tokens(messages) + manager._max_tokens
    assert total <= manager._max_history_tokens, (
        f"assembled {total:,} against a {manager._max_history_tokens:,} cap"
    )


def test_a_bigger_per_turn_context_leaves_room_by_trimming_history():
    """The blocks are not optional, so history is what has to give."""
    manager = _manager()
    _fill_history(manager)
    small = manager._build_prompt_messages("hi", extra_context="x")
    _fill_history(manager)
    large = manager._build_prompt_messages("hi", extra_context="context " * 2000)

    assert estimate_message_tokens(large) <= estimate_message_tokens(small) + 200


def test_history_is_still_used_when_the_turn_context_is_small():
    """Over-reserving would throw away context that fits."""
    manager = _manager()
    _fill_history(manager)

    messages = manager._build_prompt_messages("hi", extra_context="")

    assert sum(1 for m in messages if m["role"] == "assistant") > 5


# ── 2. combat turns stay out of the narrative history ─────────────────────

@pytest.mark.asyncio
async def test_a_turn_that_does_not_persist_leaves_history_alone(monkeypatch):
    manager = _manager()
    manager._conversation_history = [{"role": "user", "content": "The party enters the crypt."}]
    _stub_http(manager, monkeypatch)

    await manager.generate("Goblin's turn.", persist_history=False)

    assert len(manager._conversation_history) == 1, "combat turn was written to the story"


@pytest.mark.asyncio
async def test_an_ordinary_turn_still_persists(monkeypatch):
    manager = _manager()
    _stub_http(manager, monkeypatch)

    await manager.generate("I search the altar.")

    assert len(manager._conversation_history) == 2


@pytest.mark.asyncio
async def test_a_turn_can_skip_reading_history_too(monkeypatch):
    """A combat turn carries its whole world in extra_context; replaying the
    narrative at it costs tokens and latency for nothing."""
    manager = _manager()
    _fill_history(manager, pairs=40)
    sent = _stub_http(manager, monkeypatch)

    await manager.generate("Goblin's turn.", include_history=False, persist_history=False)

    roles = [m["role"] for m in sent["messages"]]
    assert roles.count("assistant") == 0


def _combat_loop():
    """A CombatLoop whose collaborators record what they were asked to do."""
    from unittest.mock import AsyncMock, MagicMock

    from combat.loop import CombatLoop

    llm = MagicMock()
    llm.generate = AsyncMock(return_value={"actions": []})
    llm.remember_combat = AsyncMock()

    foundry = AsyncMock()
    foundry.get_scene_tokens = AsyncMock(return_value=[])
    foundry.execute_js = AsyncMock(return_value={"result": None})
    foundry.get_scene_details = AsyncMock(return_value={})

    tracker = MagicMock()
    tracker.get_snapshot = MagicMock(return_value="")
    tracker.update_combat = AsyncMock()
    tracker.set_mode = AsyncMock()
    tracker.save = AsyncMock()

    loop = CombatLoop(
        foundry=foundry, llm=llm, dispatcher=AsyncMock(),
        state_tracker=tracker, db=MagicMock(),
    )
    return loop, llm


@pytest.mark.asyncio
async def test_combat_asks_the_model_without_touching_the_shared_history():
    """Checked on the call, not on the source: a substring check passes
    against the `hasattr(self.llm, "remember_combat")` guard beside it."""
    loop, llm = _combat_loop()

    await loop._process_npc_turn({"id": "tok1", "name": "Goblin", "actorUuid": "Actor.g"})

    assert llm.generate.await_args is not None, "the NPC turn never reached the model"
    kwargs = llm.generate.await_args.kwargs
    assert kwargs.get("persist_history") is False, "the turn was written to the story"
    assert kwargs.get("include_history") is False, "the story was replayed at a goblin"


@pytest.mark.asyncio
async def test_combat_leaves_one_record_of_the_fight_behind():
    """Dropping the turns must not mean the GM forgets there was a battle."""
    loop, llm = _combat_loop()
    loop._round_log.append("R1: Goblin — attack_with_item for 5 damage")

    await loop._end_combat()

    llm.remember_combat.assert_awaited_once()
    assert "Goblin" in llm.remember_combat.await_args.args[0]


@pytest.mark.asyncio
async def test_a_fight_with_nothing_in_it_records_nothing():
    loop, llm = _combat_loop()

    await loop._end_combat()

    llm.remember_combat.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_round_log_is_bounded():
    """It goes into every turn prompt, so it cannot grow with the fight."""
    from combat.loop import CombatLoop

    loop, _ = _combat_loop()
    for i in range(200):
        loop._round_log.append(f"line {i}")

    assert len(loop._round_log) == CombatLoop.ROUND_LOG_LINES


@pytest.mark.asyncio
async def test_the_next_combatant_can_see_what_the_last_one_did():
    """Without the shared history, this is the only continuity in a fight."""
    loop, llm = _combat_loop()
    loop._log_round_actions(
        "Goblin", [{"type": "attack_with_item", "target_token_id": "tok9"}],
        [{"success": True, "damage": 5}],
    )

    rendered = loop._build_round_log()

    assert "Goblin" in rendered and "attack_with_item" in rendered


@pytest.mark.asyncio
async def test_a_turn_that_acts_is_recorded_for_the_next_one():
    """The log has to be filled by the turn itself, not just renderable."""
    from unittest.mock import AsyncMock

    loop, llm = _combat_loop()
    llm.generate = AsyncMock(return_value={
        "actions": [{"type": "attack_with_item", "target_token_id": "tok9"}]
    })
    loop.dispatcher.execute_batch = AsyncMock(return_value=[{"success": True, "damage": 5}])

    await loop._process_npc_turn({"id": "tok1", "name": "Goblin", "actorUuid": "Actor.g"})

    assert loop._round_log, "the turn left no trace for the next combatant"
    assert "Goblin" in loop._round_log[0]


@pytest.mark.asyncio
async def test_the_fight_so_far_is_in_the_turn_prompt():
    """Rendering it is not enough — with no shared history, this block is the
    only thing telling the next NPC what just happened."""
    loop, llm = _combat_loop()
    loop._log_round_actions(
        "Goblin", [{"type": "attack_with_item", "target_token_id": "tok9"}],
        [{"success": True, "damage": 5}],
    )

    await loop._process_npc_turn({"id": "tok2", "name": "Orc", "actorUuid": "Actor.o"})

    context = llm.generate.await_args.kwargs.get("extra_context", "")
    assert "THIS FIGHT SO FAR" in context
    assert "Goblin" in context


def _stub_http(manager, monkeypatch):
    """Capture the payload and return a minimal valid action response."""
    captured = {}

    class _Response:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": '{"actions": []}'}}]}

    async def _post(url, json=None, timeout=None):
        captured.update(json or {})
        return _Response()

    monkeypatch.setattr(manager._http, "post", _post)
    return captured
