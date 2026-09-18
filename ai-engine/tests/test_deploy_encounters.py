"""Characterisation of deploy_encounters before its 335 lines are split.

Only the composition guard and the teardown test referenced this method, so
the suite went green whether or not it worked. These pin the observable
contract — the per-encounter result dicts and the calls made to Foundry — so
the extraction is visible as a refactor rather than trusted on faith.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from campaign.orchestrator import CampaignOrchestrator


def _orch():
    return CampaignOrchestrator()


def _foundry(actors=None):
    f = AsyncMock()
    f.get_actors.return_value = actors if actors is not None else []
    f.activate_scene_and_wait.return_value = {"ok": True}
    f.execute_js.return_value = {"result": []}
    f.create_entity.return_value = {"uuid": "JournalEntry.j1"}
    return f


def _deployment(scene="The Crypt", status="created"):
    return {"scenes": [{"name": scene, "status": status}]}


@pytest.mark.asyncio
async def test_no_encounters_returns_an_empty_list():
    result = await _orch().deploy_encounters({}, _foundry(), _deployment(), {})

    assert result == []


@pytest.mark.asyncio
async def test_each_encounter_yields_a_result_row():
    data = {
        "encounters": [
            {"name": "Ambush", "linked_scene": "The Crypt"},
            {"name": "Boss", "linked_scene": "The Crypt"},
        ],
        "scenes": [{"name": "The Crypt", "scene_setup": {}}],
    }

    results = await _orch().deploy_encounters(data, _foundry(), _deployment(), {})

    assert [r["name"] for r in results] == ["Ambush", "Boss"]
    assert all(set(r) >= {"name", "scene", "tokens_placed", "journal_created", "status", "errors"}
               for r in results)


@pytest.mark.asyncio
async def test_an_undeployed_scene_is_reported_partial_not_silently_dropped():
    data = {
        "encounters": [{"name": "Ambush", "linked_scene": "Nowhere At All"}],
        "scenes": [],
    }

    results = await _orch().deploy_encounters(data, _foundry(), _deployment(), {})

    assert results[0]["status"] == "partial"
    assert any("token placement skipped" in e for e in results[0]["errors"])


@pytest.mark.asyncio
async def test_a_near_miss_scene_name_is_fuzzy_matched():
    """The LLM drifts on scene names; an exact-match-only lookup dropped them."""
    data = {
        "encounters": [{"name": "Ambush", "linked_scene": "Crypt"}],
        "scenes": [{"name": "The Crypt", "scene_setup": {}}],
    }

    results = await _orch().deploy_encounters(data, _foundry(), _deployment(), {})

    assert results[0]["scene"] == "The Crypt"


@pytest.mark.asyncio
async def test_a_linked_scene_is_switched_to_by_its_real_foundry_name():
    """Linked scenes are tracked under a generated name; Foundry needs the real one."""
    foundry = _foundry()
    deployment = {"scenes": [
        {"name": "The Crypt", "status": "linked", "foundry_name": "Ch3 — Crypt of Bones"},
    ]}
    data = {
        "encounters": [{"name": "Ambush", "linked_scene": "The Crypt"}],
        "scenes": [{"name": "The Crypt", "scene_setup": {}}],
    }

    await _orch().deploy_encounters(data, foundry, deployment, {})

    switched = [c.args[0] for c in foundry.activate_scene_and_wait.call_args_list]
    assert "Ch3 — Crypt of Bones" in switched


@pytest.mark.asyncio
async def test_a_failed_scene_switch_is_recorded_not_raised():
    foundry = _foundry()
    foundry.activate_scene_and_wait.side_effect = ConnectionError("relay down")
    data = {
        "encounters": [{"name": "Ambush", "linked_scene": "The Crypt"}],
        "scenes": [{"name": "The Crypt", "scene_setup": {}}],
    }

    results = await _orch().deploy_encounters(data, foundry, _deployment(), {})

    assert results[0]["status"] == "partial"
    assert any("scene switch" in e for e in results[0]["errors"])


@pytest.mark.asyncio
async def test_a_failed_actor_snapshot_fails_safe():
    """Ownership can't be proven, so teardown must not claim the user's actors.

    The comment records the live incident: an empty snapshot marked every
    reused actor as ours and teardown destroyed four DDBImporter monsters.
    """
    foundry = _foundry()
    foundry.get_actors.side_effect = ConnectionError("relay down")
    data = {
        "encounters": [{"name": "Ambush", "linked_scene": "The Crypt"}],
        "scenes": [{"name": "The Crypt", "scene_setup": {}}],
    }

    results = await _orch().deploy_encounters(data, foundry, _deployment(), {})

    assert results[0]["name"] == "Ambush"   # still proceeds, just untracked
