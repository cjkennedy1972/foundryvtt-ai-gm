"""World CLI writes: the router's never-write-twice rule, the adapters, and FoundryClient's use of them."""

from unittest.mock import AsyncMock

import pytest

from actions.executors import execute_place_lights, execute_place_walls
from foundry import world_cli_writes as writes
from foundry.client import FoundryClient
from foundry.world_cli import WorldCLIError
from foundry.world_cli_router import WorldCLIRouter, WorldCLIWriteUncertain

WALLS = [{"c": [0, 0, 64, 0]}, {"c": [64, 0, 64, 64]}]


class FakeCLI:
    """Records calls; `script` maps (command, dry_run) -> result | Exception | callable(params) | list (consumed in order)."""

    def __init__(self, script=None):
        self.calls, self.script = [], script or {}

    async def call(self, command, params=None, *, dry_run=False, idempotency_key=None, **kw):
        self.calls.append((command, dict(params or {}), dry_run, idempotency_key))
        out = self.script.get((command, dry_run), self._default(command, params, dry_run))
        if isinstance(out, list):
            out = out.pop(0)
        if isinstance(out, Exception):
            raise out
        return out(params) if callable(out) else out

    @staticmethod
    def _default(command, params, dry_run):
        if command.endswith(".create-many"):
            docs = (params or {}).get("data", [])
            return {"complete": True, "outcomes": [{"index": i, "id": f"d{i}", "status": "created"} for i, _ in enumerate(docs)]}
        if command == "chat.create":
            return {"message": {"id": "m1", "content": params["data"]["content"], "whisper": [], "flags": {}}}
        if command == "scene.update":
            return {"scene": {"id": params["sceneId"], **params["patch"]}}
        return {"complete": True}

    def real(self):
        return [c for c in self.calls if not c[2]]


def router(cli, writes=True):
    return WorldCLIRouter(cli, reads=True, writes=writes)


async def _pre(cli):
    await cli.call("x", {}, dry_run=True)


async def _run(cli, key):
    return await cli.call("x", {}, idempotency_key=key)


# ── router.write ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_off_unless_writes_are_enabled():
    cli = FakeCLI()
    assert await router(cli, writes=False).write(_pre, _run) is None and cli.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [WorldCLIError("INVALID_MESSAGE", "bad"), WorldCLIError("TIMEOUT", "slow"), WorldCLIError("DAEMON_LOST", "gone")])
async def test_any_failed_dry_run_falls_back_because_a_dry_run_persists_nothing(error):
    cli = FakeCLI({("x", True): error})
    assert await router(cli).write(_pre, _run) is None
    assert cli.real() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [WorldCLIError("COMMAND_DENIED", "no"), WorldCLIError("APPROVAL_PENDING", "gm"), WorldCLIError("BRIDGE_NOT_READY", "x")])
async def test_a_write_refused_before_it_ran_falls_back(error):
    cli = FakeCLI({("x", False): error})
    assert await router(cli).write(_pre, _run) is None
    assert len(cli.real()) == 1


@pytest.mark.asyncio
async def test_a_timeout_is_retried_once_with_the_same_key_when_the_command_takes_one():
    cli = FakeCLI({("x", False): [WorldCLIError("TIMEOUT", "slow"), {"ok": True}]})
    assert await router(cli).write(_pre, _run, same_key_retry=True) == {"ok": True}
    keys = [c[3] for c in cli.real()]
    assert len(keys) == 2 and keys[0] == keys[1] and keys[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("same_key_retry,error,attempts", [
    (True, WorldCLIError("TIMEOUT", "slow"), 2),                     # still timing out after the one safe retry
    (False, WorldCLIError("TIMEOUT", "slow"), 1),                    # no key to make a retry safe
    (True, WorldCLIError("DAEMON_LOST", "dropped"), 1),              # the protocol says re-request under a fresh key, not this one
    (True, WorldCLIError("BRIDGE_DISCONNECTED", "bridge"), 1),
    (True, WorldCLIError("UPDATE_FAILED", "x", {"partial": True}), 1),
])
async def test_a_write_that_may_have_applied_is_never_handed_to_the_relay(same_key_retry, error, attempts):
    cli = FakeCLI({("x", False): [error, error]})
    with pytest.raises(WorldCLIWriteUncertain) as exc:
        await router(cli).write(_pre, _run, same_key_retry=same_key_retry)
    assert len(cli.real()) == attempts and exc.value.error is error
    assert not isinstance(exc.value, (ConnectionError, RuntimeError))   # a retry wrapper must not mistake it for transient


# ── adapters ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_chat_create_posts_content_only_and_returns_a_relay_shaped_result():
    cli = FakeCLI()
    pre, run = writes.chat_create("The bell tolls.")
    await pre(cli)
    out = await run(cli, "k1")
    assert cli.calls[0][2] is True and cli.calls[1][3] == "k1"
    assert cli.calls[1][1] == {"data": {"content": "The bell tolls."}}          # no speaker alias: the echo guard depends on that
    assert out["success"] is True and out["data"]["uuid"] == "ChatMessage.m1" and out["data"]["content"] == "The bell tolls."


@pytest.mark.asyncio
async def test_canvas_adapters_build_the_right_commands_and_treat_a_partial_result_as_uncertain():
    assert writes.canvas_create("s1", "bogus", WALLS) is None and writes.canvas_delete("s1", "walls", []) is None
    cli = FakeCLI()
    out = await writes.canvas_create("s1", "walls", WALLS)[1](cli, "k")
    assert cli.calls[0][0] == "scene.wall.create-many" and out["data"]["created"] == ["d0", "d1"]

    partial = FakeCLI({("scene.light.create-many", False): {"complete": False, "outcomes": [{"index": 0, "id": "a", "status": "created"}]}})
    with pytest.raises(WorldCLIError) as exc:
        await writes.canvas_create("s1", "lights", [{}, {}])[1](partial, "k")
    assert exc.value.maybe_applied

    cli = FakeCLI()
    assert (await writes.canvas_delete("s1", "sounds", ["a", "b"])[1](cli, "k"))["result"] == 2
    assert cli.calls[0][:2] == ("scene.sound.delete-many", {"sceneId": "s1", "ids": ["a", "b"]})
    await writes.canvas_update("s1", "walls", "w1", {"door": 1})[1](cli, "k")
    assert cli.calls[1][:2] == ("scene.wall.update", {"sceneId": "s1", "wallId": "w1", "patch": {"door": 1}})
    assert writes.split_uuid("Scene.s1.Wall.w1", "walls") == ("s1", "w1") and writes.split_uuid("Actor.x", "walls") is None


# ── FoundryClient ───────────────────────────────────────────────────────────

def _client(cli=None, *, active="s1", existing=None, writes_on=True):
    c = FoundryClient()
    c.world_cli_router = router(cli, writes=writes_on) if cli else None
    c.sent = []

    async def send(msg_type, **kw):
        c.sent.append((msg_type, kw))
        return {"success": True, "via": "relay"}

    async def js(code, _timeout=None):
        c.sent.append(("execute-js", {"code": code}))
        return {"result": 0}

    c._send, c._send_with_retry, c.execute_js = send, send, js
    c.get_active_scene_id = AsyncMock(return_value=active)
    c.canvas_get = AsyncMock(return_value=existing or [])
    return c


@pytest.mark.asyncio
async def test_chat_goes_through_world_cli_and_whispers_stay_on_the_relay():
    cli = FakeCLI()
    c = _client(cli)
    assert (await c.chat_message("hello", speaker="GM"))["via"] == "world-cli" and c.sent == []
    assert (await c.chat_message("psst", whisper=["Ann"]))["via"] == "relay"
    assert [m[0] for m in c.sent] == ["chat-send"]                        # the whisper only
    assert [c_[0] for c_ in cli.real()] == ["chat.create"]


@pytest.mark.asyncio
async def test_chat_falls_back_when_world_cli_refuses_and_never_duplicates_after_an_uncertain_write():
    refused = _client(FakeCLI({("chat.create", False): WorldCLIError("COMMAND_DENIED", "no")}))
    assert (await refused.chat_message("hello"))["via"] == "relay"

    uncertain = _client(FakeCLI({("chat.create", False): [WorldCLIError("DAEMON_LOST", "x")]}))
    with pytest.raises(WorldCLIWriteUncertain):
        await uncertain.chat_message("hello")
    assert uncertain.sent == []                                            # the relay was NOT tried


@pytest.mark.asyncio
async def test_a_client_without_a_router_or_with_writes_off_uses_the_relay_unchanged():
    assert (await _client(None).chat_message("hi"))["via"] == "relay"
    cli = FakeCLI()
    assert (await _client(cli, writes_on=False).chat_message("hi"))["via"] == "relay" and cli.calls == []


@pytest.mark.asyncio
async def test_canvas_create_update_delete_route_by_the_active_scene_and_fall_back():
    cli = FakeCLI()
    c = _client(cli)
    assert (await c.canvas_create("walls", WALLS))["via"] == "world-cli"
    assert (await c.canvas_update("walls", {"door": 1}, uuid="Scene.s1.Wall.w1"))["via"] == "world-cli"
    assert (await c.canvas_delete("walls", ids=["w1", "w2"]))["via"] == "world-cli"
    assert (await c.canvas_delete("walls", uuid="Scene.s1.Wall.w9"))["via"] == "world-cli"
    assert c.sent == [] and [x[0] for x in cli.real()] == ["scene.wall.create-many", "scene.wall.update", "scene.wall.delete-many", "scene.wall.delete-many"]

    unsupported = _client(FakeCLI())
    assert (await unsupported.canvas_create("bogus", WALLS))["via"] == "relay"      # a layer World CLI has no kind for
    assert (await _client(FakeCLI(), active=None).canvas_create("walls", WALLS))["via"] == "relay"
    assert (await _client(FakeCLI()).canvas_update("walls", {"door": 1}))["via"] == "relay"        # no uuid: can't address it


@pytest.mark.asyncio
async def test_clearing_a_layer_deletes_by_id_skips_when_empty_and_falls_back_when_refused():
    cli = FakeCLI()
    c = _client(cli, existing=[{"_id": "w1"}, {"id": "w2"}, {"c": [0, 0, 1, 1]}])
    assert (await c.clear_canvas_layer("walls"))["via"] == "world-cli"
    assert cli.real()[0][:2] == ("scene.wall.delete-many", {"sceneId": "s1", "ids": ["w1", "w2"]})

    empty = _client(FakeCLI(), existing=[])
    assert (await empty.clear_canvas_layer("walls"))["result"] == 0 and empty.sent == []

    gated = _client(FakeCLI({("scene.wall.delete-many", False): WorldCLIError("APPROVAL_PENDING", "gm")}), existing=[{"_id": "w1"}])
    out = await gated.clear_canvas_layer("walls")
    assert out == {"result": 0} and [m[0] for m in gated.sent] == ["execute-js"]       # the original JS path cleared it


@pytest.mark.asyncio
async def test_scene_updates_resolve_the_scene_and_fall_back_on_a_rejected_patch(monkeypatch):
    async def fake_read(adapter, *args):
        return "s9" if adapter.__name__ == "scene_id" else None

    cli = FakeCLI()
    c = _client(cli)
    c._cli_read = fake_read
    assert (await c.update_scene("Crypt", {"darkness": 0.5}))["via"] == "world-cli"
    assert cli.real()[0][:2] == ("scene.update", {"sceneId": "s9", "patch": {"darkness": 0.5}})
    assert (await c.configure_scene({"padding": 0}))["via"] == "world-cli"                     # no name: the active scene

    rejected = _client(FakeCLI({("scene.update", True): WorldCLIError("INVALID_MESSAGE", "unknown field")}))
    rejected._cli_read = fake_read
    assert (await rejected.update_scene("Crypt", {"environment.darknessLevel": 1}))["via"] == "relay"


# ── the placeable actions now ride on the client ────────────────────────────

@pytest.mark.asyncio
async def test_placing_walls_and_lights_reaches_world_cli_through_the_client_and_maps_legacy_sense():
    cli = FakeCLI()
    c = _client(cli)
    out = await execute_place_walls([{"c": [0, 0, 64, 0], "sense": 0}], foundry=c)
    assert out["count"] == 1 and out["result"]["via"] == "world-cli"
    assert cli.real()[0][1]["data"] == [{"c": [0, 0, 64, 0], "sight": 0}]
    await execute_place_lights([{"x": 1, "y": 1}], clear_existing=False, foundry=c)
    assert cli.real()[1][0] == "scene.light.create-many" and c.sent == []
