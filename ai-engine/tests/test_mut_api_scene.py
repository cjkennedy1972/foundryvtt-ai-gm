"""Mutation-audit tests: session-control routes, scene cache bound, deployment 404, embedding probe."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from api import startup
from api.deps import ApiError, AppState, ErrorResponse, get_app_state
from api.routes import campaign as campaign_routes
from api.routes.session_control import create_session_control_router
from config import settings
from scene import awareness
from scene.awareness import SceneAwareness


def _session_client(listener, session_id=None):
    st = SimpleNamespace(db=AsyncMock(), chat_listener=listener)
    st.db.get_active_session.return_value = session_id
    app = FastAPI()
    app.include_router(create_session_control_router(st))
    return TestClient(app, raise_server_exceptions=False)


def test_pause_stops_the_listener():
    listener = SimpleNamespace(_running=True)
    r = _session_client(listener).post("/api/session/pause")
    assert r.status_code == 200 and listener._running is False


def test_idle_beat_without_active_session_is_400_and_runs_nothing():
    listener = SimpleNamespace(_running=True, _run_proactive_action=AsyncMock())
    r = _session_client(listener, session_id=None).post("/api/session/idle-beat")
    assert r.status_code == 400
    listener._run_proactive_action.assert_not_called()


def test_exactly_max_cached_scenes_evicts_nothing():
    sa = SceneAwareness(AsyncMock(), MagicMock())
    for i in range(awareness.MAX_CACHED_SCENES):
        sa._cache_scene(f"s{i}", {})
    assert len(sa._scene_data) == awareness.MAX_CACHED_SCENES and "s0" in sa._scene_data
    sa._cache_scene("extra", {})
    assert "s0" not in sa._scene_data and len(sa._scene_data) == awareness.MAX_CACHED_SCENES


def test_enrich_scenes_missing_deployment_is_404_not_400():
    state = AppState()
    state.foundry_client = AsyncMock()
    state.foundry_client.is_connected = True
    state.campaign_loader = MagicMock()
    state.campaign_loader.current_campaign_name = "Valenthal"
    app = FastAPI()
    app.include_router(campaign_routes.router)
    app.dependency_overrides[get_app_state] = lambda: state

    @app.exception_handler(ApiError)
    async def _h(request, exc):
        return JSONResponse(status_code=exc.status, content=ErrorResponse(status="error", error=exc.error, code=exc.code).model_dump())

    store = MagicMock()
    store.load = AsyncMock(return_value={})
    store.load_deployment = AsyncMock(return_value=None)
    with patch("api.routes.campaign.CampaignStore", return_value=store):
        r = TestClient(app, raise_server_exceptions=False).post("/api/campaign/enrich-scenes", json={})
    assert r.status_code == 404 and r.json()["code"] == "DEPLOYMENT_NOT_FOUND"


@pytest.mark.parametrize("vectors", [[], [[]]])
def test_probe_rejects_empty_vector(monkeypatch, vectors):
    class Fake:
        def __init__(self, **kw): pass
        async def embed(self, texts): return vectors
    monkeypatch.setattr("vault.embeddings.OllamaEmbeddings", Fake)
    with pytest.raises(RuntimeError, match="no vector"):
        asyncio.run(startup._probe_embeddings("ollama", "m"))


def test_fallback_equal_to_primary_reraises_without_second_probe(monkeypatch):
    monkeypatch.setattr(settings, "vault_embeddings_provider", "ollama")
    monkeypatch.setattr(settings, "vault_embeddings_fallback_provider", "ollama")
    probe = AsyncMock(side_effect=ConnectionError("down"))
    monkeypatch.setattr(startup, "_probe_embeddings", probe)
    with pytest.raises(ConnectionError):
        asyncio.run(startup._select_embeddings())
    assert probe.await_count == 1
