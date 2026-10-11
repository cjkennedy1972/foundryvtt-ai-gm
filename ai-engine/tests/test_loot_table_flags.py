import asyncio
from unittest.mock import AsyncMock

from campaign.orchestrator import CampaignOrchestrator


def test_loot_tables_are_created_with_the_ai_gm_flag():
    # Teardown's flag pass deletes only flagged documents. An unflagged loot table survives a
    # teardown, and the UUID fallback only knows the last deploy's tables.
    client = AsyncMock()
    client.create_entity = AsyncMock(return_value={"uuid": "RollTable.1"})
    deployment = {"loot_tables": []}
    tables = [{"name": "The Sanctum Loot", "entries": [{"name": "Gold", "weight": 1}]}]

    asyncio.run(CampaignOrchestrator()._deploy_loot_tables({"loot_tables": tables}, client, deployment, {}))

    entity_type, data = client.create_entity.await_args.args
    assert entity_type == "RollTable"
    assert data["flags"] == {"ai-gm": {"type": "loot_table"}}
    assert deployment["loot_tables"] == [{"name": "The Sanctum Loot", "uuid": "RollTable.1", "status": "created"}]
