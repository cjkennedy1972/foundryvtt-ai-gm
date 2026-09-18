"""Characterisation of deploy_to_foundry before its 444 lines are split.

Eight independent "deploy X" blocks, none of them covered: the missing ranges
in the module's 44% were exactly these. Each test pins one block's
contribution to the returned deployment dict, so the split is observable as a
refactor.
"""

from unittest.mock import AsyncMock

import pytest

from campaign.orchestrator import CampaignOrchestrator


def _foundry(uuid="Actor.1"):
    f = AsyncMock()
    f._send.return_value = {"data": {"uuid": uuid, "_id": uuid}}
    f.execute_js.return_value = {"result": []}
    return f


async def _deploy(campaign_data, mods=None):
    scan = {"active_modules": mods or {}}
    return await CampaignOrchestrator().deploy_to_foundry(
        campaign_data, _foundry(), {}, scan_result=scan
    )


@pytest.mark.asyncio
async def test_an_empty_campaign_still_returns_the_full_deployment_shape():
    result = await _deploy({})

    for key in ("scenes", "npcs", "journal_entries", "quest_logs", "loot_tables",
                "loot_piles", "playlists", "calendar_events", "encounters",
                "encounter_actors"):
        assert result[key] == [], f"{key} should start empty"
    assert result["status"] == "complete"


@pytest.mark.asyncio
async def test_npcs_are_deployed_and_recorded():
    result = await _deploy({"npcs": [{"name": "Borin", "role": "innkeeper"}]})

    assert len(result["npcs"]) == 1
    assert result["npcs"][0]["name"] == "Borin"


@pytest.mark.asyncio
async def test_journal_entries_are_deployed_and_recorded():
    """Journals and quests are keyed by `title`; NPCs and scenes by `name`."""
    result = await _deploy({"journal_entries": [{"title": "Lore", "content": "Once..."}]})

    assert [j["title"] for j in result["journal_entries"]] == ["Lore"]


@pytest.mark.asyncio
async def test_quest_logs_are_deployed_and_recorded():
    result = await _deploy({"quest_logs": [{"title": "Find the Sword", "objectives": ["go"]}]})

    assert [q["title"] for q in result["quest_logs"]] == ["Find the Sword"]


@pytest.mark.asyncio
async def test_loot_tables_are_deployed_and_recorded():
    result = await _deploy({"loot_tables": [{"name": "Crypt Hoard", "items": ["gem"]}]})

    assert [t["name"] for t in result["loot_tables"]] == ["Crypt Hoard"]


@pytest.mark.asyncio
async def test_scenes_are_deployed_and_recorded():
    result = await _deploy({"scenes": [{"name": "The Crypt", "description": "dark"}]})

    assert [s["name"] for s in result["scenes"]] == ["The Crypt"]


@pytest.mark.asyncio
async def test_calendar_events_need_the_calendar_module():
    data = {"calendar_events": [{"name": "Festival", "date": "1/1"}]}

    without = await _deploy(data)
    with_mod = await _deploy(data, mods={"foundryvtt-simple-calendar-reborn": {"active": True}})

    assert without["calendar_events"] == []
    assert len(with_mod["calendar_events"]) == 1


@pytest.mark.asyncio
async def test_playlists_need_a_soundscape_module():
    data = {"playlists": [{"name": "Tavern", "tracks": ["lute.ogg"]}]}

    without = await _deploy(data)
    with_mod = await _deploy(data, mods={"dynamic-soundscapes": {"active": True}})

    assert without["playlists"] == []
    assert len(with_mod["playlists"]) == 1


@pytest.mark.asyncio
async def test_a_failing_create_does_not_abort_the_whole_deployment():
    """One bad entity must not cost the rest of the campaign."""
    foundry = _foundry()
    foundry._send.side_effect = ConnectionError("relay down")

    result = await CampaignOrchestrator().deploy_to_foundry(
        {"npcs": [{"name": "Borin"}], "journal_entries": [{"name": "Lore"}]},
        foundry, {}, scan_result={"active_modules": {}},
    )

    assert result["status"] in ("complete", "partial")
