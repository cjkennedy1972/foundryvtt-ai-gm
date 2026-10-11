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

from api.routes.session import GMSettings, get_settings, update_settings
from config import settings

PANEL_SRC = Path(__file__).resolve().parents[1] / "admin-panel" / "src"
STORE_JS = PANEL_SRC / "store.js"
CONFIG_JS = PANEL_SRC / "config.js"


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








def _panel_secret_keys() -> list[str]:
    """The fields config.js tells the panel to treat as secrets."""
    match = re.search(r"SECRET_KEYS\s*=\s*\[([^\]]*)\]", CONFIG_JS.read_text())
    assert match, f"could not find SECRET_KEYS in {CONFIG_JS}"
    return re.findall(r"['\"](\w+)['\"]", match.group(1))


def test_every_secret_the_panel_masks_has_a_presence_flag_on_the_server():
    """The panel shows "a key is already set" from `<name>_set`.

    The server never returns a key, so that flag is the only way the panel can
    know one exists. A secret listed in config.js with no flag here would be
    masked on the panel's side and never reported on the server's.
    """
    secrets = _panel_secret_keys()
    assert secrets, "parsed no secrets from config.js; the test below would be vacuous"

    missing = [f"{name}_set" for name in secrets if f"{name}_set" not in GMSettings.model_fields]
    assert not missing, f"GMSettings does not declare {missing}"


@pytest.mark.asyncio
async def test_get_settings_reports_that_a_key_is_set_without_returning_it():
    original = settings.llm_api_key
    try:
        settings.llm_api_key = "sk-very-secret"

        response = await get_settings(_state())

        assert response.llm_api_key_set is True
        # The property that matters: presence is reported, the value never is.
        assert response.llm_api_key == ""
        assert "sk-very-secret" not in response.model_dump_json()
    finally:
        settings.llm_api_key = original






