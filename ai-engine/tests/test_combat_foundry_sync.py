"""Checks that CombatLoop actually drives a real Foundry Combat document.

Before this, the loop's turn order/round/turn state lived only in engine
memory — Foundry's own combat tracker (and anything skinning it, like
Carousel Combat Tracker or Combat Booster) showed nothing during AI-run
combat, since they all just render game.combat.
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from combat.loop import CombatLoop


def _make_loop():
    loop = CombatLoop(
        foundry=MagicMock(),
        llm=MagicMock(),
        dispatcher=MagicMock(),
        state_tracker=MagicMock(),
        db=MagicMock(),
    )
    loop.foundry.execute_js = AsyncMock()
    return loop


def test_sync_foundry_combat_calls_combatants_then_turn():
    loop = _make_loop()
    loop._turn_order = ["tokA", "tokB"]
    loop._round_number = 1
    loop._current_turn_index = 0
    loop.foundry.execute_js.return_value = {"result": {"ok": True, "combatId": "c1"}}

    asyncio.run(loop._sync_foundry_combat())

    calls = loop.foundry.execute_js.call_args_list
    assert len(calls) == 2
    assert "Combat.create" in calls[0].args[0] or "combat" in calls[0].args[0].lower()
    assert "round: 1" in calls[1].args[0]
    assert "turn: 0" in calls[1].args[0]


def test_sync_foundry_combat_skips_turn_sync_when_combatant_sync_fails():
    loop = _make_loop()
    loop._turn_order = ["tokA"]
    loop.foundry.execute_js.return_value = {"result": {"ok": False, "error": "no active scene"}}

    asyncio.run(loop._sync_foundry_combat())

    assert loop.foundry.execute_js.call_count == 1  # never reached set_combat_turn


def test_sync_foundry_combat_never_raises_on_relay_failure():
    loop = _make_loop()
    loop._turn_order = ["tokA"]
    loop.foundry.execute_js.side_effect = ConnectionError("relay down")

    asyncio.run(loop._sync_foundry_combat())  # must not raise


def test_sync_foundry_combat_turn_pushes_current_state():
    loop = _make_loop()
    loop._round_number = 2
    loop._current_turn_index = 3

    asyncio.run(loop._sync_foundry_combat_turn())

    loop.foundry.execute_js.assert_awaited_once()
    js = loop.foundry.execute_js.call_args.args[0]
    assert "round: 2" in js
    assert "turn: 3" in js


def test_sync_foundry_combat_turn_never_raises_on_relay_failure():
    loop = _make_loop()
    loop.foundry.execute_js.side_effect = ConnectionError("relay down")

    asyncio.run(loop._sync_foundry_combat_turn())  # must not raise


def test_end_combat_calls_foundry_combat_cleanup():
    loop = _make_loop()
    loop.state_tracker.update_combat = AsyncMock()
    loop.state_tracker.set_mode = AsyncMock()
    loop.state_tracker.save = AsyncMock()
    loop.state_tracker.clear_combat_snapshot = MagicMock()
    loop.foundry.end_encounter = AsyncMock()
    loop.foundry.chat_message = AsyncMock()

    asyncio.run(loop._end_combat())

    # end_encounter (existing relay call) AND the direct Combat.delete() both run
    assert loop.foundry.end_encounter.await_count == 1
    assert loop.foundry.execute_js.await_count == 1
    assert "combat.delete()" in loop.foundry.execute_js.call_args.args[0]
