"""The admin panel's settings payload has to be one the API accepts.

Both sides of this boundary were individually correct and individually
tested while five fields silently did nothing: the store POSTs its own
`settings` object verbatim, pydantic dropped the keys it did not recognise,
and every unrecognised field fell back to a default that was the current
server value — so the handler assigned each one to itself and returned 200.

These tests read the key list out of store.js rather than restating it, so
renaming a field on one side and not the other fails here.
"""

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from api.routes.session import GMSettings, update_settings
from config import settings

STORE_JS = Path(__file__).resolve().parents[1] / "admin-panel" / "src" / "store.js"


def _store_settings_keys() -> list[str]:
    """The keys of the `settings` object literal in store.js.

    Read from the source because that object is what saveSettings() posts;
    a hand-copied list here would drift the same way the two sides did.
    """
    source = STORE_JS.read_text()
    opener = "\n    settings: {"
    start = source.index(opener) + len(opener)
    body = source[start:]
    return re.findall(r"^\s+(\w+):", body[: body.index("\n    },")], re.MULTILINE)


def _state():
    return SimpleNamespace(llm_manager=None, foundry_client=None, token_usage=None)


def test_store_js_exposes_a_settings_object_we_can_read():
    """Guards the parser: a silent [] would make every test below vacuous."""
    keys = _store_settings_keys()

    assert len(keys) >= 8, f"parsed too few keys from {STORE_JS}: {keys}"
    assert "model" in keys and "temperature" in keys
    # The declaration line itself is not one of the payload's keys.
    assert "settings" not in keys


def test_every_field_the_panel_sends_is_one_the_api_declares():
    """An unknown key is dropped in silence, so the field never saves."""
    unknown = set(_store_settings_keys()) - set(GMSettings.model_fields)

    assert not unknown, (
        f"store.js posts {sorted(unknown)}, which GMSettings does not declare. "
        "pydantic ignores extra keys, so those fields are discarded on save."
    )


@pytest.mark.asyncio
async def test_the_panels_own_payload_actually_changes_the_settings():
    """End to end over the real payload shape, not a hand-written one."""
    keys = _store_settings_keys()
    # Values distinct from whatever the server currently holds, so a field
    # that fails to apply cannot coincidentally match.
    wanted = {
        "model": "boundary-model",
        "temperature": 0.11,
        "ai_name": "Boundary GM",
        "ai_tone": "clipped and exact",
        "comfyui_url": "http://comfy.boundary:18188",
        "llm_token_budget": 4096,
    }
    payload = {k: wanted[k] for k in keys if k in wanted}
    assert set(payload) == set(wanted), (
        f"store.js no longer sends all of {sorted(wanted)}; sends {sorted(keys)}"
    )

    originals = {k: getattr(settings, k) for k in wanted}
    try:
        # Parse the JSON the panel would actually put on the wire.
        await update_settings(GMSettings(**json.loads(json.dumps(payload))), _state())

        for field, value in wanted.items():
            assert getattr(settings, field) == value, (
                f"settings.{field} stayed {getattr(settings, field)!r}; "
                f"the panel asked for {value!r}"
            )
    finally:
        for field, value in originals.items():
            setattr(settings, field, value)


@pytest.mark.asyncio
async def test_the_masked_secret_the_panel_echoes_back_cannot_overwrite_a_key():
    """The panel posts its whole settings object, mask included.

    `fetchSettings` puts a '••••••••' sentinel in state when a key is set, and
    `saveSettings` posts state verbatim, so the sentinel goes back to the
    server on every save. `update_settings` must not store it as the key.
    """
    original = settings.relay_api_key
    try:
        await update_settings(
            GMSettings(relay_api_key="••••••••", model=settings.model), _state()
        )

        assert settings.relay_api_key == original, (
            "the display mask was stored as the relay API key"
        )
    finally:
        settings.relay_api_key = original


@pytest.mark.asyncio
async def test_an_unchanged_relay_url_does_not_block_the_rest_of_the_save():
    """The ordinary case: the operator edits one field and posts them all.

    relay_url is a restart-only field, and the panel sends it on every save
    because it sends the whole object. Sending it back unchanged has to be
    accepted, or no settings change would ever apply.
    """
    original_name = settings.ai_name
    try:
        await update_settings(
            GMSettings(ai_name="Boundary GM", relay_url=settings.relay_url), _state()
        )

        assert settings.ai_name == "Boundary GM"
    finally:
        settings.ai_name = original_name


@pytest.mark.asyncio
async def test_changing_the_relay_url_is_refused_rather_than_silently_ignored():
    """Editing a restart-only field now reaches the server and is rejected.

    Before the store's keys matched the wire format this field was dropped in
    transit, so the panel reported success and changed nothing. An explicit
    400 naming the restart requirement is the honest outcome.
    """
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as excinfo:
        await update_settings(GMSettings(relay_url="http://elsewhere:9999"), _state())

    assert excinfo.value.status_code == 400
    assert "relay_url" in excinfo.value.detail
    assert "restart" in excinfo.value.detail.lower()
