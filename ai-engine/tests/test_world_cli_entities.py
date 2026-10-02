"""create_entity / update_entity / update_actor through World CLI: relay return keys, fallback, never-write-twice."""

import json
from pathlib import Path

import pytest

from foundry.client import FoundryClient
from foundry.world_cli import WorldCLIError
from foundry.world_cli_router import WorldCLIRouter, WorldCLIWriteUncertain
from tests.test_world_cli_writes import FakeCLI

SHAPES = json.loads((Path(__file__).parent / "fixtures" / "relay_entity_shapes.json").read_text())
# The relay's keys minus clientId/requestId, which have no World CLI equivalent and no caller reads.
CREATE_KEYS = set(SHAPES["create_Actor"]) - {"clientId", "requestId"}
UPDATE_KEYS = set(SHAPES["update_by_uuid"]) - {"clientId", "requestId"}
RELAY = {"relay": True}


def _client(cli):
    c = FoundryClient()
    c.world_cli_router = WorldCLIRouter(cli, reads=True, writes=True)
    c.sent = []

    async def send(msg_type, **params):
        c.sent.append((msg_type, params))
        return RELAY

    c._send = send
    return c


def _doc(key, ident="abc"):
    return lambda p: {key: {"id": p.get("actorId") or ident, "name": "N"}}


@pytest.mark.asyncio
@pytest.mark.parametrize("etype,cmd,key,data", [
    ("Actor", "actor", "actor", {"name": "A", "type": "npc"}),
    ("Item", "item", "item", {"name": "I", "type": "loot"}),
    ("JournalEntry", "journal", "journal", {"name": "J"}),
    ("Scene", "scene", "scene", {"name": "S"}),
])
async def test_create_matches_the_relay_shape(etype, cmd, key, data):
    cli = FakeCLI({(f"{cmd}.create", False): _doc(key)})
    c = _client(cli)
    out = await c.create_entity(etype, data)
    assert set(out) == CREATE_KEYS and out["type"] == "create-result"
    assert out["uuid"] == f"{etype}.abc" and out["entity"]["_id"] == "abc"
    assert not c.sent
    dry, real = cli.calls
    assert dry[0] == real[0] == f"{cmd}.create" and dry[2] is True and real[2] is False and real[3]
    assert real[1]["data"] == data


@pytest.mark.asyncio
@pytest.mark.parametrize("uuid,cmd,idp,key", [
    ("Actor.abc", "actor", "actorId", "actor"), ("Item.abc", "item", "itemId", "item"),
    ("JournalEntry.abc", "journal", "journalId", "journal"), ("Scene.abc", "scene", "sceneId", "scene"),
])
async def test_update_matches_the_relay_shape(uuid, cmd, idp, key):
    patch = {"prototypeToken": {"texture": {"src": "t.webp"}}} if cmd == "actor" else {"flags": {"ai-gm": {"x": 1}}}
    cli = FakeCLI({(f"{cmd}.update", False): lambda p: {key: {"id": p[idp]}}})
    c = _client(cli)
    out = await c.update_entity(uuid=uuid, data=patch)
    assert set(out) == UPDATE_KEYS and out["type"] == "update-result" and out["uuid"] == uuid
    assert isinstance(out["entity"], list) and out["entity"][0]["_id"] == "abc"
    assert not c.sent
    dry, real = cli.calls
    assert dry[2] is True and real[2] is False and real[1] == {idp: "abc", "patch": patch, **({"include": ["flags", "effects"]} if cmd in ("actor", "item") else {})}


@pytest.mark.asyncio
async def test_update_actor_benefits_from_the_routed_update():
    cli = FakeCLI({("actor.update", False): lambda p: {"actor": {"id": p["actorId"]}}})
    c = _client(cli)

    async def scenes():
        return []

    async def actors(world_only=True):
        return [{"name": "Goblin", "uuid": "Actor.g1"}]

    c.get_scenes, c.get_actors = scenes, actors
    out = await c.update_actor("Goblin", {"img": "p.png"})
    assert out["uuid"] == "Actor.g1" and out["type"] == "update-result"
    assert [m for m, _ in c.sent] == ["search"]            # name resolution only; the write went through the CLI


@pytest.mark.asyncio
async def test_falls_back_when_the_dry_run_fails():
    cli = FakeCLI({("actor.create", True): WorldCLIError("INVALID_PARAMS", "bad data"),
                   ("actor.update", True): WorldCLIError("INVALID_PARAMS", "dotted key")})
    c = _client(cli)
    assert await c.create_entity("Actor", {"name": "A", "type": "npc", "items": []}) == RELAY
    assert await c.update_entity(uuid="Actor.abc", data={"system.attributes.hp.value": 3}) == RELAY
    assert [m for m, _ in c.sent] == ["create", "update"] and not cli.real()


@pytest.mark.asyncio
async def test_unrouted_types_uuids_and_token_updates_stay_on_the_relay():
    cli = FakeCLI()
    c = _client(cli)
    assert await c.create_entity("RollTable", {"name": "T"}) == RELAY
    assert await c.update_entity(uuid="Scene.s.Token.t", data={"x": 1}) == RELAY      # embedded uuid
    assert await c.update_entity(uuid="Playlist.p", data={"name": "x"}) == RELAY
    assert await c.update_entity(token_id="tok1", data={"hidden": True}) == RELAY
    assert await c.update_entity(uuid="Actor.abc", data={}) == RELAY                   # empty patch
    assert cli.calls == [] and len(c.sent) == 5
    assert c.sent[3][1] == {"token_id": "tok1", "data": {"hidden": True}}


@pytest.mark.asyncio
async def test_a_write_that_may_have_applied_never_reaches_the_relay():
    c = _client(FakeCLI({("actor.create", False): [WorldCLIError("DAEMON_LOST", "gone")]}))
    with pytest.raises(WorldCLIWriteUncertain):
        await c.create_entity("Actor", {"name": "A", "type": "npc"})
    c2 = _client(FakeCLI({("actor.update", False): [WorldCLIError("DAEMON_LOST", "gone")]}))
    with pytest.raises(WorldCLIWriteUncertain):
        await c2.update_entity(uuid="Actor.abc", data={"name": "B"})
    assert not c.sent and not c2.sent


@pytest.mark.asyncio
async def test_create_timeout_retries_once_with_the_same_key():
    cli = FakeCLI({("scene.create", False): [WorldCLIError("TIMEOUT", "slow"), {"scene": {"id": "s1"}}]})
    out = await _client(cli).create_entity("Scene", {"name": "S"})
    assert out["uuid"] == "Scene.s1"
    keys = [c_[3] for c_ in cli.real()]
    assert len(keys) == 2 and keys[0] == keys[1]


@pytest.mark.asyncio
async def test_deploy_create_entity_sees_the_same_uuid_either_way():
    from campaign.orchestrator_deploy import DeploymentMixin
    mixin = DeploymentMixin()
    routed = await mixin._create_entity(_client(FakeCLI({("actor.create", False): _doc("actor")})), "Actor", {"name": "A", "type": "npc"})
    assert DeploymentMixin._entity_uuid(routed) == "Actor.abc"
    assert await mixin._create_entity(_client(FakeCLI()), "RollTable", {"name": "T"}) == RELAY
