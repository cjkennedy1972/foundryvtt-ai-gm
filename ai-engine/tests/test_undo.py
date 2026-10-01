"""Undo of the AI's HP changes and token moves (actions/undo.py + dispatcher wiring)."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from actions.dispatcher import ActionDispatcher
from actions.undo import UndoLedger, restore_point, undo_last
from api.routes.undo import list_undoable, undo_last_action


def _foundry(hp=20, max_hp=30):
    """A relay stand-in with one actor whose hp.value Foundry clamps to [0, max]."""
    state = {"hp": hp}
    fc = MagicMock()
    fc.is_connected = True
    fc.get_actors = AsyncMock(side_effect=lambda **k: [{"uuid": "Actor.a1", "name": "Thorin", "hp": state["hp"], "max_hp": max_hp}])

    async def dec(path, n, uuid):
        state["hp"] = max(0, state["hp"] - n)
        return {"success": True}

    async def inc(path, n, uuid):
        state["hp"] = min(max_hp, state["hp"] + n)
        return {"success": True}

    fc.decrease_attribute = AsyncMock(side_effect=dec)
    fc.increase_attribute = AsyncMock(side_effect=inc)
    fc.move_token = AsyncMock(return_value={"ok": True, "id": "t1", "name": "Thorin", "x": 300, "y": 400, "fromX": 100, "fromY": 200})
    return fc, state


@pytest.mark.asyncio
async def test_undo_restores_exact_hp_even_after_a_clamped_killing_blow():
    fc, state = _foundry(hp=5)
    d = ActionDispatcher(fc)
    res = await d.execute({"type": "update_hp", "actor_uuid": "Actor.a1", "damage": 20})
    assert res["success"] and state["hp"] == 0          # 5 HP target took 20: clamped at 0
    undone = await d.undo_last()
    assert undone["success"] and state["hp"] == 5        # not 20, which "heal 20" would give
    assert (await d.undo_last())["success"] is False     # ledger now empty


@pytest.mark.asyncio
async def test_undo_reverses_a_heal_and_goes_back_in_order():
    fc, state = _foundry(hp=10)
    d = ActionDispatcher(fc)
    await d.execute({"type": "update_hp", "actor_uuid": "Actor.a1", "damage": -8})   # heal -> 18
    await d.execute({"type": "update_hp", "actor_uuid": "Actor.a1", "damage": 3})    # hit -> 15
    await d.undo_last()
    assert state["hp"] == 18
    await d.undo_last()
    assert state["hp"] == 10


@pytest.mark.asyncio
async def test_undo_moves_a_token_back():
    fc, _ = _foundry()
    d = ActionDispatcher(fc)
    await d.execute({"type": "move_token", "token_id": "t1", "x": 300, "y": 400})
    fc.move_token.reset_mock()
    fc.move_token.return_value = {"ok": True}
    assert (await d.undo_last())["success"]
    fc.move_token.assert_awaited_once_with("t1", 100, 200)


@pytest.mark.asyncio
async def test_failed_or_unreversible_actions_are_not_recorded():
    fc, _ = _foundry()
    d = ActionDispatcher(fc)
    fc.decrease_attribute = AsyncMock(return_value={"success": False})
    await d.execute({"type": "update_hp", "actor_uuid": "Actor.a1", "damage": 4})
    await d.execute({"type": "narrate", "text": "The bell tolls."})
    await d.execute({"type": "update_hp", "actor_uuid": "Actor.a1", "damage": 1, "hp_path": "hp.temp"})
    assert d.undo.recent() == []


@pytest.mark.asyncio
async def test_a_failed_restore_keeps_the_entry_for_a_retry():
    fc, _ = _foundry()
    ledger = UndoLedger()
    ledger.record({"type": "move_token", "label": "Move", "restore": {"kind": "move", "token_id": "t1", "x": 1, "y": 2}})
    fc.move_token = AsyncMock(return_value={"ok": False, "error": "blocked"})
    assert (await undo_last(ledger, fc))["success"] is False
    assert len(ledger.recent()) == 1
    fc.move_token = AsyncMock(return_value={"ok": True})
    assert (await undo_last(ledger, fc))["success"] is True
    assert ledger.recent() == []


def test_ledger_is_bounded_and_lists_newest_first():
    ledger = UndoLedger(maxlen=2)
    for n in range(3):
        ledger.record({"type": "update_hp", "label": f"e{n}", "restore": {}})
    assert [e["label"] for e in ledger.recent()] == ["e2", "e1"]


def test_restore_point_needs_the_prior_value():
    assert restore_point("update_hp", {"actor_uuid": "Actor.a1"}, {"success": True}) is None
    assert restore_point("move_token", {"token_id": "t1"}, {"result": {"ok": True}}) is None
    assert restore_point("narrate", {}, {"success": True}) is None


@pytest.mark.asyncio
async def test_undo_endpoints():
    fc, state = _foundry(hp=12)
    d = ActionDispatcher(fc)
    app = MagicMock()
    app.action_dispatcher = d
    await d.execute({"type": "update_hp", "actor_uuid": "Actor.a1", "damage": 5})
    listed = await list_undoable(app)
    assert listed["actions"][0]["type"] == "update_hp"
    ok = await undo_last_action(app)
    assert ok["status"] == "ok" and state["hp"] == 12
    nothing = await undo_last_action(app)
    assert nothing.status_code == 409 and json.loads(nothing.body)["code"] == "UNDO_FAILED"
    app.action_dispatcher = None
    assert (await undo_last_action(app)).status_code == 503
