"""Mutation-audit tests: dispatcher gating/success derivation and undo restore details."""
from unittest.mock import AsyncMock

import pytest

import actions.undo as undo_mod
from actions.dispatcher import ActionDispatcher
from actions.executors import ACTION_HANDLERS
from actions.undo import UndoLedger, restore_point, undo_last
from conftest import minimal_action_payload


@pytest.mark.asyncio
async def test_player_turn_rejects_blocked_action_without_running_handler(monkeypatch):
    handler = AsyncMock(return_value={"type": "pause_game", "success": True})
    monkeypatch.setitem(ACTION_HANDLERS, "pause_game", handler)
    action = minimal_action_payload("pause_game")
    action["source"] = "player_turn"
    res = await ActionDispatcher(AsyncMock()).execute(action)
    assert res["success"] is False and "not allowed" in res["error"]
    handler.assert_not_called()


@pytest.mark.asyncio
async def test_handler_error_without_success_flag_is_failure(monkeypatch):
    async def handler(**kw):
        return {"type": "narrate", "error": "x"}
    monkeypatch.setitem(ACTION_HANDLERS, "narrate", handler)
    res = await ActionDispatcher(AsyncMock()).execute(minimal_action_payload("narrate"))
    assert res["success"] is False

    async def ok(**kw):
        return {"type": "narrate"}
    monkeypatch.setitem(ACTION_HANDLERS, "narrate", ok)
    assert (await ActionDispatcher(AsyncMock()).execute(minimal_action_payload("narrate")))["success"] is True


def test_restore_point_condition_only_when_action_added_it():
    kw = {"condition": "Prone", "actor_uuid": "Actor.a"}
    assert restore_point("apply_condition", kw, {"had_condition": True}) is None
    assert restore_point("apply_condition", kw, {"had_condition": None}) is None
    assert restore_point("apply_condition", kw, {})is None
    assert restore_point("apply_condition", kw, {"had_condition": False})["restore"]["status"] == "prone"


def test_restore_point_exhaustion_unchanged_level_is_none():
    kw = {"actor_uuid": "Actor.a"}
    assert restore_point("set_exhaustion", kw, {"previousLevel": 2, "newLevel": 2}) is None
    assert restore_point("set_exhaustion", kw, {"previousLevel": 1, "newLevel": 2})["restore"]["level"] == 1


def _hp_ledger():
    ledger = UndoLedger()
    ledger.record({"type": "update_hp", "label": "hp",
                   "restore": {"kind": "hp", "actor_uuid": "Actor.a", "hp_before": 10}})
    return ledger


@pytest.mark.asyncio
async def test_hp_undo_passes_exact_amount_and_verifies_readback(monkeypatch):
    reads = iter([(4, 10), (10, 10)])
    monkeypatch.setattr(undo_mod, "_read_hp", AsyncMock(side_effect=lambda *a: next(reads)))
    apply = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(undo_mod, "_apply_hp_once", apply)
    ledger = _hp_ledger()
    res = await undo_last(ledger, AsyncMock())
    assert res["success"] is True and ledger.last() is None
    assert apply.await_args.args[1:] == ("hp.value", 4 - 10, "Actor.a")


@pytest.mark.asyncio
async def test_hp_undo_wrong_readback_fails_and_keeps_entry(monkeypatch):
    reads = iter([(4, 10), (7, 10)])
    monkeypatch.setattr(undo_mod, "_read_hp", AsyncMock(side_effect=lambda *a: next(reads)))
    monkeypatch.setattr(undo_mod, "_apply_hp_once", AsyncMock(return_value={"success": True}))
    ledger = _hp_ledger()
    res = await undo_last(ledger, AsyncMock())
    assert res["success"] is False
    assert ledger.last() is not None
