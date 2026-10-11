"""Campaign start under the playwright backend: verify the serving world, never start the relay."""

import asyncio
import types

import pytest

from api.routes import campaign as campaign_routes
from config import settings


class FakeClient:
    def __init__(self, title, world_id, connected=True):
        self.is_connected = connected
        self._title = title
        self._id = world_id
        self._last_connect_error = ""
        self.connect_calls = 0

    async def connect(self, max_retries=3):
        self.connect_calls += 1
        self.is_connected = True
        return True

    async def execute_js(self, code, _timeout=None):
        return {"result": {"title": self._title, "id": self._id}}


def _state(client):
    return types.SimpleNamespace(foundry_client=client, relay_manager=types.SimpleNamespace(
        status=lambda: {"running": False}, start=None))


@pytest.fixture
def playwright(monkeypatch):
    monkeypatch.setattr(settings, "foundry_backend", "playwright")


def _linked(monkeypatch, name, world_name, world_id):
    import campaign.obsidian_sync as sync
    monkeypatch.setattr(sync, "get_campaign_world", lambda c, vault_path=None: {"world_name": world_name, "world_id": world_id})


def test_matching_world_starts_without_relaunch(playwright, monkeypatch):
    _linked(monkeypatch, "Sunder", "Umbraeth", "umbraeth")
    client = FakeClient("Umbraeth", "umbraeth")
    assert asyncio.run(campaign_routes._select_campaign_world("Sunder", _state(client))) is None


def test_mismatched_world_refuses_and_names_both(playwright, monkeypatch):
    _linked(monkeypatch, "Sunder", "Umbraeth", "umbraeth")
    client = FakeClient("Krynn", "krynn")
    err = asyncio.run(campaign_routes._select_campaign_world("Sunder", _state(client)))
    assert "'Krynn'" in err and "'Umbraeth'" in err


def test_disconnected_client_logs_in_first(playwright, monkeypatch):
    _linked(monkeypatch, "Sunder", "Umbraeth", "umbraeth")
    client = FakeClient("Umbraeth", "umbraeth", connected=False)
    assert asyncio.run(campaign_routes._select_campaign_world("Sunder", _state(client))) is None
    assert client.connect_calls == 1


def test_failed_login_is_reported(playwright, monkeypatch):
    _linked(monkeypatch, "Sunder", "Umbraeth", "umbraeth")
    client = FakeClient("Umbraeth", "umbraeth", connected=False)
    client._last_connect_error = "login rejected"

    async def failing_connect(max_retries=3):
        return False

    client.connect = failing_connect
    err = asyncio.run(campaign_routes._select_campaign_world("Sunder", _state(client)))
    assert "login rejected" in err
