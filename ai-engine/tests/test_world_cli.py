"""World CLI client (foundry/world_cli.py) against a fake daemon, and its read-only routes."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
import websockets

from api.routes.world_cli import world_cli_audit_files, world_cli_status
from foundry.world_cli import WorldCLI, WorldCLIError

CREDENTIAL = "c" * 43
VERSION = "1.1.2"


class FakeDaemon:
    """Speaks the daemon's side: a hello handshake, then answers command.request frames via `respond`."""

    def __init__(self, respond=None, hello_error=None):
        self.frames = []
        self.respond = respond or (lambda req: {"ok": True, "result": {"echo": req["command"]}})
        self.hello_error = hello_error
        self.connections = 0
        self.sockets = []

    async def _handler(self, ws):
        self.connections += 1
        self.sockets.append(ws)
        hello = json.loads(await ws.recv())
        self.frames.append(hello)
        ack = {"protocolVersion": VERSION, "type": "client.hello.ack", "ok": self.hello_error is None}
        if self.hello_error:
            ack["error"] = self.hello_error
        await ws.send(json.dumps(ack))
        if self.hello_error:
            return
        async for raw in ws:
            req = json.loads(raw)
            self.frames.append(req)
            reply = self.respond(req)
            if asyncio.iscoroutine(reply):
                reply = await reply
            if reply is None:           # simulate a daemon that never answers
                continue
            await ws.send(json.dumps({"protocolVersion": VERSION, "type": "command.response", "id": req["id"], **reply}))

    async def __aenter__(self):
        self.server = await websockets.serve(self._handler, "127.0.0.1", 0)
        self.url = f"ws://127.0.0.1:{self.server.sockets[0].getsockname()[1]}/"
        return self

    async def __aexit__(self, *a):
        self.server.close()
        await self.server.wait_closed()


@pytest.fixture
def config(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"version": 3, "deviceCredential": CREDENTIAL}))
    return str(path)


@pytest.mark.asyncio
async def test_hello_then_request_frames_match_the_protocol(config):
    async with FakeDaemon() as d:
        client = WorldCLI(d.url, VERSION, config)
        assert await client.call("system.ping", {"x": 1}) == {"echo": "system.ping"}
        await client.close()
    hello, request = d.frames
    assert hello == {"protocolVersion": VERSION, "type": "client.hello", "credential": CREDENTIAL, "client": "cli"}
    assert request["type"] == "command.request" and request["command"] == "system.ping"
    assert request["params"] == {"x": 1} and request["protocolVersion"] == VERSION and request["id"]


@pytest.mark.asyncio
async def test_dry_run_and_idempotency_key_ride_in_params(config):
    async with FakeDaemon() as d:
        client = WorldCLI(d.url, VERSION, config)
        await client.call("actor.create", {"data": {"name": "G", "type": "npc"}}, dry_run=True, idempotency_key="k1")
        await client.close()
    assert d.frames[-1]["params"] == {"data": {"name": "G", "type": "npc"}, "dryRun": True, "idempotencyKey": "k1"}


@pytest.mark.asyncio
async def test_error_frames_raise_with_code_message_and_details(config):
    err = {"ok": False, "error": {"code": "APPROVAL_PENDING", "message": "needs the GM", "details": {"approvalId": "a1"}}}
    async with FakeDaemon(respond=lambda r: err) as d:
        client = WorldCLI(d.url, VERSION, config)
        with pytest.raises(WorldCLIError) as exc:
            await client.call("scene.delete", {"sceneId": "s"})
        await client.close()
    assert exc.value.code == "APPROVAL_PENDING" and exc.value.message == "needs the GM"
    assert exc.value.details == {"approvalId": "a1"}


@pytest.mark.asyncio
async def test_concurrent_calls_are_matched_by_id_even_when_answered_out_of_order(config):
    async def respond(req):
        await asyncio.sleep(0.2 if req["params"]["n"] == 0 else 0)     # the first request is answered last
        return {"ok": True, "result": {"n": req["params"]["n"]}}

    async with FakeDaemon(respond=respond) as d:
        client = WorldCLI(d.url, VERSION, config)
        results = await asyncio.gather(*[client.call("system.ping", {"n": n}) for n in range(5)])
        await client.close()
    assert [r["n"] for r in results] == [0, 1, 2, 3, 4]


@pytest.mark.asyncio
async def test_a_rejected_hello_surfaces_the_daemons_reason(config):
    skew = {"code": "PROTOCOL_VERSION_MISMATCH", "message": "CLI 1.1.2 vs daemon 1.2.0", "details": {"daemon": "1.2.0"}}
    async with FakeDaemon(hello_error=skew) as d:
        with pytest.raises(WorldCLIError) as exc:
            await WorldCLI(d.url, VERSION, config).call("system.ping")
    assert exc.value.code == "PROTOCOL_VERSION_MISMATCH" and exc.value.details == {"daemon": "1.2.0"}


@pytest.mark.asyncio
async def test_unreachable_daemon_and_missing_config_have_their_own_codes(config, tmp_path):
    with pytest.raises(WorldCLIError) as exc:
        await WorldCLI("ws://127.0.0.1:1/", VERSION, config).call("system.ping")
    assert exc.value.code == "DAEMON_UNAVAILABLE"
    with pytest.raises(WorldCLIError) as exc:
        await WorldCLI("ws://127.0.0.1:1/", VERSION, str(tmp_path / "nope.json")).call("system.ping")
    assert exc.value.code == "NOT_CONFIGURED"
    (tmp_path / "empty.json").write_text("{}")
    with pytest.raises(WorldCLIError) as exc:
        await WorldCLI("ws://127.0.0.1:1/", VERSION, str(tmp_path / "empty.json")).call("system.ping")
    assert exc.value.code == "NOT_CONFIGURED" and "deviceCredential" in exc.value.message


@pytest.mark.asyncio
async def test_a_daemon_that_drops_mid_request_fails_fast_and_the_next_call_reconnects(config):
    async with FakeDaemon() as d:
        client = WorldCLI(d.url, VERSION, config)
        await client.call("system.ping")
        d.respond = lambda r: None                      # next request is swallowed...
        task = asyncio.create_task(client.call("system.ping"))
        await asyncio.sleep(0.1)
        await d.sockets[0].close()                      # ...and the daemon goes away
        with pytest.raises(WorldCLIError) as exc:
            await asyncio.wait_for(task, 5)
        assert exc.value.code == "DAEMON_LOST" and exc.value.maybe_applied   # sent, then lost: may have run
        d.respond = lambda r: {"ok": True, "result": {"back": True}}
        assert await client.call("system.ping") == {"back": True}
        assert d.connections == 2
        await client.close()


@pytest.mark.asyncio
async def test_an_unanswered_command_times_out_and_leaves_no_pending_entry(config):
    async with FakeDaemon(respond=lambda r: None) as d:
        client = WorldCLI(d.url, VERSION, config, timeout=0.2)
        with pytest.raises(WorldCLIError) as exc:
            await client.call("system.ping")
        assert exc.value.code == "TIMEOUT" and client._pending == {}
        await client.close()


# ── routes ──────────────────────────────────────────────────────────────────

def _state(client):
    state = MagicMock()
    state.world_cli = client
    return state


def _body(resp):
    return json.loads(resp.body)


@pytest.mark.asyncio
async def test_status_and_audit_routes_pass_through_and_map_errors():
    client = MagicMock()
    client.call = AsyncMock(return_value={"pong": True, "bridge": {"status": "connected"}})
    assert (await world_cli_status(_state(client)))["bridge"]["status"] == "connected"

    client.call = AsyncMock(return_value={"broken": [{"docType": "Actor", "id": "a", "field": "img"}], "total": 1, "checkedRefs": 40})
    out = await world_cli_audit_files(["actor", "scene"], 25, 5, _state(client))
    client.call.assert_awaited_once_with("world.audit-files", {"limit": 25, "offset": 5, "scope": ["actor", "scene"]})
    assert out["total"] == 1 and out["broken"][0]["field"] == "img"

    client.call = AsyncMock(side_effect=WorldCLIError("DAEMON_UNAVAILABLE", "down"))
    assert (await world_cli_status(_state(client))).status_code == 503
    client.call = AsyncMock(side_effect=WorldCLIError("BRIDGE_NOT_READY", "no GM browser"))
    assert (await world_cli_audit_files(None, 50, 0, _state(client))).status_code == 503
    client.call = AsyncMock(side_effect=WorldCLIError("COMMAND_DENIED", "denied"))
    assert (await world_cli_audit_files(None, 50, 0, _state(client))).status_code == 409
    client.call = AsyncMock(side_effect=WorldCLIError("INVALID_MESSAGE", "bad scope"))
    assert (await world_cli_audit_files(None, 50, 0, _state(client))).status_code == 400


@pytest.mark.asyncio
async def test_routes_say_so_when_world_cli_is_not_enabled():
    for resp in (await world_cli_status(_state(None)), await world_cli_audit_files(None, 50, 0, _state(None))):
        assert resp.status_code == 503 and _body(resp)["code"] == "DISABLED"


def test_only_errors_that_may_have_run_are_flagged_maybe_applied():
    # Provably not executed: nothing reached Foundry, or it was refused before dispatch.
    for code in ("DAEMON_UNAVAILABLE", "NOT_CONFIGURED", "BRIDGE_NOT_READY", "COMMAND_DENIED", "APPROVAL_PENDING",
                 "APPROVAL_DENIED", "INVALID_MESSAGE", "SCENE_NOT_FOUND"):
        assert not WorldCLIError(code, "x").maybe_applied, code
    # May have committed: a timeout after send, a drop mid-flight, or a partial write.
    for code in ("TIMEOUT", "DAEMON_LOST", "BRIDGE_TIMEOUT", "BRIDGE_DISCONNECTED", "APPROVAL_UNKNOWN"):
        assert WorldCLIError(code, "x").maybe_applied, code
    assert WorldCLIError("UPDATE_FAILED", "x", {"partial": True}).maybe_applied
    assert WorldCLIError("UPDATE_FAILED", "x", {"indeterminate": True}).maybe_applied
