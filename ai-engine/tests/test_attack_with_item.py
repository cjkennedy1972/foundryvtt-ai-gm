"""Checks for the attack_with_item action executor — the real-combat piece
of the gameplay track (see foundry/scripts.py::resolve_item_attack, whose
JS was verified live against a running Foundry world before this was written)."""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from actions.executors import ACTION_HANDLERS, execute_attack_with_item


def _stub_foundry(js_result):
    foundry = MagicMock()
    foundry.execute_js = AsyncMock(return_value={"result": js_result})
    foundry.get_scene_tokens = AsyncMock(return_value=[{"id": "tok1", "name": "Skeleton", "actorUuid": ""}])
    return foundry


def test_registered_under_attack_with_item():
    assert ACTION_HANDLERS["attack_with_item"] is execute_attack_with_item


def test_hit_result_passthrough():
    foundry = _stub_foundry({
        "ok": True, "hit": True, "isCrit": False, "attackTotal": 18, "targetAc": 14,
        "damageTotal": 12, "damageTypes": ["slashing"], "targetName": "Skeleton 2",
        "targetHpBefore": 13, "targetHpAfter": 1,
    })

    result = asyncio.run(execute_attack_with_item("Actor.abc", "Rusty Cutlass", "tok1", foundry=foundry))

    assert result["success"] is True
    assert result["hit"] is True
    assert result["damageTotal"] == 12
    assert result["type"] == "attack_with_item"


def test_miss_result_passthrough():
    foundry = _stub_foundry({
        "ok": True, "hit": False, "isCrit": False, "attackTotal": 9, "targetAc": 14,
        "damageTotal": 0, "damageTypes": [], "targetName": "Skeleton 2",
        "targetHpBefore": 13, "targetHpAfter": 13,
    })

    result = asyncio.run(execute_attack_with_item("Actor.abc", "Rusty Cutlass", "tok1", foundry=foundry))

    assert result["success"] is True
    assert result["hit"] is False
    assert result["damageTotal"] == 0


def test_item_not_found_reports_available_items():
    foundry = _stub_foundry({
        "ok": False, "error": "item not found", "available": ["Dagger", "Fire Bolt"],
    })

    result = asyncio.run(execute_attack_with_item("Actor.abc", "Longsword", "tok1", foundry=foundry))

    assert result["success"] is False
    assert "not found" in result["error"]


def test_no_attack_activity_reports_error():
    foundry = _stub_foundry({"ok": False, "error": "item has no usable attack", "itemName": "Torch"})

    result = asyncio.run(execute_attack_with_item("Actor.abc", "Torch", "tok1", foundry=foundry))

    assert result["success"] is False
    assert "no usable attack" in result["error"]


def test_relay_exception_does_not_propagate():
    foundry = MagicMock()
    foundry.execute_js = AsyncMock(side_effect=ConnectionError("relay down"))
    foundry.get_scene_tokens = AsyncMock(return_value=[])

    result = asyncio.run(execute_attack_with_item("Actor.abc", "Cutlass", "tok1", foundry=foundry))

    assert result["success"] is False
    assert "relay down" in result["error"]


def test_target_token_id_resolved_before_use():
    # _resolve_token_id looks up the real scene token id from a name/uuid;
    # attack_with_item must use the resolved id, not the raw LLM-provided one.
    foundry = _stub_foundry({"ok": True, "hit": True, "isCrit": False, "attackTotal": 20, "targetAc": 10,
                              "damageTotal": 5, "damageTypes": ["piercing"], "targetName": "Goblin",
                              "targetHpBefore": 7, "targetHpAfter": 2})
    foundry.get_scene_tokens = AsyncMock(return_value=[{"id": "realTokenId", "name": "Goblin", "actorUuid": ""}])

    asyncio.run(execute_attack_with_item("Actor.abc", "Cutlass", "Goblin", foundry=foundry))

    js_arg = foundry.execute_js.call_args.args[0]
    assert '"realTokenId"' in js_arg
