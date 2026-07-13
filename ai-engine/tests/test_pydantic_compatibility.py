"""Compatibility smoke tests for the supported Python/Pydantic stack."""

from config import Settings
from actions.schemas import NarrateAction
from api.deps import ErrorResponse
from api.deps import (
    _token_role,
    authenticate_websocket_token,
    player_path_allowed,
)
from state.models import GameState


def test_settings_constructs_and_parses_typed_defaults():
    settings = Settings(model="compat-test", comfyui_input_dirs=["/tmp/input"])
    assert settings.model == "compat-test"
    assert settings.comfyui_input_dirs == ["/tmp/input"]


def test_core_models_keep_v2_serialization_contract():
    state = GameState()
    error = ErrorResponse(error="bad request")
    assert state.model_dump()["mode"] == "exploration"
    assert error.model_dump() == {
        "status": "error",
        "error": "bad request",
        "code": None,
        "details": None,
    }


def test_action_schema_rejects_unknown_fields():
    try:
        NarrateAction(text="hello", unexpected=True)
    except Exception as exc:
        assert "unexpected" in str(exc)
    else:
        raise AssertionError("extra action fields must remain rejected")


def test_player_api_is_explicit_allowlist():
    assert player_path_allowed("GET", "/api/rules/spell")
    assert player_path_allowed("GET", "/api/procedural/npc")
    assert not player_path_allowed("GET", "/api/status")
    assert not player_path_allowed("GET", "/api/npc_context")
    assert not player_path_allowed("POST", "/api/rules/spell")


def test_api_tokens_have_distinct_roles(monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "api_auth_required", True)
    monkeypatch.setattr(settings, "gm_api_token", "gm-secret")
    monkeypatch.setattr(settings, "player_api_token", "player-secret")

    assert _token_role("gm-secret") == "admin"
    assert _token_role("player-secret") == "player"
    assert _token_role("wrong") is None
    assert authenticate_websocket_token("gm-secret") == "admin"
    assert authenticate_websocket_token("player-secret") == "player"


def test_auth_disabled_is_explicit_local_admin_mode(monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "api_auth_required", False)
    monkeypatch.setattr(settings, "gm_api_token", "")
    assert _token_role(None) == "admin"
    assert authenticate_websocket_token(None) == "admin"
