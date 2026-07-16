"""Campaign selection controls when the relay and Foundry session are used."""

from types import SimpleNamespace

import pytest

from api.routes.campaign import CampaignBuildRequest, _select_campaign_world, build_campaign_endpoint
from config import settings


class FakeRelayManager:
    def __init__(self, running=True):
        self.running = running
        self.started = 0
        self.headless_worlds = []

    def status(self):
        return {"running": self.running}

    async def start(self):
        self.started += 1
        self.running = True

    async def ensure_api_key(self):
        return None

    async def restart_headless_session(self, world_name):
        self.headless_worlds.append(world_name)
        return "fvtt-test"

    async def ensure_headless_session(self, world_name=None):
        self.headless_worlds.append(world_name)
        return "fvtt-test"


class FakeFoundryClient:
    def __init__(self, connected=False, world=None):
        self.is_connected = connected
        self.world = world or {"title": "", "id": ""}
        self.connect_calls = 0
        self.disconnect_calls = 0

    async def connect(self, max_retries=3):
        self.connect_calls += 1
        self.is_connected = True
        return True

    async def disconnect(self):
        self.disconnect_calls += 1
        self.is_connected = False

    async def execute_js(self, _script):
        return {"result": self.world}


@pytest.mark.anyio
async def test_unlinked_campaign_adopts_a_manually_paired_world(monkeypatch):
    import campaign.obsidian_sync as sync

    linked = []
    monkeypatch.setattr(settings, "relay_managed", True)
    monkeypatch.setattr(settings, "foundry_world", "")
    monkeypatch.setattr(sync, "get_campaign_world", lambda _name: None)
    monkeypatch.setattr(sync, "link_world_to_campaign", lambda *args: linked.append(args))
    relay = FakeRelayManager(running=True)
    foundry = FakeFoundryClient(world={"title": "Manual World", "id": "world-1"})
    state = SimpleNamespace(relay_manager=relay, foundry_client=foundry)

    assert await _select_campaign_world("New Campaign", state) is None
    assert relay.headless_worlds == []
    assert linked == [("New Campaign", "Manual World", "world-1")]


@pytest.mark.anyio
async def test_linked_campaign_launches_its_world_in_headless_chrome(monkeypatch):
    import campaign.obsidian_sync as sync

    monkeypatch.setattr(settings, "relay_managed", True)
    monkeypatch.setattr(settings, "foundry_world", "")
    monkeypatch.setattr(sync, "get_campaign_world", lambda _name: {"world_name": "Linked World", "world_id": "world-2"})
    relay = FakeRelayManager(running=True)
    foundry = FakeFoundryClient()
    state = SimpleNamespace(relay_manager=relay, foundry_client=foundry)

    assert await _select_campaign_world("Existing Campaign", state) is None
    assert relay.headless_worlds == ["Linked World"]
    assert foundry.disconnect_calls == 1
    assert foundry.connect_calls == 1


@pytest.mark.anyio
async def test_builder_uses_and_links_a_manually_paired_world(monkeypatch):
    import campaign.obsidian_sync as sync
    import campaign.orchestrator as orchestrator_module

    linked = []

    class FakeOrchestrator:
        async def build_campaign(self, **kwargs):
            assert kwargs["foundry_client"] is foundry
            return {"status": "success", "campaign_id": "campaign-1", "ready_to_start": True}

    monkeypatch.setattr(settings, "relay_managed", True)
    monkeypatch.setattr(orchestrator_module, "CampaignOrchestrator", FakeOrchestrator)
    monkeypatch.setattr(sync, "link_world_to_campaign", lambda *args: linked.append(args) or True)
    relay = FakeRelayManager(running=True)
    foundry = FakeFoundryClient(connected=True, world={"title": "Manual World", "id": "world-1"})
    state = SimpleNamespace(relay_manager=relay, foundry_client=foundry)

    response = await build_campaign_endpoint(
        CampaignBuildRequest(name="New Campaign", create_world=False), state
    )

    assert response.status == "success"
    assert relay.headless_worlds == []  # live paired world adopted, nothing launched
    assert linked == [("New Campaign", "Manual World", "world-1")]


@pytest.mark.anyio
async def test_builder_launches_only_an_explicitly_named_offline_world(monkeypatch):
    import campaign.obsidian_sync as sync
    import campaign.orchestrator as orchestrator_module

    class FakeOrchestrator:
        async def build_campaign(self, **kwargs):
            return {"status": "success", "campaign_id": "campaign-1", "ready_to_start": True}

    monkeypatch.setattr(settings, "relay_managed", True)
    monkeypatch.setattr(settings, "foundry_world", "")
    monkeypatch.setattr(orchestrator_module, "CampaignOrchestrator", FakeOrchestrator)
    monkeypatch.setattr(sync, "link_world_to_campaign", lambda *args: True)
    relay = FakeRelayManager(running=True)
    foundry = FakeFoundryClient(connected=False, world={"title": "Named World", "id": "world-2"})
    state = SimpleNamespace(relay_manager=relay, foundry_client=foundry)

    response = await build_campaign_endpoint(
        CampaignBuildRequest(
            name="New Campaign", create_world=False, foundry_world_name="Named World"
        ),
        state,
    )

    assert response.status == "success"
    assert relay.headless_worlds == ["Named World"]
    assert foundry.connect_calls == 1


@pytest.mark.anyio
async def test_builder_refuses_to_guess_a_world_when_disconnected(monkeypatch):
    monkeypatch.setattr(settings, "relay_managed", True)
    monkeypatch.setattr(settings, "foundry_world", "")
    relay = FakeRelayManager(running=True)
    foundry = FakeFoundryClient(connected=False)
    state = SimpleNamespace(relay_manager=relay, foundry_client=foundry)

    response = await build_campaign_endpoint(
        CampaignBuildRequest(name="New Campaign", create_world=False), state
    )

    assert response.status == "error"
    assert "new campaign" in response.error
    assert relay.headless_worlds == []  # never launches a fallback/stale world
