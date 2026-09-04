#!/usr/bin/env python3
"""
Death saving throws: PC-defer at the executor level, and the combat loop's
per-turn trigger for a creature at 0 HP.

Run:
    cd ai-engine && python -m pytest tests/test_death_save.py -v
"""

import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import actions.executors as ex
from combat.loop import CombatLoop


def _foundry():
    f = AsyncMock()
    f.get_actors = AsyncMock(return_value=[
        {"name": "Beringar", "uuid": "Actor.IMmMlM4zG7QSuMQ7", "has_player_owner": True},
        {"name": "Goblin", "uuid": "Actor.gobxyz", "has_player_owner": False},
    ])
    f.request_death_save = AsyncMock(return_value={"total": 12, "success": True})
    f.chat_message = AsyncMock(return_value={"ok": True})
    return f


def setup_function(_):
    ex._pc_uuid_cache = {}
    ex._pc_uuid_cache_at = 0.0


# ---------------------------------------------------------------------------
# execute_death_save — PC-defer pattern
# ---------------------------------------------------------------------------

def test_pc_death_save_is_deferred_to_player():
    f = _foundry()
    out = asyncio.run(ex.execute_death_save(actor_uuid="Actor.IMmMlM4zG7QSuMQ7", foundry=f))
    assert out.get("deferred_to_player") is True
    f.request_death_save.assert_not_called()
    f.chat_message.assert_awaited_once()
    prompt = f.chat_message.await_args.args[0]
    assert "Beringar" in prompt and "death saving throw" in prompt


def test_npc_death_save_still_auto_rolls():
    f = _foundry()
    out = asyncio.run(ex.execute_death_save(actor_uuid="Actor.gobxyz", foundry=f))
    assert out.get("deferred_to_player") is None
    f.request_death_save.assert_awaited_once()
    f.chat_message.assert_not_awaited()


# ---------------------------------------------------------------------------
# CombatLoop._maybe_death_save — per-turn trigger
# ---------------------------------------------------------------------------

def _loop(foundry):
    return CombatLoop(
        foundry=foundry, llm=MagicMock(), dispatcher=MagicMock(),
        state_tracker=MagicMock(), db=MagicMock(),
    )


def _token(actor_uuid="Actor.gobxyz", name="Goblin"):
    return {"id": "tok1", "name": name, "actorUuid": actor_uuid}


def test_full_hp_creature_does_not_trigger_death_save():
    f = _foundry()
    f.execute_js = AsyncMock(return_value={"result": {"hp": 10, "isDead": False, "isStable": False,
                                                        "successes": 0, "failures": 0}})
    loop = _loop(f)
    skipped = asyncio.run(loop._maybe_death_save(_token()))
    assert skipped is False
    f.request_death_save.assert_not_called()


def test_zero_hp_npc_triggers_auto_rolled_death_save():
    f = _foundry()
    f.execute_js = AsyncMock(return_value={"result": {"hp": 0, "isDead": False, "isStable": False,
                                                        "successes": 1, "failures": 0}})
    loop = _loop(f)
    skipped = asyncio.run(loop._maybe_death_save(_token()))
    assert skipped is True
    f.request_death_save.assert_awaited_once()


def test_zero_hp_pc_defers_death_save_to_player():
    f = _foundry()
    f.execute_js = AsyncMock(return_value={"result": {"hp": 0, "isDead": False, "isStable": False,
                                                        "successes": 0, "failures": 1}})
    loop = _loop(f)
    skipped = asyncio.run(loop._maybe_death_save(_token(actor_uuid="Actor.IMmMlM4zG7QSuMQ7", name="Beringar")))
    assert skipped is True
    f.request_death_save.assert_not_called()
    f.chat_message.assert_awaited_once()


def test_dead_creature_skips_turn_without_rolling_again():
    f = _foundry()
    f.execute_js = AsyncMock(return_value={"result": {"hp": 0, "isDead": True, "isStable": False,
                                                        "successes": 0, "failures": 3}})
    loop = _loop(f)
    skipped = asyncio.run(loop._maybe_death_save(_token()))
    assert skipped is True
    f.request_death_save.assert_not_called()
    f.chat_message.assert_not_awaited()


def test_stable_creature_skips_turn_without_rolling_again():
    f = _foundry()
    f.execute_js = AsyncMock(return_value={"result": {"hp": 0, "isDead": False, "isStable": True,
                                                        "successes": 3, "failures": 0}})
    loop = _loop(f)
    skipped = asyncio.run(loop._maybe_death_save(_token()))
    assert skipped is True
    f.request_death_save.assert_not_called()
    f.chat_message.assert_not_awaited()


def test_solo_dead_pc_gets_costly_setback_and_event():
    f = _foundry()
    f.execute_js = AsyncMock(return_value={"result": {"hp": 0, "isDead": True, "isStable": False,
                                                        "successes": 0, "failures": 3}})
    f.apply_solo_death_setback = AsyncMock(return_value={"hp": 1, "exhaustion": True})
    db = MagicMock()
    db.get_active_session = AsyncMock(return_value="session-1")
    db.record_typed_event = AsyncMock()
    loop = CombatLoop(foundry=f, llm=MagicMock(), dispatcher=MagicMock(),
                      state_tracker=MagicMock(), db=db)
    loop._pc_tokens = [_token(actor_uuid="Actor.IMmMlM4zG7QSuMQ7", name="Beringar")]

    assert asyncio.run(loop._maybe_death_save(loop._pc_tokens[0])) is True
    f.apply_solo_death_setback.assert_awaited_once_with("Actor.IMmMlM4zG7QSuMQ7")
    db.record_typed_event.assert_awaited_once()
    event = db.record_typed_event.await_args
    assert event.args[1] == "solo_death_setback"
    assert event.kwargs["payload"]["consequence"] == "captured"
    prompt = f.chat_message.await_args.args[0]
    assert "death saving throw" not in prompt
    assert "awakens" in prompt


def test_solo_setback_can_be_disabled(monkeypatch):
    f = _foundry()
    f.execute_js = AsyncMock(return_value={"result": {"hp": 0, "isDead": True, "isStable": False,
                                                        "successes": 0, "failures": 3}})
    f.apply_solo_death_setback = AsyncMock()
    loop = _loop(f)
    loop._pc_tokens = [_token(actor_uuid="Actor.IMmMlM4zG7QSuMQ7", name="Beringar")]
    monkeypatch.setattr("combat.loop.settings.solo_death_setback", False)

    assert asyncio.run(loop._maybe_death_save(loop._pc_tokens[0])) is True
    f.apply_solo_death_setback.assert_not_awaited()


if __name__ == "__main__":
    for fn in [
        test_pc_death_save_is_deferred_to_player,
        test_npc_death_save_still_auto_rolls,
        test_full_hp_creature_does_not_trigger_death_save,
        test_zero_hp_npc_triggers_auto_rolled_death_save,
        test_zero_hp_pc_defers_death_save_to_player,
        test_dead_creature_skips_turn_without_rolling_again,
        test_stable_creature_skips_turn_without_rolling_again,
    ]:
        setup_function(None)
        fn()
        print(f"PASS  {fn.__name__}")
    print("\nAll death-save tests passed!")
