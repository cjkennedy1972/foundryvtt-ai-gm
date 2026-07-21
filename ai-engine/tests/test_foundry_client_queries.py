"""Mock-based unit tests for FoundryClient's query/normalization layer.

The integration suite that exercises this client needs a live Dockerized
Foundry + relay, so it can't run in CI. These tests stub the transport
(`_send` / `_send_with_retry` / `execute_js`) and cover the parts that are
pure logic: response normalization, key fallbacks, and error degradation.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from foundry.client import FoundryClient


def _client(**stubs):
    """Build a client with async transport stubs patched in."""
    c = FoundryClient()
    for name, value in stubs.items():
        if callable(value):
            setattr(c, name, value)
        else:
            async def _const(*a, _v=value, **kw):
                return _v
            setattr(c, name, _const)
    return c


def _raiser(exc=ConnectionError("relay down")):
    async def _r(*a, **kw):
        raise exc
    return _r


# ── pure helpers ─────────────────────────────────────────────────────────────

def test_next_request_id_is_unique_and_increments():
    c = FoundryClient()
    a, b = c._next_request_id(), c._next_request_id()
    assert a != b
    assert a.startswith("gm-1-") and b.startswith("gm-2-")


def test_track_scene_ignores_empty():
    c = FoundryClient()
    c._track_scene("scene-1")
    assert c._current_scene_id == "scene-1"
    c._track_scene("")  # no-op, must not clear
    assert c._current_scene_id == "scene-1"


def test_speaker_name_override():
    c = FoundryClient()
    default = c._get_speaker_name()
    c.set_ai_name("Dungeon Master")
    assert c._get_speaker_name() == "Dungeon Master"
    assert default is not None


def test_is_connected_is_a_property_not_a_method():
    """is_connected is a @property — callers across the orchestrator read it as
    an attribute (`getattr(client, "is_connected", False)`). If it ever became a
    plain method those guards would silently read a truthy bound method and
    never fire."""
    c = FoundryClient()
    c._connected = False
    assert c.is_connected is False
    c._connected = True
    assert c.is_connected is True
    assert not callable(c.is_connected)


# ── get_scene_by_name ────────────────────────────────────────────────────────

def test_get_scene_by_name_unwraps_data_envelope():
    c = _client(_send={"type": "x", "data": {"name": "Crypt", "levels": []}})
    assert asyncio.run(c.get_scene_by_name("Crypt"))["name"] == "Crypt"


def test_get_scene_by_name_returns_bare_dict():
    c = _client(_send={"name": "Crypt"})
    assert asyncio.run(c.get_scene_by_name("Crypt")) == {"name": "Crypt"}


def test_get_scene_by_name_takes_first_of_list():
    c = _client(_send=[{"name": "First"}, {"name": "Second"}])
    assert asyncio.run(c.get_scene_by_name("x"))["name"] == "First"


def test_get_scene_by_name_none_for_unusable_payloads():
    assert asyncio.run(_client(_send=[]).get_scene_by_name("x")) is None
    assert asyncio.run(_client(_send="nonsense").get_scene_by_name("x")) is None


def test_get_scene_by_name_degrades_on_error():
    c = FoundryClient()
    c._send = _raiser()
    assert asyncio.run(c.get_scene_by_name("x")) is None


# ── get_actors ───────────────────────────────────────────────────────────────

def test_get_actors_normalizes_entries():
    c = _client(_send_with_retry={"result": [
        {"name": "Mara", "uuid": "Actor.1", "type": "character",
         "hasPlayerOwner": True, "hp": 20, "maxHp": 25},
    ]})
    actors = asyncio.run(c.get_actors())
    assert actors == [{"name": "Mara", "uuid": "Actor.1", "type": "character",
                       "has_player_owner": True, "hp": 20, "max_hp": 25}]


def test_get_actors_falls_back_to_id_and_defaults():
    c = _client(_send_with_retry={"result": [{"id": "abc"}]})
    a = asyncio.run(c.get_actors())[0]
    assert a["uuid"] == "abc" and a["name"] == "Unknown" and a["type"] == "unknown"
    assert a["has_player_owner"] is False and a["hp"] is None


def test_get_actors_empty_on_bad_shape_or_error():
    assert asyncio.run(_client(_send_with_retry={"result": "nope"}).get_actors()) == []
    assert asyncio.run(_client(_send_with_retry="not-a-dict").get_actors()) == []
    c = FoundryClient(); c._send_with_retry = _raiser()
    assert asyncio.run(c.get_actors()) == []


# ── get_player_actor_mapping ─────────────────────────────────────────────────

def test_player_mapping_indexes_names_and_uuids():
    c = _client(_send_with_retry={"result": [
        {"name": "Mara", "uuid": "Actor.1", "ownerId": "user-1"}]})
    m = asyncio.run(c.get_player_actor_mapping())
    assert m["actor_names"] == {"Mara": "user-1"}
    assert m["actor_uuids"] == {"Actor.1": "user-1"}


def test_player_mapping_skips_entries_without_owner():
    c = _client(_send_with_retry={"result": [{"name": "NoOwner", "uuid": "u", "ownerId": None}]})
    m = asyncio.run(c.get_player_actor_mapping())
    assert m == {"actor_names": {}, "actor_uuids": {}}


def test_player_mapping_degrades_on_error():
    c = FoundryClient(); c._send_with_retry = _raiser()
    assert asyncio.run(c.get_player_actor_mapping()) == {"actor_names": {}, "actor_uuids": {}}


# ── get_scenes ───────────────────────────────────────────────────────────────

def test_get_scenes_reads_list_payload():
    c = _client(_send_with_retry={"data": [
        {"name": "Crypt", "uuid": "Scene.1", "active": True}]})
    assert asyncio.run(c.get_scenes()) == [
        {"name": "Crypt", "uuid": "Scene.1", "token_count": {}, "active": True}]


def test_get_scenes_unwraps_nested_dict_payload():
    c = _client(_send_with_retry={"data": {"scenes": [{"title": "Alt", "id": "S2"}]}})
    s = asyncio.run(c.get_scenes())[0]
    assert s["name"] == "Alt" and s["uuid"] == "S2" and s["active"] is False


def test_get_scenes_degrades_on_error():
    c = FoundryClient(); c._send_with_retry = _raiser()
    assert asyncio.run(c.get_scenes()) == []


# ── active-scene resolution ──────────────────────────────────────────────────

def test_active_scene_name_returns_string():
    assert asyncio.run(_client(execute_js={"result": "Crypt"})._get_active_scene_name()) == "Crypt"


def test_active_scene_name_none_when_absent_or_blank():
    assert asyncio.run(_client(execute_js={"result": None})._get_active_scene_name()) is None
    assert asyncio.run(_client(execute_js={"result": ""})._get_active_scene_name()) is None
    assert asyncio.run(_client(execute_js="bad")._get_active_scene_name()) is None


def test_active_scene_name_none_on_error():
    c = FoundryClient(); c.execute_js = _raiser()
    assert asyncio.run(c._get_active_scene_name()) is None


def test_list_scene_names_filters_blanks():
    c = _client(execute_js={"result": ["A", "", None, "B"]})
    assert asyncio.run(c.list_scene_names()) == ["A", "B"]


def test_list_scene_names_empty_on_bad_shape_or_error():
    assert asyncio.run(_client(execute_js={"result": "x"}).list_scene_names()) == []
    c = FoundryClient(); c.execute_js = _raiser()
    assert asyncio.run(c.list_scene_names()) == []


# ── get_scene_details ────────────────────────────────────────────────────────

def test_scene_details_uses_explicit_name():
    seen = {}

    async def _send(msg_type, **params):
        seen.update(params)
        return {"name": params.get("name")}

    c = _client(_send=_send)
    assert asyncio.run(c.get_scene_details("Crypt"))["name"] == "Crypt"
    assert seen["name"] == "Crypt"


def test_scene_details_defaults_to_active_scene():
    """Omitting the name must resolve the active scene — the relay has no
    default and would otherwise return 'Scene not found'."""
    c = _client(execute_js={"result": "Active One"}, _send={"name": "Active One"})
    assert asyncio.run(c.get_scene_details())["name"] == "Active One"


def test_scene_details_empty_when_no_active_scene():
    c = _client(execute_js={"result": None})
    assert asyncio.run(c.get_scene_details()) == {}


def test_scene_details_degrades_on_error():
    c = FoundryClient(); c._send = _raiser(); c.execute_js = _raiser()
    assert asyncio.run(c.get_scene_details("X")) == {}


# ── get_scene_tokens ─────────────────────────────────────────────────────────

def test_scene_tokens_normalizes_top_level_tokens():
    c = _client(_send={"tokens": [
        {"name": "Goblin", "x": 5, "y": 6, "actorId": "Actor.9", "_id": "tok1",
         "disposition": -1}]})
    t = asyncio.run(c.get_scene_tokens("S"))[0]
    assert t["name"] == "Goblin" and t["x"] == 5 and t["y"] == 6
    # actorId is the Foundry key; it must map onto actorUuid for resolution
    assert t["actorUuid"] == "Actor.9"
    assert t["id"] == "tok1"
    assert t["disposition"] == -1


def test_scene_tokens_reads_nested_data_tokens():
    c = _client(_send={"data": {"tokens": [{"name": "Rat"}]}})
    assert asyncio.run(c.get_scene_tokens("S"))[0]["name"] == "Rat"


def test_scene_tokens_absent_disposition_stays_none():
    """Defaulting disposition to friendly previously made hostiles look like
    PCs and stalled the combat loop."""
    c = _client(_send={"tokens": [{"name": "Unknown"}]})
    assert asyncio.run(c.get_scene_tokens("S"))[0]["disposition"] is None


def test_scene_tokens_empty_cases():
    assert asyncio.run(_client(_send={}).get_scene_tokens("S")) == []
    assert asyncio.run(_client(_send={"tokens": []}).get_scene_tokens("S")) == []
    assert asyncio.run(_client(_send={"data": None}).get_scene_tokens("S")) == []


# ── set_active_scene ─────────────────────────────────────────────────────────

def test_set_active_scene_returns_resolved_result():
    c = _client(execute_js={"result": {"ok": True, "name": "The Crypt", "placedPCs": 2}})
    out = asyncio.run(c.set_active_scene("Crypt"))  # article dropped by LLM
    assert out["ok"] is True and out["name"] == "The Crypt" and out["placedPCs"] == 2


def test_set_active_scene_falls_back_when_not_matched():
    calls = []

    async def _send(msg_type, **params):
        calls.append(msg_type)
        return {"fallback": True}

    c = _client(execute_js={"result": {"ok": False, "error": "Scene not found",
                                       "available": ["A"]}}, _send=_send)
    assert asyncio.run(c.set_active_scene("Nope"))["fallback"] is True
    assert calls == ["switch-scene"]


def test_set_active_scene_falls_back_on_execute_js_error():
    calls = []

    async def _send(msg_type, **params):
        calls.append(msg_type)
        return {"fallback": True}

    c = FoundryClient()
    c.execute_js = _raiser()
    c._send = _send
    assert asyncio.run(c.set_active_scene("X"))["fallback"] is True
    assert calls == ["switch-scene"]


# ── thin command wrappers (parameter mapping) ────────────────────────────────

def test_chat_message_passes_content_and_defaults_whisper():
    seen = {}

    async def _swr(msg_type, max_retries=2, **params):
        seen.update({"type": msg_type, **params})
        return {}

    c = _client(_send_with_retry=_swr)
    asyncio.run(c.chat_message("hello"))
    assert seen["type"] == "chat-send" and seen["content"] == "hello"
    assert seen["whisper"] == []


def test_roll_requests_real_chat_message():
    """createChatMessage=True is what makes Dice So Nice animate the roll."""
    seen = {}

    async def _send(msg_type, **params):
        seen.update({"type": msg_type, **params})
        return {}

    c = _client(_send=_send)
    asyncio.run(c.roll("1d20+5", flavor="Perception"))
    assert seen["type"] == "roll" and seen["formula"] == "1d20+5"
    assert seen["createChatMessage"] is True
    assert seen["flavor"] == "Perception"
