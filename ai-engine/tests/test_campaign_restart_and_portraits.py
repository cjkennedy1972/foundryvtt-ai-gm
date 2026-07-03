"""Checks for campaign restart history wipe and portrait attachment on deploy.

Covers the fixes for: NPC portraits lost on redeploy (deploy_to_foundry never
set actor img) and full campaign restart (delete_campaign_history).
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from persistence.db import Database
from campaign.orchestrator import CampaignOrchestrator


class StubFoundry:
    """Minimal foundry client: records created entities, answers execute_js."""

    is_connected = True

    def __init__(self):
        self.created = []

    async def _send(self, action, **kw):
        if action == "create":
            self.created.append((kw.get("entityType"), kw.get("data")))
        return {"uuid": f"Actor.stub{len(self.created)}"}

    async def execute_js(self, js):
        return {"result": []}


def test_deploy_attaches_persisted_portrait():
    """An NPC with portrait_src must be created with img + token texture."""
    orch = CampaignOrchestrator()
    client = StubFoundry()
    campaign_data = {
        "npcs": [
            {"name": "Elara", "portrait_src": "ai-gm-portraits/test/portrait_elara.png"},
            {"name": "Bare NPC"},  # no portrait — must not get an img key
        ],
    }
    asyncio.run(orch.deploy_to_foundry(campaign_data, client, {"maps": [], "portraits": []}))

    actors = {d["name"]: d for t, d in client.created if t == "Actor"}
    assert actors["Elara"]["img"] == "ai-gm-portraits/test/portrait_elara.png"
    assert actors["Elara"]["prototypeToken"]["texture"]["src"] == "ai-gm-portraits/test/portrait_elara.png"
    assert "img" not in actors["Bare NPC"]


def test_delete_campaign_history_scoped_to_campaign(tmp_path):
    """Restart wipes one campaign's sessions/conversations/events, not others."""

    async def run():
        db = Database(str(tmp_path / "test.db"))
        await db.init()

        await db.create_session("sess-a", "Campaign A")
        await db.save_conversation("sess-a", "user", "hello")
        await db.record_event("sess-a", "something happened")
        await db.create_session("sess-b", "Campaign B")
        await db.save_conversation("sess-b", "user", "other campaign")

        deleted = await db.delete_campaign_history("Campaign A")
        assert deleted == 1

        assert await db.get_conversation_history("sess-a") == []
        assert await db.get_events("sess-a") == []
        assert len(await db.get_conversation_history("sess-b")) == 1
        # deleting a campaign with no sessions is a no-op
        assert await db.delete_campaign_history("Campaign A") == 0
        await db.close()

    asyncio.run(run())
