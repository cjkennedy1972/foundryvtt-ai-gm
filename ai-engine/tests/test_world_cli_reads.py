"""World CLI read adapters, the router's fallback rules, and FoundryClient's use of them.

The response shapes below were captured from a live Foundry v14 session (the differential test
that compares each adapter with the relay/execute_js path it replaces). The adapters must return
what the old methods returned; the router must fall back to the old path whenever World CLI cannot
answer, for any reason.
"""

import pytest

from foundry import world_cli_reads as reads
from foundry.client import FoundryClient
from foundry.world_cli import WorldCLIError
from foundry.world_cli_router import TRIP_SECONDS, WorldCLIRouter

GM, P1, P2 = "gm0", "pl1", "pl2"
USERS = {"users": [{"id": GM, "name": "Gamemaster", "role": 4, "isGM": True, "active": True, "color": "#111", "avatar": None, "character": None},
                   {"id": P1, "name": "Ann", "role": 1, "isGM": False, "active": False, "color": "#222", "avatar": "a.png", "character": None},
                   {"id": P2, "name": "Bo", "role": 1, "isGM": False, "active": False, "color": "#333", "avatar": None, "character": None}],
         "total": 3, "hasMore": False}
ACTORS = {
    "g1": {"id": "g1", "name": "Goblin", "type": "npc", "ownership": {"default": 0, GM: 3}, "system": {"attributes": {"hp": {"value": 7, "max": 10}}}},
    "h1": {"id": "h1", "name": "Hero", "type": "character", "ownership": {"default": 0, GM: 3, P1: 3}, "system": {"attributes": {"hp": {"value": 20, "max": 23}}}},
    "v1": {"id": "v1", "name": "Vase", "type": "npc", "ownership": {"default": 3}, "system": {}},
    "o1": {"id": "o1", "name": "Observer", "type": "character", "ownership": {"default": 0, P2: 2}, "system": {"attributes": {"hp": {"value": 5}}}},
}


class FakeCLI:
    def __init__(self, **overrides):
        self.calls = []
        self.overrides = overrides

    async def call(self, command, params=None, **kw):
        params = params or {}
        self.calls.append((command, params))
        if command in self.overrides:
            out = self.overrides[command]
            if isinstance(out, Exception):
                raise out
            return out(params) if callable(out) else out
        if command == "user.list":
            return USERS
        if command == "actor.list":
            rows = [{"id": i, "name": a["name"]} for i, a in ACTORS.items()]
            return {"actors": rows[params.get("offset", 0):][:params.get("limit", 100)], "total": len(rows), "hasMore": False}
        if command == "actor.get-many":
            return {"actors": [ACTORS[i] for i in params["ids"]]}
        raise AssertionError(f"unexpected command {command}")


@pytest.mark.asyncio
async def test_actors_come_back_in_the_old_shape_with_has_player_owner_recomputed():
    out = {a["name"]: a for a in await reads.actors(FakeCLI())}
    assert out["Goblin"] == {"name": "Goblin", "uuid": "Actor.g1", "type": "npc", "has_player_owner": False, "hp": 7, "max_hp": 10}
    assert out["Hero"]["has_player_owner"] is True and out["Hero"]["uuid"] == "Actor.h1"
    assert out["Vase"]["has_player_owner"] is True        # world-wide OWNER default, and players exist: Foundry says yes
    assert out["Observer"]["has_player_owner"] is False   # OBSERVER is not OWNER
    assert out["Vase"]["hp"] is None and out["Vase"]["max_hp"] is None     # missing stays None, as `?? null` did
    assert out["Observer"]["hp"] == 5 and out["Observer"]["max_hp"] is None


@pytest.mark.asyncio
async def test_a_default_owner_world_with_only_a_gm_has_no_player_owner():
    solo = {"users": [USERS["users"][0]], "total": 1, "hasMore": False}
    out = {a["name"]: a for a in await reads.actors(FakeCLI(**{"user.list": solo}))}
    assert out["Vase"]["has_player_owner"] is False


@pytest.mark.asyncio
async def test_player_actor_mapping_skips_default_gms_and_non_owners():
    assert await reads.player_actor_mapping(FakeCLI()) == {
        "actor_names": {"Hero": P1}, "actor_uuids": {"Actor.h1": P1}}


@pytest.mark.asyncio
async def test_paginated_lists_are_followed_to_the_end():
    pages = {0: {"actors": [{"id": "g1", "name": "Goblin"}], "total": 2, "hasMore": True},
             1: {"actors": [{"id": "h1", "name": "Hero"}], "total": 2, "hasMore": False}}
    cli = FakeCLI(**{"actor.list": lambda p: pages[p["offset"]]})
    assert [a["uuid"] for a in await reads.actors(cli)] == ["Actor.g1", "Actor.h1"]


SCENES = {"scenes": [{"id": "s1", "name": "Hall", "active": False}, {"id": "s2", "name": "Crypt", "active": True}], "total": 2, "hasMore": False}


@pytest.mark.asyncio
async def test_scene_names_and_the_active_scene():
    cli = FakeCLI(**{"scene.list": SCENES})
    assert await reads.scene_names(cli) == ["Hall", "Crypt"]
    assert await reads.active_scene(cli) == {"id": "s2", "name": "Crypt"}
    nothing = FakeCLI(**{"scene.list": {"scenes": [{"id": "s1", "name": "Hall", "active": False}], "hasMore": False}})
    assert await reads.active_scene(nothing) is None          # None sends the caller to the old (canvas-aware) path


@pytest.mark.asyncio
async def test_users_keep_the_old_keys_and_default_the_avatar():
    out = {u["name"]: u for u in await reads.users(FakeCLI())}
    assert set(out["Ann"]) == {"active", "avatar", "character", "color", "id", "isGM", "name", "role"}
    assert out["Ann"]["avatar"] == "a.png" and out["Bo"]["avatar"] == "icons/svg/mystery-man.svg" and out["Gamemaster"]["isGM"] is True


INFO = {"world": {"id": "w1", "title": "My World"}, "foundry": {"version": "14.365"}, "system": {"id": "dnd5e", "version": "5.0.4"},
        "modules": [{"id": "a", "title": "A", "version": "1", "active": True}, {"id": "b", "title": "", "version": None, "active": False}]}


@pytest.mark.asyncio
async def test_world_metadata_and_modules_match_the_old_shapes():
    cli = FakeCLI(**{"system.info": INFO, "actor.list": {"actors": [], "total": 4}, "item.list": {"items": [], "total": 9}})
    assert await reads.world_metadata(cli) == {
        "name": "My World", "id": "w1", "version": "14.365", "systems": [{"name": "dnd5e", "version": "5.0.4", "enabled": True}],
        "rooms": [], "totalActors": 4, "totalItems": 9}
    mods = await reads.active_modules(cli, include_world=True)
    assert mods["modules"] == [{"id": "a", "title": "A", "version": "1", "active": True}, {"id": "b", "title": "b", "version": "", "active": False}]
    assert mods["world"] == {"title": "My World", "id": "w1"} and "world" not in await reads.active_modules(cli)


TOKENS = {"sceneId": "s2", "tokens": [{"id": "t1", "name": "Goblin"}, {"id": "t2", "name": "Hero"}], "total": 2, "hasMore": False}
FULL = {"t1": {"id": "t1", "name": "Goblin", "actorId": "g1", "x": 128, "y": 64, "width": 1, "height": 1, "disposition": -1},
        "t2": {"id": "t2", "name": "Hero", "actorId": None, "x": 0, "y": 0}}          # no disposition, no actor


def _token_cli(**extra):
    return FakeCLI(**{"scene.list": SCENES, "scene.token.list": TOKENS, "scene.token.get": lambda p: {"token": FULL[p["tokenId"]]}, **extra})


@pytest.mark.asyncio
async def test_scene_tokens_use_full_tokens_and_keep_a_missing_disposition_as_none():
    toks = {t["name"]: t for t in await reads.scene_tokens(_token_cli(), "Crypt")}
    assert toks["Goblin"] == {"name": "Goblin", "x": 128, "y": 64, "width": 1, "height": 1, "actorUuid": "g1", "id": "t1",
                              "emitter": 0, "brightness": 1, "disposition": -1}
    assert toks["Hero"]["disposition"] is None          # never defaulted: defaulting stalled combat once
    assert toks["Hero"]["actorUuid"] == "" and toks["Hero"]["width"] == 1
    assert await reads.scene_tokens(_token_cli(), "No Such Scene") is None


@pytest.mark.asyncio
async def test_scene_details_rebuilds_the_relay_envelope_and_only_fetches_non_empty_kinds():
    scene = {"scene": {"id": "s2", "_id": "s2", "name": "Crypt", "width": 1536, "height": 1152, "grid": {"size": 64}, "flags": {"ai-gm": {"atmosphere": "dusty"}},
                       "counts": {"tokens": 2, "walls": 1, "lights": 0, "sounds": 0}}}
    cli = _token_cli(**{"scene.get": scene, "scene.wall.list": {"sceneId": "s2", "walls": [{"id": "w1", "c": [0, 0, 1, 1], "door": 0, "ds": 0}], "hasMore": False}})
    data = (await reads.scene_details(cli, "Crypt"))["data"]
    assert data["name"] == "Crypt" and data["_id"] == "s2" and data["flags"]["ai-gm"]["atmosphere"] == "dusty" and data["grid"] == {"size": 64}
    assert [t["id"] for t in data["tokens"]] == ["t1", "t2"] and [w["id"] for w in data["walls"]] == ["w1"]
    assert data["lights"] == [] and data["sounds"] == [] and "counts" not in data
    assert "scene.light.list" not in [c[0] for c in cli.calls]            # an empty kind costs no call


@pytest.mark.asyncio
async def test_canvas_documents_serve_walls_lights_sounds_but_not_tokens():
    cli = FakeCLI(**{"scene.list": SCENES, "scene.wall.list": {"sceneId": "s2", "walls": [{"id": "w1", "c": [0, 0, 1, 1]}], "hasMore": False}})
    assert await reads.canvas_documents(cli, "walls") == [{"id": "w1", "c": [0, 0, 1, 1]}]
    assert await reads.canvas_documents(cli, "tokens") is None and await reads.canvas_documents(cli, "bogus") is None
    assert [c for c in cli.calls if c[0] == "scene.wall.list"][0][1]["sceneId"] == "s2"


# ── router ──────────────────────────────────────────────────────────────────

async def _ok(cli):
    return ["answer"]


@pytest.mark.asyncio
async def test_router_serves_only_when_enabled_and_never_raises():
    cli = FakeCLI()
    assert await WorldCLIRouter(cli, reads=False).read(_ok) is None
    assert await WorldCLIRouter(cli, reads=True).read(_ok) == ["answer"]

    async def broken(cli):
        raise KeyError("adapter bug")

    assert await WorldCLIRouter(cli, reads=True).read(broken) is None       # a bug in an adapter falls back

    async def refused(cli):
        raise WorldCLIError("COMMAND_DENIED", "no")

    router = WorldCLIRouter(cli, reads=True)
    assert await router.read(refused) is None and not router.tripped()     # a refusal is not "down"


@pytest.mark.asyncio
async def test_router_stops_trying_for_a_while_after_the_daemon_proves_unreachable(monkeypatch):
    attempts = []

    async def down(cli):
        attempts.append(1)
        raise WorldCLIError("DAEMON_UNAVAILABLE", "refused")

    clock = [1000.0]
    monkeypatch.setattr("foundry.world_cli_router.time.monotonic", lambda: clock[0])
    router = WorldCLIRouter(FakeCLI(), reads=True)
    assert await router.read(down) is None and router.tripped()
    assert await router.read(down) is None and len(attempts) == 1          # skipped: no connect timeout per call
    clock[0] += TRIP_SECONDS + 1
    assert not router.tripped()
    await router.read(down)
    assert len(attempts) == 2


# ── FoundryClient integration ───────────────────────────────────────────────

class _Router:
    def __init__(self, answer):
        self.answer, self.seen = answer, []

    async def read(self, adapter, *args):
        self.seen.append(adapter.__name__)
        return self.answer


def _client(router, js=None):
    c = FoundryClient()
    c.world_cli_router = router
    calls = []

    async def send_with_retry(msg_type, **kw):
        calls.append(msg_type)
        return {"result": js if js is not None else []}

    async def send(msg_type, **kw):
        calls.append(msg_type)
        return {"result": js if js is not None else []}

    c._send_with_retry, c._send = send_with_retry, send
    return c, calls


@pytest.mark.asyncio
async def test_a_migrated_read_uses_the_cli_answer_and_never_touches_the_relay():
    router = _Router([{"name": "Goblin", "uuid": "Actor.g1", "type": "npc", "has_player_owner": False, "hp": 7, "max_hp": 10}])
    c, calls = _client(router)
    assert (await c.get_actors())[0]["name"] == "Goblin"
    assert calls == [] and router.seen == ["actors"]


@pytest.mark.asyncio
async def test_when_the_router_says_none_the_original_path_runs_unchanged():
    c, calls = _client(_Router(None), js=[{"id": "g1", "uuid": "Actor.g1", "name": "Goblin", "type": "npc", "hasPlayerOwner": False, "hp": 7, "maxHp": 10}])
    assert (await c.get_actors())[0] == {"name": "Goblin", "uuid": "Actor.g1", "type": "npc", "has_player_owner": False, "hp": 7, "max_hp": 10}
    assert calls == ["execute-js"]


@pytest.mark.asyncio
async def test_a_client_with_no_router_behaves_exactly_as_before():
    c, calls = _client(None, js=[])
    assert await c.get_actors() == [] and calls == ["execute-js"]
    c2, calls2 = _client(None, js=["Hall", "Crypt"])
    assert await c2.list_scene_names() == ["Hall", "Crypt"]


# ── token helpers ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_actor_image_prefers_the_prototype_token_art_then_the_portrait():
    cli = FakeCLI(**{"actor.get": lambda p: {"actor": {"id": p["actorId"], "img": "portrait.png", "prototypeToken": {"texture": {"src": "token.webp"}}}}})
    assert await reads.actor_image(cli, "g1") == "token.webp"
    bare = FakeCLI(**{"actor.get": {"actor": {"img": "portrait.png", "prototypeToken": {"texture": {}}}}})
    assert await reads.actor_image(bare, "g1") == "portrait.png"


@pytest.mark.asyncio
async def test_actor_dispositions_match_by_name_overlap_and_default_to_hostile():
    protos = {"g1": {"disposition": -1}, "h1": {"disposition": 1}, "v1": {"disposition": 0}, "o1": {}}
    cli = FakeCLI(**{"actor.get-many": lambda p: {"actors": [{**ACTORS[i], "prototypeToken": protos[i]} for i in p["ids"]]}})
    out = await reads.actor_dispositions(cli, ["goblin", "The Hero of Time", "vase"])
    assert out == {"Goblin": -1, "Hero": 1, "Vase": 0}                   # equal, either-direction substring, and an explicit 0 kept
    assert await reads.actor_dispositions(cli, ["observer"]) == {"Observer": -1}    # no prototype disposition -> hostile
    assert await reads.actor_dispositions(cli, ["nobody"]) == {}


@pytest.mark.asyncio
async def test_token_rows_are_listed_for_the_scene():
    cli = FakeCLI(**{"scene.token.list": TOKENS})
    assert [r["id"] for r in await reads.token_rows(cli, "s2")] == ["t1", "t2"]
    assert cli.calls[-1][1]["sceneId"] == "s2"


@pytest.mark.asyncio
async def test_playlists_come_back_in_the_relay_shape():
    pl = {"id": "p1", "name": "Tavern", "description": None, "mode": 0, "playing": True, "sorting": "a", "folder": None,
          "fade": None, "channel": "music", "sounds": [{"id": "s1", "name": "Fire", "path": "sounds/fire.ogg", "playing": False,
                                                        "repeat": True, "volume": 0.3, "channel": "", "flags": {}}]}
    cli = FakeCLI(**{"playlist.list": {"playlists": [{"id": "p1", "name": "Tavern"}], "hasMore": False},
                     "playlist.get-many": {"playlists": [pl]}})

    assert await reads.playlists(cli) == [{
        "description": "", "folder": None, "id": "p1", "mode": 0, "name": "Tavern", "playing": True, "sorting": "a",
        "sounds": [{"id": "s1", "name": "Fire", "path": "sounds/fire.ogg", "playing": False, "repeat": True, "volume": 0.3}]}]
