"""file.upload through World CLI: same return keys as the relay's /upload, fallback, never-write-twice."""

import base64

import pytest

from foundry import client as client_mod
from foundry.client import FoundryClient
from foundry.world_cli import WorldCLIError
from foundry.world_cli_router import WorldCLIRouter, WorldCLIWriteUncertain
from tests.test_world_cli_writes import FakeCLI

RELAY = {"type": "upload-file-result", "requestId": "r1", "success": True, "path": "maps/crypt.png"}
LIVE_KEYS = {"type", "success", "path"}        # the relay's shape minus its requestId, which no caller reads


def _client(cli, monkeypatch):
    c = FoundryClient()
    c.world_cli_router = WorldCLIRouter(cli, reads=True, writes=True)
    c.relayed = False

    async def relay(fn, *a, **k):
        c.relayed = True
        return RELAY

    monkeypatch.setattr(client_mod.asyncio, "to_thread", relay)
    return c


def _stored(params):
    return {"file": {"path": params["path"]}}


@pytest.mark.asyncio
async def test_upload_goes_through_world_cli_with_the_old_return_keys(monkeypatch):
    cli = FakeCLI({("file.upload", False): _stored})
    c = _client(cli, monkeypatch)
    out = await c.upload_file(b"png", "worlds/w/maps", "crypt.png")
    assert LIVE_KEYS <= set(out) and out["path"] == "worlds/w/maps/crypt.png" and out["success"] is True
    assert not c.relayed
    dry, real = cli.calls
    assert dry[2] is True and real[2] is False and real[3]                      # dry run first, real call keyed
    assert real[1] == {"path": "worlds/w/maps/crypt.png", "contentBase64": base64.b64encode(b"png").decode(), "mimeType": "image/png"}


@pytest.mark.asyncio
async def test_upload_falls_back_when_the_dry_run_fails_or_the_case_is_not_routable(monkeypatch):
    refused = _client(FakeCLI({("file.upload", True): WorldCLIError("INVALID_PARAMS", "outside the world")}), monkeypatch)
    assert await refused.upload_file(b"x", "maps", "a.png") == RELAY and refused.relayed
    cli = FakeCLI()
    other = _client(cli, monkeypatch)
    assert await other.upload_file(b"x", "maps", "a.png", overwrite=False) == RELAY
    assert await other.upload_file(b"x", "maps", "a.png", source="public") == RELAY
    assert cli.calls == []


@pytest.mark.asyncio
async def test_an_upload_that_may_have_applied_is_never_sent_to_the_relay_too(monkeypatch):
    cli = FakeCLI({("file.upload", False): [WorldCLIError("DAEMON_LOST", "gone")]})
    c = _client(cli, monkeypatch)
    with pytest.raises(WorldCLIWriteUncertain):
        await c.upload_file(b"x", "worlds/w", "a.png")
    assert not c.relayed

    timeouts = FakeCLI({("file.upload", False): [WorldCLIError("TIMEOUT", "slow"), _stored({"path": "worlds/w/a.png"})]})
    out = await _client(timeouts, monkeypatch).upload_file(b"x", "worlds/w", "a.png")
    assert out["path"] == "worlds/w/a.png"
    keys = [c_[3] for c_ in timeouts.real()]
    assert len(keys) == 2 and keys[0] == keys[1]                                # same key on the timeout retry
