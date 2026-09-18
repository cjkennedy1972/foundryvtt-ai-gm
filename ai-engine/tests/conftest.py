"""Suite-wide fixtures, plus the player-turn action policy shared by tests.

RelayManager derives data_dir (and _credentials_path inside it) from
settings.relay_data_dir, falling back to <repo>/data/relay. Tests that
construct RelayManager() without redirecting it ran _save_credentials,
_reap_stale_profiles and _clear_chrome_locks against the developer's live
relay directory, overwriting aigm-credentials.json on every pytest run.
Redirecting the setting covers every construction site at once, including
ones added later.
"""

from typing import get_args, get_origin

import pytest
from pydantic import BaseModel

from config import settings


@pytest.fixture(autouse=True)
def isolate_relay_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "relay_data_dir", str(tmp_path / "relay"))


@pytest.fixture(autouse=True)
def restore_mutated_settings():
    """Undo runtime writes to the process-global settings object.

    Several routes assign to settings at runtime — api/routes/campaign.py sets
    relay_headless_client_id after launching a headless session, and
    api/routes/session.py updates model and temperature. A test that drives
    one of those leaves the value behind for every test after it, which is how
    a campaign-route test broke two FoundryClient.connect tests: the route
    stored a MagicMock in relay_headless_client_id and connect() put it in the
    auth frame.
    """
    before = {name: getattr(settings, name) for name in settings.model_fields}
    yield
    for name, value in before.items():
        if getattr(settings, name) is not value:
            setattr(settings, name, value)


# ---------------------------------------------------------------------------
# Player-turn action policy
#
# Action types a player message must never reach, whatever the model emits.
# Pinned here so a change to the production policy has to be deliberate.
# Shared by test_dispatcher_player_gate.py and test_player_turn_provenance.py:
# each used to hand-maintain its own copy, which is why turning the gate on
# broke them in two different ways.
# ---------------------------------------------------------------------------

# The production policy, re-stated here so a change to it has to be deliberate
# in two places. Deliberately short: see the rationale on PLAYER_ALLOWED_ACTIONS
# in actions/schemas.py for why a broad allowlist cannot work in a system whose
# job is to reshape the world from player text.
BLOCKED_FROM_PLAYER_TURNS = {
    "execute_js",
    "execute_macro",
    "pause_game",
    "resume_game",
}

# Schemas with model-level validators that field-by-field synthesis cannot
# satisfy (e.g. "one of these two optional fields is required").
_PAYLOAD_OVERRIDES = {
    "place_token": {"actor_name": "placeholder", "x": 1.0, "y": 1.0},
}


def _scalar(annotation):
    text = str(annotation)
    if "dict" in text or "Dict" in text:
        return {}
    if "bool" in text:
        return False
    if "int" in text:
        return 1
    if "float" in text:
        return 1.0
    # Long enough for the min_length constraints several schemas carry.
    return "placeholder"


def _fill(model):
    """Smallest body satisfying a Pydantic model's required fields.

    Recurses into nested models and list items, because several schemas
    constrain lists to min_length=1 (PlaceWallsAction.walls and friends).
    """
    body = {}
    for name, field in model.model_fields.items():
        if not field.is_required():
            continue
        annotation = field.annotation
        origin = get_origin(annotation)
        if origin in (list, set, tuple):
            item_type = (get_args(annotation) or (str,))[0]
            if isinstance(item_type, type) and issubclass(item_type, BaseModel):
                body[name] = [_fill(item_type)]
            else:
                body[name] = [_scalar(item_type)]
        elif origin is dict:
            body[name] = {}
        elif isinstance(annotation, type) and issubclass(annotation, BaseModel):
            body[name] = _fill(annotation)
        else:
            body[name] = _scalar(annotation)
    return body


def minimal_action_payload(action_type):
    """Schema-valid body for an action type, with its discriminator.

    The dispatcher validates against the Pydantic schema BEFORE it checks
    provenance (validation, then the PLAYER_ALLOWED_ACTIONS gate), so a body
    missing required fields fails validation and never exercises the gate.
    """
    from actions.schemas import ACTION_SCHEMAS

    if action_type in _PAYLOAD_OVERRIDES:
        return {"type": action_type, **_PAYLOAD_OVERRIDES[action_type]}
    return {"type": action_type, **_fill(ACTION_SCHEMAS[action_type])}
