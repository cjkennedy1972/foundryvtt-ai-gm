"""Behavioural coverage for api/routes/session.py."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.deps import AppState, get_app_state
from api.routes import session as session_routes
from config import settings
from state.models import GameMode


@pytest.fixture
def state():
    s = AppState()
    s.db = AsyncMock()
    return s


def _client(state):
    app = FastAPI()
    app.include_router(session_routes.router)
    app.dependency_overrides[get_app_state] = lambda: state
    return TestClient(app, raise_server_exceptions=False)


class TestGetState:
    def test_empty_without_tracker_and_dump_with(self, state):
        c = _client(state)
        assert c.get("/api/state").json() == {}
        state.state_tracker = MagicMock()
        state.state_tracker.state.model_dump.return_value = {"mode": "combat"}
        assert c.get("/api/state").json() == {"mode": "combat"}


class TestSettings:
    def test_get_never_returns_secrets_but_reports_presence(self, state, monkeypatch):
        monkeypatch.setattr(settings, "llm_api_key", "sk-secret")
        monkeypatch.setattr(settings, "relay_api_key", "")
        body = _client(state).get("/api/settings").json()
        assert body["llm_api_key"] == "" and body["relay_api_key"] == ""
        assert body["llm_api_key_set"] is True and body["relay_api_key_set"] is False
        assert "sk-secret" not in str(body)

    @pytest.mark.parametrize("field,value", [
        ("llm_base_url", "http://evil.example/v1"),
        ("llm_api_key", "sk-new"),
        ("relay_url", "http://evil.example:3010"),
    ])
    def test_critical_fields_need_a_restart(self, state, monkeypatch, field, value):
        monkeypatch.setattr(settings, "model", "keep")
        r = _client(state).post("/api/settings", json={field: value, "model": "changed"})
        assert r.status_code == 400 and "restart" in r.json()["detail"]
        assert getattr(settings, field) != value
        assert settings.model == "keep"  # nothing applied when the request is refused

    def test_resubmitting_current_critical_value_is_accepted(self, state, monkeypatch):
        monkeypatch.setattr(settings, "relay_url", "http://relay:3010")
        r = _client(state).post("/api/settings", json={"relay_url": "http://relay:3010"})
        assert r.status_code == 200

    def test_applies_runtime_changes_to_settings_and_components(self, state):
        state.llm_manager = SimpleNamespace(model="old", _temperature=0.0, _ai_tone="x")
        state.foundry_client = MagicMock()
        state.token_usage = SimpleNamespace(budget=1)
        r = _client(state).post("/api/settings", json={
            "model": "m-2", "temperature": 0.4, "ai_name": "Zed", "ai_tone": "grim",
            "comfyui_url": "http://comfy:1", "llm_token_budget": 500})
        assert r.status_code == 200
        assert settings.model == "m-2" and settings.temperature == 0.4
        assert settings.ai_name == "Zed" and settings.ai_tone == "grim"
        assert settings.comfyui_url == "http://comfy:1" and settings.llm_token_budget == 500
        assert (state.llm_manager.model, state.llm_manager._temperature, state.llm_manager._ai_tone) == (
            "m-2", 0.4, "grim")
        state.foundry_client.set_ai_name.assert_called_once_with("Zed")
        assert state.token_usage.budget == 500

    def test_negative_budget_is_ignored(self, state, monkeypatch):
        monkeypatch.setattr(settings, "llm_token_budget", 77)
        state.token_usage = SimpleNamespace(budget=77)
        _client(state).post("/api/settings", json={"llm_token_budget": -1})
        assert settings.llm_token_budget == 77 and state.token_usage.budget == 77

    def test_partial_update_does_not_revert_other_runtime_settings(self, state, monkeypatch):
        """A body naming one field must not reset the rest to import-time defaults."""
        monkeypatch.setattr(settings, "model", "runtime-model")
        monkeypatch.setattr(settings, "temperature", 0.9)
        monkeypatch.setattr(settings, "ai_name", "Runtime")
        monkeypatch.setattr(settings, "ai_tone", "runtime-tone")
        state.llm_manager = SimpleNamespace(model="runtime-model", _temperature=0.9, _ai_tone="runtime-tone")
        state.foundry_client = MagicMock()
        r = _client(state).post("/api/settings", json={"llm_token_budget": 123})
        assert r.status_code == 200
        assert settings.model == "runtime-model" and settings.temperature == 0.9
        assert settings.ai_name == "Runtime" and settings.ai_tone == "runtime-tone"
        assert state.llm_manager._temperature == 0.9 and state.llm_manager.model == "runtime-model"
        state.foundry_client.set_ai_name.assert_not_called()

    def test_bad_types_are_422(self, state):
        assert _client(state).post("/api/settings", json={"temperature": "hot"}).status_code == 422


class TestStateUpdate:
    def _state(self, state):
        state.state_tracker = MagicMock()
        state.state_tracker.set_mode = AsyncMock()
        state.state_tracker.set_scene = AsyncMock()
        state.state_tracker.set_campaign = AsyncMock()
        state.state_tracker.save = AsyncMock()
        state.state_tracker.state.model_dump.return_value = {"ok": 1}
        return state

    def test_applies_each_provided_field_and_saves(self, state):
        self._state(state)
        r = _client(state).post("/api/state/update", json={
            "mode": "combat", "scene": "Crypt", "session": 3, "campaign": "K"})
        assert r.json() == {"status": "ok", "state": {"ok": 1}}
        t = state.state_tracker
        t.set_mode.assert_awaited_once_with(GameMode.COMBAT)
        t.set_scene.assert_awaited_once_with("Crypt")
        t.set_campaign.assert_awaited_once_with("K")
        assert t.state.session_number == 3
        t.save.assert_awaited_once()

    def test_empty_update_changes_nothing_but_still_saves(self, state):
        self._state(state)
        _client(state).post("/api/state/update", json={})
        t = state.state_tracker
        t.set_mode.assert_not_awaited(); t.set_scene.assert_not_awaited(); t.set_campaign.assert_not_awaited()

    def test_invalid_mode_is_rejected_as_client_error_before_any_change(self, state):
        self._state(state)
        r = _client(state).post("/api/state/update", json={"mode": "bogus", "scene": "Crypt"})
        assert r.status_code == 422
        state.state_tracker.set_scene.assert_not_awaited()
        state.state_tracker.save.assert_not_awaited()


class TestSessionsAndUsage:
    def test_active_session_both_shapes(self, state):
        c = _client(state)
        state.db.get_active_session_info.return_value = {"session_id": "s1", "campaign": None}
        assert c.get("/api/session/active").json() == {
            "session_id": "s1", "campaign_name": "", "active": True, "status": "started"}
        state.db.get_active_session_info.return_value = None
        assert c.get("/api/session/active").json() == {
            "session_id": None, "campaign_name": "", "active": False, "status": "none"}

    def test_usage_for_active_session(self, state, monkeypatch):
        monkeypatch.setattr(settings, "llm_token_budget", 900)
        c = _client(state)
        state.db.get_active_session_info.return_value = None
        assert c.get("/api/session/usage").json()["total_tokens"] == 0
        state.db.get_llm_usage.assert_not_awaited()
        state.db.get_active_session_info.return_value = {"session_id": "s1", "campaign": "K"}
        state.db.get_llm_usage.return_value = {"total_tokens": 42}
        body = c.get("/api/session/usage").json()
        assert body == {"total_tokens": 42, "session_id": "s1", "campaign": "K", "budget": 900}
        state.db.get_llm_usage.assert_awaited_with(session_id="s1")

    def test_usage_by_session_and_campaign(self, state, monkeypatch):
        monkeypatch.setattr(settings, "llm_token_budget", 5)
        c = _client(state)
        state.db.get_llm_usage.return_value = {"total_tokens": 1}
        assert c.get("/api/usage/session/abc").json() == {"total_tokens": 1, "session_id": "abc", "budget": 5}
        state.db.get_llm_usage.assert_awaited_with(session_id="abc")
        assert c.get("/api/usage/campaign/K").json()["campaign"] == "K"
        state.db.get_llm_usage.assert_awaited_with(campaign="K")

    def test_new_session_persists_row_and_sets_campaign(self, state, monkeypatch):
        monkeypatch.setattr(settings, "default_campaign", "Default")
        state.state_tracker = AsyncMock()
        c = _client(state)
        body = c.post("/api/session/new").json()
        assert body["campaign"] == "Default" and len(body["session_id"]) == 8
        state.db.create_session.assert_awaited_once_with(body["session_id"], "Default")
        state.state_tracker.set_campaign.assert_awaited_once_with("Default")
        assert c.post("/api/session/new", params={"campaign": "Other"}).json()["campaign"] == "Other"

    def test_events_scoped_to_active_session(self, state):
        c = _client(state)
        state.db.get_active_session_info.return_value = None
        assert c.get("/api/session/events").json() == []
        state.db.get_active_session_info.return_value = {"session_id": "s1", "campaign": None}
        state.db.get_events.return_value = [{"description": "d", "timestamp": "t"}]
        assert c.get("/api/session/events", params={"limit": 5}).json() == [
            {"description": "d", "timestamp": "t"}]
        state.db.get_events.assert_awaited_once_with("", session_id="s1", limit=5)


class TestChatTest:
    def _ready(self, state):
        state.chat_listener = object()
        state.llm_manager = AsyncMock()
        state.llm_manager.generate.return_value = {"actions": [{"type": "speak"}]}

    def test_not_ready_503(self, state):
        assert _client(state).post("/api/chat/test", json={"message": "hi"}).status_code == 503

    def test_missing_message_422(self, state):
        self._ready(state)
        assert _client(state).post("/api/chat/test", json={}).status_code == 422

    def test_passes_speaker_tagged_message_with_context(self, state):
        self._ready(state)
        state.state_tracker = MagicMock()
        state.state_tracker.get_snapshot.return_value = "SNAP"
        state.campaign_loader = AsyncMock()
        state.campaign_loader.get_npc_context.return_value = "NPCS"
        r = _client(state).post("/api/chat/test", json={"message": "hi", "speaker": "Ann"})
        assert r.json() == {"actions": [{"type": "speak"}]}
        state.llm_manager.generate.assert_awaited_once_with(
            user_message="[Ann]: hi", game_state_summary="SNAP", extra_context="NPCS")

    def test_generation_failure_reports_type_only(self, state):
        self._ready(state)
        state.llm_manager.generate.side_effect = TimeoutError("http://llm.internal")
        r = _client(state).post("/api/chat/test", json={"message": "hi"})
        assert r.status_code == 500 and r.json()["code"] == "CHAT_GENERATION_FAILED"
        assert "internal" not in r.text


class TestGMChat:
    def _ready(self, state):
        state.llm_manager = AsyncMock()
        state.llm_manager.generate_text.return_value = "Use place_walls."

    def test_503_without_llm(self, state):
        assert _client(state).post("/api/chat/gm", json={"message": "hi"}).status_code == 503

    def test_response_context_and_conversation_recording(self, state):
        self._ready(state)
        state.state_tracker = MagicMock()
        state.state_tracker.get_snapshot.return_value = "SNAP"
        state.campaign_loader = AsyncMock()
        state.campaign_loader.get_npc_context.return_value = "NPCS"
        state.foundry_client = AsyncMock()
        state.foundry_client.is_connected = True
        state.foundry_client.get_scene_details.return_value = {"data": {
            "name": "Crypt", "width": 4000, "height": 3000, "grid": {"size": 140},
            "walls": [1, 2], "lights": [1], "tokens": [1, 2, 3]}}
        state.db.get_active_session_info.return_value = {"session_id": "s1", "campaign": "K"}

        r = _client(state).post("/api/chat/gm", json={"message": "how do walls work"})
        assert r.json() == {"response": "Use place_walls."}
        kw = state.llm_manager.generate_text.await_args.kwargs
        assert kw["user_message"] == "how do walls work"
        assert "Active scene: Crypt (4000×3000px, grid=140px/sq) — 2 walls, 1 lights, 3 tokens" in kw["context"]
        assert "GAME STATE:\nSNAP" in kw["context"] and "NPC CONTEXT:\nNPCS" in kw["context"]
        saved = [c.args for c in state.db.save_conversation.await_args_list]
        assert saved == [("s1", "K", "user", "[GM Chat] how do walls work"),
                         ("s1", "K", "assistant", "Use place_walls.")]

    def test_flat_scene_details_use_gridsize(self, state):
        self._ready(state)
        state.foundry_client = AsyncMock()
        state.foundry_client.is_connected = True
        state.foundry_client.get_scene_details.return_value = {"name": "A", "gridSize": 70}
        _client(state).post("/api/chat/gm", json={"message": "x"})
        assert "grid=70px/sq" in state.llm_manager.generate_text.await_args.kwargs["context"]

    def test_scene_lookup_failure_is_non_fatal(self, state):
        self._ready(state)
        state.foundry_client = AsyncMock()
        state.foundry_client.is_connected = True
        state.foundry_client.get_scene_details.side_effect = RuntimeError("x")
        r = _client(state).post("/api/chat/gm", json={"message": "x"})
        assert r.json() == {"response": "Use place_walls."}
        assert state.llm_manager.generate_text.await_args.kwargs["context"] == ""

    def test_recording_failure_still_returns_the_answer(self, state):
        self._ready(state)
        state.db.get_active_session_info.return_value = {"session_id": "s", "campaign": None}
        state.db.save_conversation.side_effect = RuntimeError("disk")
        assert _client(state).post("/api/chat/gm", json={"message": "x"}).json() == {
            "response": "Use place_walls."}

    def test_no_active_session_records_nothing(self, state):
        self._ready(state)
        state.db.get_active_session_info.return_value = None
        _client(state).post("/api/chat/gm", json={"message": "x"})
        state.db.save_conversation.assert_not_awaited()

    def test_llm_failure_is_500_type_only(self, state):
        self._ready(state)
        state.llm_manager.generate_text.side_effect = ValueError("/secret/key")
        r = _client(state).post("/api/chat/gm", json={"message": "x"})
        assert r.status_code == 500 and r.json()["code"] == "GM_CHAT_FAILED" and "secret" not in r.text


class TestFoundryJs:
    """execute_js is arbitrary code execution: the gate must hold."""

    def _post(self, state, body):
        return _client(state).post("/api/foundry/js", json=body)

    def test_denied_when_gate_closed_and_never_reaches_foundry(self, state, monkeypatch):
        monkeypatch.setattr(settings, "allow_execute_js", False)
        state.foundry_client = AsyncMock()
        r = self._post(state, {"code": "game.world.delete()"})
        assert r.status_code == 403
        state.foundry_client.execute_js.assert_not_awaited()

    def test_allowed_path_validations(self, state, monkeypatch):
        monkeypatch.setattr(settings, "allow_execute_js", True)
        assert self._post(state, {"code": "1"}).status_code == 503
        state.foundry_client = AsyncMock()
        assert self._post(state, {"code": "   "}).status_code == 400
        assert self._post(state, {"code": "x" * 10001}).status_code == 400
        assert self._post(state, {}).status_code == 422
        state.foundry_client.execute_js.assert_not_awaited()

    def test_allowed_runs_code_and_hides_failures(self, state, monkeypatch):
        monkeypatch.setattr(settings, "allow_execute_js", True)
        state.foundry_client = AsyncMock()
        state.foundry_client.execute_js.return_value = 2
        assert self._post(state, {"code": "1+1"}).json() == {"status": "ok", "result": 2}
        state.foundry_client.execute_js.assert_awaited_once_with("1+1")
        state.foundry_client.execute_js.side_effect = RuntimeError("/etc/secret")
        r = self._post(state, {"code": "1+1"})
        assert r.status_code == 500 and "secret" not in r.text

    def test_boundary_length_is_accepted(self, state, monkeypatch):
        monkeypatch.setattr(settings, "allow_execute_js", True)
        state.foundry_client = AsyncMock()
        assert self._post(state, {"code": "x" * 10000}).status_code == 200


class TestRoll:
    def _state(self, state, result=None):
        state.foundry_client = AsyncMock()
        state.foundry_client.roll.return_value = result
        return state

    def test_forwards_formula_speaker_flavor(self, state):
        self._state(state, {"total": 14})
        r = _client(state).post("/api/roll", json={"formula": "2d6+3", "speaker": "Ann", "flavor": "hit"})
        assert r.json() == {"total": 14}
        state.foundry_client.roll.assert_awaited_once_with("2d6+3", speaker="Ann", flavor="hit")

    def test_defaults_and_empty_result(self, state):
        self._state(state)
        assert _client(state).post("/api/roll", json={}).json() == {"ok": True}
        state.foundry_client.roll.assert_awaited_once_with("1d20", speaker="GM", flavor="")

    @pytest.mark.parametrize("body", [
        {"formula": ""}, {"formula": "d" * 257}, {"speaker": "s" * 201},
        {"flavor": "f" * 501}, {"formula": "1d20", "extra": 1}])
    def test_bounds_and_extra_fields_rejected_before_foundry(self, state, body):
        self._state(state)
        assert _client(state).post("/api/roll", json=body).status_code == 422
        state.foundry_client.roll.assert_not_awaited()

    def test_failure_is_500_code(self, state):
        self._state(state)
        state.foundry_client.roll.side_effect = RuntimeError("x")
        r = _client(state).post("/api/roll", json={})
        assert r.status_code == 500 and r.json()["code"] == "DICE_ROLL_FAILED"
