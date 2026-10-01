"""One-step World CLI pairing (foundry/world_cli_setup.py) and POST /api/world-cli/pair."""

import json
import re
import shlex
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.routes.world_cli import PairRequest, world_cli_pair
from config import settings
from foundry.world_cli import WorldCLIError
from foundry.world_cli_setup import READ_JS, SESSION_JS, SetupError, pair_world_cli, validate_allow

SESSION = {"origin": "http://foundry:30000", "world": "test-world", "user": "gm1", "isGM": True}
STORED = {"credentials": '{"test-world:gm1":{"pairingId":"p","credential":"SECRET","label":"x"}}', "clientId": '"cid"'}


class FakeFoundry:
    def __init__(self, session=None, clicked="clicked", stored=None):
        self.session, self.clicked, self.stored = session or SESSION, clicked, STORED if stored is None else stored
        self.label = None
        self.scripts = []

    async def execute_js(self, script):
        self.scripts.append(script)
        if script == SESSION_JS:
            return {"result": self.session}
        if script == READ_JS:
            return {"result": self.stored}
        match = re.search(r'input\.value = ("[^"]*")', script)
        self.label = json.loads(match.group(1))
        return {"result": self.clicked}


class FakeDaemon:
    """Bridge is down until a request is approved; `pending` is built from the label the page was given."""

    def __init__(self, foundry, connected=False, decoys=(), approve_connects=True):
        self.foundry, self.connected, self.decoys, self.approve_connects = foundry, connected, list(decoys), approve_connects
        self.approved = []

    async def call(self, command, params=None, **kw):
        assert command == "system.ping"
        return {"pong": True, "bridge": {"status": "connected" if self.connected else "disconnected"}}

    async def control(self, operation, params=None, **kw):
        if operation == "auth.pending":
            mine = [] if self.foundry.label is None else [dict(code="MINE0001", label=self.foundry.label, origin=SESSION["origin"],
                                                              worldId=SESSION["world"], userId=SESSION["user"])]
            return {"pending": self.decoys + mine}
        if operation == "auth.approve":
            self.approved.append(params["code"])
            self.connected = self.connected or self.approve_connects
            return {"approved": True}
        raise AssertionError(operation)


def test_validate_allow_accepts_plain_commands_and_refuses_anything_that_runs_code_or_changes_access():
    assert validate_allow(["scene.wall.delete-many", "scene.wall.delete-many", "scene.light.delete-many"]) == [
        "scene.light.delete-many", "scene.wall.delete-many"]
    assert validate_allow([]) == []
    for bad in ("macro.execute", "setting.set", "setting.set-many", "user.role.set", "user.permissions.set",
                "scene.region.behavior.executable.create", "scene.region.behavior.executable.update"):
        with pytest.raises(SetupError, match="cannot be allowed"):
            validate_allow(["scene.wall.delete-many", bad])
    for malformed in ("", "delete", "Scene.Wall.Delete", "scene.wall.delete many", "scene..x", "../etc", "scene.wall.delete-many; rm"):
        with pytest.raises(SetupError, match="not a World CLI command"):
            validate_allow([malformed])


@pytest.mark.asyncio
async def test_an_already_paired_bridge_skips_pairing_and_just_exports_the_seed():
    foundry = FakeFoundry()
    daemon = FakeDaemon(foundry, connected=True)
    out = await pair_world_cli(daemon, foundry, ["scene.wall.delete-many"])
    assert out["paired_now"] is False and daemon.approved == [] and foundry.label is None   # never touched the Pair button
    entries = out["seed"]["http://foundry:30000"]
    assert entries["fvtt-world-cli.credentials"] == STORED["credentials"] and entries["fvtt-world-cli.clientId"] == '"cid"'
    assert json.loads(entries["fvtt-world-cli.commandPolicy"]) == {"version": 1, "overrides": {"scene.wall.delete-many": "allow"}}
    # The export line is shell-safe and round-trips to the same JSON.
    name, _, value = shlex.split(out["export"])[1].partition("=")
    assert name == "RELAY_ENV_HEADLESS_LOCALSTORAGE_SEED" and json.loads(value) == out["seed"]


@pytest.mark.asyncio
async def test_pairing_labels_its_own_request_and_approves_only_that_one():
    foundry = FakeFoundry()
    decoys = [
        {"code": "DECOY001", "label": "someone-else", "origin": SESSION["origin"], "worldId": SESSION["world"], "userId": SESSION["user"]},
        {"code": "DECOY002", "label": "will-be-replaced", "origin": "http://evil:30000", "worldId": SESSION["world"], "userId": SESSION["user"]},
    ]
    daemon = FakeDaemon(foundry, decoys=decoys)
    out = await pair_world_cli(daemon, foundry, [], wait_s=5)
    assert out["paired_now"] and daemon.approved == ["MINE0001"]
    assert re.fullmatch(r"aigm-setup-[0-9a-f]{8}", foundry.label)
    assert "fvtt-world-cli.commandPolicy" not in out["seed"]["http://foundry:30000"]       # no policy asked, none seeded


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [("origin", "http://evil:30000"), ("worldId", "other-world"), ("userId", "someone-else")])
async def test_a_request_with_the_right_label_from_the_wrong_origin_world_or_user_is_never_approved(field, value):
    foundry = FakeFoundry()
    daemon = FakeDaemon(foundry)
    original = daemon.control

    async def forged(operation, params=None, **kw):
        res = await original(operation, params, **kw)
        for p in res.get("pending", []) if isinstance(res, dict) else []:
            p[field] = value                                    # same nonce, but not from this session
        return res

    daemon.control = forged
    with pytest.raises(SetupError, match="No pairing request"):
        await pair_world_cli(daemon, foundry, [], wait_s=0.3)
    assert daemon.approved == []


@pytest.mark.asyncio
async def test_clear_errors_for_the_things_an_operator_can_fix():
    foundry = FakeFoundry(session={**SESSION, "isGM": False})
    with pytest.raises(SetupError, match="not a GM"):
        await pair_world_cli(FakeDaemon(foundry), foundry, [])
    for clicked, text in (("module-not-enabled", "not enabled"), ("no-pair-button", "no Pair button")):
        foundry = FakeFoundry(clicked=clicked)
        with pytest.raises(SetupError, match=text):
            await pair_world_cli(FakeDaemon(foundry), foundry, [], wait_s=0.3)
    foundry = FakeFoundry(stored={"credentials": "{}", "clientId": ""})
    with pytest.raises(SetupError, match="not stored"):
        await pair_world_cli(FakeDaemon(foundry, connected=True), foundry, [])
    foundry = FakeFoundry()
    with pytest.raises(SetupError, match="did not connect"):
        await pair_world_cli(FakeDaemon(foundry, approve_connects=False), foundry, [], wait_s=0.6)
    with pytest.raises(SetupError, match="not a World CLI command"):
        await pair_world_cli(FakeDaemon(foundry), foundry, ["nope"])


# ── route ───────────────────────────────────────────────────────────────────

def _state(client="client", foundry_connected=True):
    foundry = SimpleNamespace(is_connected=foundry_connected)
    return SimpleNamespace(world_cli=client, foundry_client=foundry)


@pytest.mark.asyncio
async def test_pair_route_gates_and_defaults(monkeypatch):
    assert (await world_cli_pair(PairRequest(), _state(client=None))).status_code == 503
    assert (await world_cli_pair(PairRequest(), _state(foundry_connected=False))).status_code == 503

    seen = {}

    async def fake_pair(client, foundry, allow, **kw):
        seen["allow"] = allow
        return {"seed": {}, "export": "export X=1"}

    import api.routes.world_cli as route
    monkeypatch.setattr(route, "pair_world_cli", fake_pair)
    monkeypatch.setattr(settings, "world_cli_writes_enabled", False)
    assert (await world_cli_pair(PairRequest(), _state()))["status"] == "ok" and seen["allow"] == []
    monkeypatch.setattr(settings, "world_cli_writes_enabled", True)
    await world_cli_pair(PairRequest(), _state())
    assert seen["allow"] == ["scene.wall.delete-many", "scene.light.delete-many", "scene.sound.delete-many"]
    await world_cli_pair(PairRequest(allow_commands=["scene.tile.delete-many"]), _state())
    assert seen["allow"] == ["scene.tile.delete-many"]


@pytest.mark.asyncio
async def test_pair_route_refuses_dangerous_commands_and_maps_failures(monkeypatch):
    resp = await world_cli_pair(PairRequest(allow_commands=["macro.execute"]), _state())
    assert resp.status_code == 400 and json.loads(resp.body)["code"] == "SETUP_FAILED"

    import api.routes.world_cli as route
    monkeypatch.setattr(route, "pair_world_cli", AsyncMock(side_effect=SetupError("no daemon")))
    assert (await world_cli_pair(PairRequest(allow_commands=[]), _state())).status_code == 400
    monkeypatch.setattr(route, "pair_world_cli", AsyncMock(side_effect=WorldCLIError("DAEMON_UNAVAILABLE", "down")))
    assert (await world_cli_pair(PairRequest(allow_commands=[]), _state())).status_code == 503


@pytest.mark.asyncio
async def test_what_foundry_returned_is_logged_never_sent_to_the_caller(monkeypatch, caplog):
    import api.routes.world_cli as route
    leak = "/Users/someone/private/foundry.db is locked"
    monkeypatch.setattr(route, "pair_world_cli", AsyncMock(side_effect=SetupError("Could not press Pair in the Foundry session.", detail=leak)))
    with caplog.at_level("WARNING"):
        resp = await world_cli_pair(PairRequest(allow_commands=[]), _state())
    assert resp.status_code == 400 and leak not in resp.body.decode()
    assert "Could not press Pair" in resp.body.decode() and leak in caplog.text
