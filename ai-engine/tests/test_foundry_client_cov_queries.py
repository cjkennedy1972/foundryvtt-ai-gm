"""FoundryClient chat/roll/scene/actor/query methods: message routing and degradation."""
import asyncio

import pytest

import foundry.client as client_mod
from test_foundry_client_cov_common import client_with_pw
from foundry.client import FoundryClient


def run(coro):
    return asyncio.run(coro)


def js(result):
    return {"result": result}


# ── _cli_* helpers ───────────────────────────────────────────────────────────


# ── chat / roll / structure ──────────────────────────────────────────────────


def test_roll_creates_real_chat_message():
    c = client_with_pw({"roll": {"total": 9}})
    assert run(c.roll("1d20+3", speaker="Bob", flavor="Stealth")) == {"total": 9}
    assert c._pw.sent == [("roll", {"formula": "1d20+3", "speaker": "Bob", "flavor": "Stealth",
                                    "createChatMessage": True})]


def test_get_structure_sends_structure():
    c = client_with_pw({"structure": {"data": {"folders": {}}}})
    assert run(c.get_structure()) == {"data": {"folders": {}}}
    assert c._pw.sent == [("structure", {})]


# ── update_scene ─────────────────────────────────────────────────────────────


# ── update_actor ─────────────────────────────────────────────────────────────

def _actor_client(*, scenes=(), tokens=None, search=None, world=(), allactors=(), update=None):
    c = FoundryClient()
    calls = {"update": [], "actors": []}

    async def get_scenes():
        if isinstance(scenes, Exception):
            raise scenes
        return list(scenes)

    async def get_scene_tokens(name):
        return (tokens or {}).get(name, [])

    async def send(msg, **kw):
        if isinstance(search, Exception):
            raise search
        return search if search is not None else {}

    async def get_actors(world_only=False, strict=False):
        calls["actors"].append(world_only)
        return list(world if world_only else allactors)

    async def update_entity(uuid=None, data=None, token_id=None):
        calls["update"].append((uuid, data))
        return {"updated": uuid}

    c.get_scenes, c.get_scene_tokens, c._send = get_scenes, get_scene_tokens, send
    c.get_actors, c.update_entity = get_actors, update_entity
    return c, calls


def test_update_actor_prefers_scene_token_match_case_insensitively():
    c, calls = _actor_client(
        scenes=[{"name": "A"}, {"name": "B"}],
        tokens={"A": [{"name": "Other", "actorUuid": "Actor.0"}],
                "B": [{"name": "GOBLIN", "actorUuid": "Actor.g1"}]},
    )
    assert run(c.update_actor("goblin", {"img": "x.png"})) == {"updated": "Actor.g1"}
    assert calls["update"] == [("Actor.g1", {"img": "x.png"})]
    assert calls["actors"] == []  # never needed the fallbacks


def test_update_actor_scene_search_failure_falls_through_to_direct_search():
    c, calls = _actor_client(
        scenes=RuntimeError("no scenes"),
        search={"results": [{"documentType": "Item", "name": "Bob", "uuid": "Item.1"},
                            {"documentType": "Actor", "name": "bob", "uuid": "Actor.b"}]},
    )
    assert run(c.update_actor("Bob", {"a": 1})) == {"updated": "Actor.b"}


def test_update_actor_direct_search_error_then_world_actors():
    c, calls = _actor_client(
        search=RuntimeError("search down"),
        world=[{"name": "Zed", "uuid": "Actor.z"}],
    )
    assert run(c.update_actor("zed", {"a": 1})) == {"updated": "Actor.z"}
    assert calls["actors"] == [True]


def test_update_actor_non_list_search_results_are_ignored():
    c, calls = _actor_client(search={"results": "nope"}, world=[{"name": "Zed", "uuid": "Actor.z"}])
    assert run(c.update_actor("Zed", {})) == {"updated": "Actor.z"}


def test_update_actor_falls_back_to_all_actors():
    c, calls = _actor_client(allactors=[{"name": "Lich", "uuid": "Compendium.l"}])
    assert run(c.update_actor("lich", {"a": 1})) == {"updated": "Compendium.l"}
    assert calls["actors"] == [True, False]


def test_update_actor_returns_none_when_nowhere_found():
    c, calls = _actor_client(allactors=[{"name": "Other", "uuid": "Actor.o"}])
    assert run(c.update_actor("Ghost", {"a": 1})) is None
    assert calls["update"] == []


# ── upload_file ──────────────────────────────────────────────────────────────


def test_upload_file_relay_sends_data_url_and_unwraps_data():
    c = client_with_pw({"upload-file": {"data": {"path": "uploads/x.png"}}})
    out = run(c.upload_file(b"abc", "uploads", "x.png", mime_type="image/webp", source="public", overwrite=False))
    assert out == {"path": "uploads/x.png"}
    msg, params = c._pw.sent[0]
    assert msg == "upload-file"
    assert params["fileData"] == "data:image/webp;base64,YWJj"
    assert (params["path"], params["filename"], params["source"], params["mimeType"], params["overwrite"]) == (
        "uploads", "x.png", "public", "image/webp", False)
    assert "_timeout" not in params  # consumed by _send, not forwarded to the transport


def test_upload_file_uses_canvas_timeout_and_returns_unwrapped_reply():
    seen = {}
    c = FoundryClient()

    async def send(msg, **kw):
        seen.update(kw)
        return {"path": "bare"}

    c._send = send
    assert run(c.upload_file(b"a", "p", "f")) == {"path": "bare"}  # no "data" key: whole reply
    assert seen["_timeout"] == client_mod.settings.relay_rpc_timeout_canvas


def test_search_sends_query():
    c = client_with_pw({"search": {"results": []}})
    assert run(c.search("orc")) == {"results": []}
    assert c._pw.sent == [("search", {"query": "orc"})]


# ── get_actors ───────────────────────────────────────────────────────────────


def test_get_actors_normalises_execute_js_rows():
    c = client_with_pw({"execute-js": js([
        {"id": "i1", "name": "Hero", "type": "character", "hasPlayerOwner": True, "hp": 5, "maxHp": 9},
        {},
    ])})
    assert run(c.get_actors()) == [
        {"name": "Hero", "uuid": "i1", "type": "character", "has_player_owner": True, "hp": 5, "max_hp": 9},
        {"name": "Unknown", "uuid": "", "type": "unknown", "has_player_owner": False, "hp": None, "max_hp": None},
    ]


def test_get_actors_non_list_result_is_empty():
    c = client_with_pw({"execute-js": js("garbage")})
    assert run(c.get_actors()) == []


def test_get_actors_lenient_vs_strict_on_transport_failure():
    c = client_with_pw({"execute-js": RuntimeError("relay blip")})
    assert run(c.get_actors()) == []
    with pytest.raises(RuntimeError, match="relay blip"):
        run(c.get_actors(strict=True))


def test_get_actors_retries_timeouts(monkeypatch):
    async def fake_sleep(d):
        pass

    monkeypatch.setattr(client_mod.asyncio, "sleep", fake_sleep)
    n = []

    async def flaky(**kw):
        n.append(1)
        if len(n) < 3:
            raise asyncio.TimeoutError()
        return js([{"name": "A", "id": "1"}])

    c = client_with_pw({"execute-js": flaky})
    assert [a["name"] for a in run(c.get_actors())] == ["A"]
    assert len(n) == 3  # max_retries=3 used


# ── get_player_actor_mapping ─────────────────────────────────────────────────


def test_player_actor_mapping_skips_rows_without_owner_or_name():
    c = client_with_pw({"execute-js": js([
        {"name": "A", "uuid": "Actor.a", "ownerId": "u1"},
        {"name": "", "uuid": "Actor.b", "ownerId": "u2"},
        {"name": "C", "uuid": "", "ownerId": "u3"},
        {"name": "D", "uuid": "Actor.d", "ownerId": None},
    ])})
    assert run(c.get_player_actor_mapping()) == {
        "actor_names": {"A": "u1", "C": "u3"},
        "actor_uuids": {"Actor.a": "u1", "Actor.b": "u2"},
    }


def test_player_actor_mapping_non_list_and_error_degrade_to_empty():
    empty = {"actor_names": {}, "actor_uuids": {}}
    assert run(client_with_pw({"execute-js": js(None)}).get_player_actor_mapping()) == empty
    assert run(client_with_pw({"execute-js": RuntimeError("x")}).get_player_actor_mapping()) == empty


# ── world metadata / documents / scenes ──────────────────────────────────────


def test_search_documents_unwraps_dict_to_first_list_and_filters_by_type():
    c = client_with_pw({"search": {"data": {"count": 2, "scenes": [{"name": "A"}]}}})
    assert run(c._search_documents("Scene")) == [{"name": "A"}]
    assert c._pw.sent == [("search", {"filter": "documentType:Scene"})]


def test_search_documents_falls_back_to_results_key_and_rejects_non_lists():
    assert run(client_with_pw({"search": {"results": [{"name": "r"}]}})._search_documents("Item")) == [{"name": "r"}]
    assert run(client_with_pw({"search": {"data": {"n": 1}}})._search_documents("Item")) == []
    assert run(client_with_pw({"search": {"data": "str"}})._search_documents("Item")) == []


def test_get_scenes_normalises_and_degrades_on_error():
    c = client_with_pw({"search": {"data": [
        {"title": "T", "id": "i", "tokens": 3, "active": True}, {}]}})
    assert run(c.get_scenes()) == [
        {"name": "T", "uuid": "i", "token_count": 3, "active": True},
        {"name": "Unknown", "uuid": "", "token_count": {}, "active": False},
    ]
    assert run(client_with_pw({"search": RuntimeError("x")}).get_scenes()) == []


# ── get_scene_details / active scene / names ─────────────────────────────────


def test_get_scene_details_no_active_scene_returns_empty_without_relay_call():
    c = client_with_pw({"execute-js": js(None)})
    assert run(c.get_scene_details()) == {}
    assert [m for m, _ in c._pw.sent] == ["execute-js"]


def test_get_scene_details_error_returns_empty():
    c = client_with_pw({"get-scene": RuntimeError("x")})
    assert run(c.get_scene_details("Crypt")) == {}


@pytest.mark.parametrize("reply,expected", [(js("Crypt"), "Crypt"), (js(""), None), (js(5), None), ({}, None)])
def test_active_scene_name_via_js_accepts_only_non_empty_strings(reply, expected):
    assert run(client_with_pw({"execute-js": reply})._get_active_scene_name()) == expected


def test_active_scene_name_and_id_swallow_transport_errors():
    c = client_with_pw({"execute-js": RuntimeError("x")})
    assert run(c._get_active_scene_name()) is None
    assert run(c.get_active_scene_id()) is None


@pytest.mark.parametrize("reply,expected", [(js("abc"), "abc"), (js(""), None), (js(1), None)])
def test_active_scene_id_via_js(reply, expected):
    assert run(client_with_pw({"execute-js": reply}).get_active_scene_id()) == expected


# ── get_scene_tokens ─────────────────────────────────────────────────────────


def test_scene_tokens_normalised_from_relay_details_with_fallback_keys():
    c = client_with_pw({"get-scene": {"data": {"tokens": [
        {"tname": "Orc", "position": {"x": 5, "y": 6}, "scale": {"x": 2, "y": 3},
         "actorId": "a1", "_id": "t1", "disposition": -1},
        {"name": "Elf", "x": 1, "y": 2, "width": 4, "height": 5, "actorUuid": "Actor.e", "id": "t2",
         "emitter": 7, "brightness": 0.5},
    ]}}})
    assert run(c.get_scene_tokens("Crypt")) == [
        {"name": "Orc", "x": 5, "y": 6, "width": 2, "height": 3, "actorUuid": "a1", "id": "t1",
         "emitter": 0, "brightness": 1, "disposition": -1},
        {"name": "Elf", "x": 1, "y": 2, "width": 4, "height": 5, "actorUuid": "Actor.e", "id": "t2",
         "emitter": 7, "brightness": 0.5, "disposition": None},
    ]


def test_scene_tokens_top_level_tokens_key_wins_and_empty_cases():
    c = client_with_pw({"get-scene": {"tokens": [{"name": "A", "x": 0, "y": 0}]}})
    assert [t["name"] for t in run(c.get_scene_tokens("S"))] == ["A"]
    assert run(client_with_pw({"get-scene": {}}).get_scene_tokens("S")) == []                 # no details
    assert run(client_with_pw({"get-scene": {"data": None}}).get_scene_tokens("S")) == []     # data None
    assert run(client_with_pw({"get-scene": {"data": "str"}}).get_scene_tokens("S")) == []    # data not dict
    assert run(client_with_pw({"get-scene": {"data": {"tokens": []}}}).get_scene_tokens("S")) == []


