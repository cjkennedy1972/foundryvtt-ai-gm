"""Teardown must never delete pre-existing documents the AI GM only reused."""
import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

from campaign.orchestrator import CampaignOrchestrator


class _RecordingFoundry:
    """Captures the UUID-map script so we can assert what teardown targeted."""
    is_connected = True

    def __init__(self):
        self.scripts = []

    async def execute_js(self, code, _timeout=None):
        self.scripts.append(code)
        return {"result": {}}


def test_teardown_preserves_linked_and_reused_documents(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    state_dir = tmp_path / "campaign_assets" / "teardown test"
    state_dir.mkdir(parents=True)
    (state_dir / "deployment_state.json").write_text(json.dumps({
        "scenes": [
            {"name": "Ours", "uuid": "Scene.OURS", "status": "created"},
            {"name": "Map 3.2: Battle of High Hill", "uuid": "Scene.THEIRS", "status": "linked"},
        ],
        "npcs": [
            {"name": "Our NPC", "uuid": "Actor.OURS", "status": "created"},
            {"name": "Lord Soth", "uuid": "Actor.THEIRS", "status": "linked"},
        ],
        "encounter_actors": [
            {"name": "Imported Goblin", "uuid": "Actor.IMPORTED", "reused": False},
            {"name": "Baaz Draconian", "uuid": "Actor.PREEXISTING", "reused": True},
        ],
    }))

    foundry = _RecordingFoundry()
    orch = CampaignOrchestrator()
    result = asyncio.run(orch.teardown_campaign("Teardown Test", foundry))

    uuid_script = next((s for s in foundry.scripts if "uuidMap" in s), "")
    assert "Scene.OURS" in uuid_script
    assert "Actor.OURS" in uuid_script
    assert "Actor.IMPORTED" in uuid_script
    # The user's own content must never be targeted
    assert "Scene.THEIRS" not in uuid_script
    assert "Actor.THEIRS" not in uuid_script
    assert "Actor.PREEXISTING" not in uuid_script
    assert len(result["preserved"]) == 3


class _EncounterFoundry:
    """Minimal client for deploy_encounters' actor-provenance path."""
    is_connected = True

    def __init__(self, existing, fail_snapshot=False):
        self._existing = existing
        self._fail = fail_snapshot

    async def get_actors(self, world_only=False):
        if self._fail:
            raise RuntimeError("relay down")
        return self._existing

    async def activate_scene_and_wait(self, name, timeout=None):
        return {}

    async def get_scene_by_name(self, name):
        return None

    async def canvas_get(self, doc_type):
        return []

    async def canvas_create(self, doc_type, data):
        return {}

    async def _send(self, *a, **kw):
        return {}


def _run_encounters(foundry, monster_uuid):
    """Deploy one encounter whose monster resolves to monster_uuid."""
    campaign_data = {
        "scenes": [{"name": "S", "scene_setup": {"grid_width": 10, "grid_height": 10}}],
        "encounters": [{
            "name": "E", "linked_scene": "S",
            "monsters": [{"name": "Baaz Draconian", "count": 1}],
        }],
    }
    deployment = {"scenes": [{"name": "S", "status": "created"}]}
    orch = CampaignOrchestrator()
    with patch.object(orch, "_ensure_monster_actor", new_callable=AsyncMock,
                      return_value=monster_uuid):
        asyncio.run(orch.deploy_encounters(campaign_data, foundry, deployment, mods={}))
    return deployment.get("encounter_actors", [])


def test_encounter_actor_that_already_existed_is_marked_reused():
    foundry = _EncounterFoundry(existing=[{"name": "Baaz Draconian", "uuid": "Actor.PRE"}])
    tracked = _run_encounters(foundry, "Actor.PRE")
    assert tracked and tracked[0]["reused"] is True


def test_encounter_actor_newly_imported_is_marked_ours():
    foundry = _EncounterFoundry(existing=[{"name": "Something Else", "uuid": "Actor.OTHER"}])
    tracked = _run_encounters(foundry, "Actor.NEW")
    assert tracked and tracked[0]["reused"] is False


def test_encounter_actor_snapshot_failure_fails_safe_as_reused():
    """Can't prove ownership -> must not risk deleting the user's actor."""
    foundry = _EncounterFoundry(existing=[], fail_snapshot=True)
    tracked = _run_encounters(foundry, "Actor.UNKNOWN")
    assert tracked and tracked[0]["reused"] is True
