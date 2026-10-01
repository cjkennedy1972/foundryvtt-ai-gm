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


# ── review findings on the first version ────────────────────────────────────

@pytest.mark.asyncio
async def test_an_action_recorded_during_a_restore_keeps_its_own_undo():
    import asyncio
    fc, _ = _foundry()
    ledger = UndoLedger()
    old = {"type": "move_token", "label": "old", "restore": {"kind": "move", "token_id": "t1", "x": 1, "y": 2}}
    newer = {"type": "update_hp", "label": "newer", "restore": {"kind": "hp", "actor_uuid": "Actor.a1", "hp_before": 9}}
    ledger.record(old)
    gate = asyncio.Event()

    async def slow_move(*a):
        await gate.wait()
        return {"ok": True}

    fc.move_token = slow_move
    task = asyncio.create_task(undo_last(ledger, fc))
    await asyncio.sleep(0)            # restore is now awaiting Foundry
    ledger.record(newer)              # the AI acts meanwhile
    gate.set()
    assert (await task)["success"]
    assert [e["label"] for e in ledger.recent()] == ["newer"]   # the older entry went, not the newer one


@pytest.mark.asyncio
async def test_two_simultaneous_undos_do_not_restore_the_same_entry_twice():
    import asyncio
    fc, _ = _foundry()
    ledger = UndoLedger()
    for n in range(2):
        ledger.record({"type": "move_token", "label": f"m{n}", "restore": {"kind": "move", "token_id": f"t{n}", "x": n, "y": n}})
    fc.move_token = AsyncMock(return_value={"ok": True})
    a, b = await asyncio.gather(undo_last(ledger, fc), undo_last(ledger, fc))
    assert {a["label"], b["label"]} == {"m0", "m1"}
    assert [c.args[0] for c in fc.move_token.call_args_list] == ["t1", "t0"]


@pytest.mark.asyncio
async def test_a_refused_or_ineffective_hp_restore_is_reported_as_failed_and_kept():
    fc, state = _foundry(hp=5)
    d = ActionDispatcher(fc)
    await d.execute({"type": "update_hp", "actor_uuid": "Actor.a1", "damage": 3})     # 5 -> 2

    fc.increase_attribute = AsyncMock(return_value={"success": False, "error": "locked"})
    refused = await d.undo_last()
    assert refused["success"] is False and "locked" in refused["error"]

    fc.increase_attribute = AsyncMock(return_value={"success": True})                  # claims success, changes nothing
    ineffective = await d.undo_last()
    assert ineffective["success"] is False and "expected 5" in ineffective["error"]
    assert len(d.undo.recent()) == 1                                                   # still there to retry
    assert state["hp"] == 2


@pytest.mark.asyncio
async def test_an_hp_change_aimed_by_display_name_is_still_undoable():
    fc, state = _foundry(hp=20)
    real_dec = fc.decrease_attribute.side_effect

    async def dec(path, n, uuid):
        if uuid != "Actor.a1":                      # the relay rejects a name, as it does in play
            return {"success": False, "error": "Entity not found"}
        return await real_dec(path, n, uuid)

    fc.decrease_attribute = AsyncMock(side_effect=dec)
    d = ActionDispatcher(fc)
    res = await d.execute({"type": "update_hp", "actor_uuid": "Thorin", "damage": 6})
    assert res["success"] and state["hp"] == 14 and res["actor_uuid"] == "Actor.a1"
    assert res["hp_before"] == 20
    assert (await d.undo_last())["success"] and state["hp"] == 20
