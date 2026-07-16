"""Compatibility smoke tests for the supported Python/Pydantic stack."""

from config import Settings
from actions.schemas import NarrateAction
from api.deps import ErrorResponse
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


def test_settings_uses_single_optional_admin_token():
    """The old two-token/role system is gone; LAN protection is one optional token."""
    assert "admin_host" in Settings.model_fields
    assert "admin_token" in Settings.model_fields
    assert Settings.model_fields["admin_host"].default == "127.0.0.1"
    assert Settings.model_fields["admin_token"].default == ""
    assert "api_auth_required" not in Settings.model_fields
    assert "gm_api_token" not in Settings.model_fields
    assert "player_api_token" not in Settings.model_fields
