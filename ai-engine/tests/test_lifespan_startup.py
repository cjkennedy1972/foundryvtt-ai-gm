"""Characterisation of main.lifespan: what startup builds, and what shutdown closes.

lifespan was a single 339-line function with fifteen numbered steps and no
test beyond its security gate, which made decomposing it unsafe. These tests
pin the observable contract — the set of components on app.state, and the
fact that each is closed on the way out — so the steps can be moved without
silently dropping one.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

import main
from config import settings

# Every component a route or background task resolves off app.state.
EXPECTED_COMPONENTS = {
    "action_dispatcher", "ambient_manager", "campaign_loader", "chat_listener",
    "combat_loop", "context_manager", "db", "effects_manager", "foundry_client",
    "item_manager", "llm_manager", "macro_manager", "npc_registry",
    "particle_manager", "personality_engine", "reinforcement_mgr",
    "relay_manager", "scene_awareness", "state_tracker", "token_usage",
    "tts_service", "vision_manager",
}


@pytest.fixture
def offline_settings(tmp_path, monkeypatch):
    """Startup with every outbound dependency switched off."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(settings, "tts_enabled", False)
    monkeypatch.setattr(settings, "vault_embeddings_enabled", False)
    monkeypatch.setattr(settings, "relay_managed", False)
    monkeypatch.setattr(settings, "admin_host", "127.0.0.1")
    monkeypatch.setattr(settings, "admin_token", "")
    monkeypatch.setattr(settings, "npc_agent_model", "")
    return settings


@pytest.mark.asyncio
async def test_startup_populates_every_component(offline_settings):
    app = MagicMock()

    async with main.lifespan(app):
        present = {a for a in vars(app.state) if not a.startswith("_")}

    missing = sorted(EXPECTED_COMPONENTS - present)
    assert missing == [], f"startup no longer builds: {missing}"


@pytest.mark.asyncio
async def test_startup_registers_the_session_control_router(offline_settings):
    """The router is added after app.state is complete; order matters."""
    app = MagicMock()

    async with main.lifespan(app):
        pass

    app.include_router.assert_called_once()


@pytest.mark.asyncio
async def test_shutdown_closes_the_database(offline_settings):
    app = MagicMock()

    async with main.lifespan(app) as _:
        db = app.state.db
        assert db._conn is not None, "database should be open while running"

    assert db._conn is None, "shutdown must close the database connection"


@pytest.mark.asyncio
async def test_shutdown_stops_the_background_loops(offline_settings):
    app = MagicMock()

    async with main.lifespan(app):
        chat_listener = app.state.chat_listener
        combat_loop = app.state.combat_loop
        chat_listener._running = True

    assert chat_listener._running is False
    assert combat_loop.is_running is False


@pytest.mark.asyncio
async def test_budget_exhaustion_is_wired_to_the_chat_listener(offline_settings):
    """token_usage.on_exhausted drives degraded mode; an unwired hook is silent."""
    app = MagicMock()

    async with main.lifespan(app):
        usage = app.state.token_usage
        listener = app.state.chat_listener
        listener.handle_budget_exhausted = AsyncMock()

        assert usage.on_exhausted is not None
        await usage.on_exhausted(RuntimeError("budget gone"))

    listener.handle_budget_exhausted.assert_awaited_once()


@pytest.mark.asyncio
async def test_refuses_to_start_when_exposed_without_a_token(offline_settings, monkeypatch):
    monkeypatch.setattr(settings, "admin_host", "0.0.0.0")
    monkeypatch.setattr(settings, "admin_token", "")

    with pytest.raises(RuntimeError, match="ADMIN_TOKEN"):
        async with main.lifespan(MagicMock()):
            pass
