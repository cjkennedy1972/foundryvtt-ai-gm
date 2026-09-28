"""Settlements serialized into campaign.json reach the world clock at session start.

Run:
    cd ai-engine && python -m pytest tests/test_load_campaign_settlements.py -v
"""

import asyncio
import json
from unittest.mock import MagicMock

import campaign.vault as vault
from campaign.obsidian_sync import get_campaign_folder
from campaign.settlement_integration import serialize_settlements
from foundry.chat_listener import ChatListener
from npc.registry import NPCRegistry
from world.settlement import Settlement


def _listener():
    return ChatListener(
        foundry=MagicMock(), llm=MagicMock(), dispatcher=MagicMock(),
        state_tracker=MagicMock(), db=MagicMock(), npc_registry=NPCRegistry(),
    )


def _write_campaign(tmp_path, name, data):
    folder = get_campaign_folder(tmp_path, name)
    folder.mkdir(parents=True)
    (folder / "campaign.json").write_text(json.dumps(data), encoding="utf-8")


def test_settlement_in_campaign_json_is_registered(tmp_path, monkeypatch):
    monkeypatch.setattr(vault.settings, "campaign_vault_path", str(tmp_path))
    town = Settlement(id="redmarch", name="Redmarch", region="North",
                      population=800, character="trade town")
    _write_campaign(tmp_path, "With Towns", {
        "title": "With Towns", "settlements": serialize_settlements({"redmarch": town}),
    })
    _write_campaign(tmp_path, "Old Campaign", {"title": "Old Campaign"})

    listener = _listener()
    asyncio.run(listener.load_campaign_settlements("With Towns"))
    assert [s.name for s in listener._world_clock.list_settlements()] == ["Redmarch"]

    # Switching to a campaign with no "settlements" key leaves zero, not the old ones.
    asyncio.run(listener.load_campaign_settlements("Old Campaign"))
    assert listener._world_clock.list_settlements() == []
