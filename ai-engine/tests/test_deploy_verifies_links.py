import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from campaign.orchestrator import CampaignOrchestrator


def run(c):
    return asyncio.run(c)


def foundry(exists: bool, uuid="Actor.new"):
    f = MagicMock()
    f.create_entity = AsyncMock(return_value={"data": {"uuid": uuid}})
    f.execute_js = AsyncMock(return_value={"result": exists})
    return f


def deployment():
    return {k: [] for k in ("scenes", "npcs", "journal_entries", "quest_logs", "loot_tables")}


def test_a_saved_npc_uuid_that_is_gone_is_created_again():
    # The teardown deleted the actor; the saved UUID is stale and must not count as deployed.
    f, d = foundry(exists=False), deployment()
    run(CampaignOrchestrator()._deploy_npcs({"npcs": [{"name": "A", "existing_uuid": "Actor.old"}]}, f, d, {}))

    f.execute_js.assert_awaited()
    f.create_entity.assert_awaited_once()
    assert d["npcs"] == [{"name": "A", "uuid": "Actor.new", "status": "created"}]


def test_a_saved_scene_uuid_that_is_gone_is_created_again():
    f, d = foundry(exists=False, uuid="Scene.new"), deployment()
    run(CampaignOrchestrator()._deploy_scenes({"scenes": [{"name": "Oakhaven", "existing_uuid": "Scene.old"}]}, f, d, {}))

    f.create_entity.assert_awaited_once()
    assert d["scenes"][0]["status"] == "created"
    assert d["scenes"][0]["uuid"] == "Scene.new"


def test_a_saved_uuid_that_is_still_there_stays_linked():
    f, d = foundry(exists=True), deployment()
    run(CampaignOrchestrator()._deploy_npcs({"npcs": [{"name": "A", "existing_uuid": "Actor.old"}]}, f, d, {}))

    f.create_entity.assert_not_awaited()
    assert d["npcs"] == [{"name": "A", "uuid": "Actor.old", "status": "linked"}]


def test_a_failed_existence_check_raises_instead_of_creating_a_duplicate():
    f, d = foundry(exists=True), deployment()
    f.execute_js = AsyncMock(side_effect=RuntimeError("page gone"))

    with pytest.raises(RuntimeError, match="page gone"):
        run(CampaignOrchestrator()._deploy_npcs({"npcs": [{"name": "A", "existing_uuid": "Actor.old"}]}, f, d, {}))
    f.create_entity.assert_not_awaited()
