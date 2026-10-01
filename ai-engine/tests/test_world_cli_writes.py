"""Scene placeables through World CLI (foundry/world_cli_writes.py) and the executors that use it.

The invariant these pin down: a write is never run twice. The relay may take over only when the
failure proves nothing was executed (or it was the dry run); a timeout, a connection lost
mid-request or a partial result is reported as a failure and never retried on the relay.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from actions.dispatcher import ActionDispatcher
from actions.executors import execute_place_lights, execute_place_sounds, execute_place_walls
from config import settings
from foundry.world_cli import WorldCLIError
from foundry.world_cli_writes import place_via_world_cli

WALLS = [{"c": [0, 0, 64, 0]}, {"c": [64, 0, 64, 64]}]


class FakeCLI:
    """Records calls; `script` maps (command, dry_run) -> a result, an Exception to raise, or a callable."""

    def __init__(self, script=None):
        self.calls = []
        self.script = script or {}

    async def call(self, command, params=None, *, dry_run=False, **kw):
        self.calls.append((command, dict(params or {}), dry_run))
        outcome = self.script.get((command, dry_run), self._default(command, params, dry_run))
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    @staticmethod
    def _default(command, params, dry_run):
        if command.endswith(".create-many"):
            docs = (params or {}).get("data", [])
            if dry_run:
                return {"complete": True, "outcomes": [{"index": i, "status": "previewed"} for i, _ in enumerate(docs)]}
            return {"complete": True, "outcomes": [{"index": i, "id": f"d{i}", "status": "created"} for i, _ in enumerate(docs)]}
        return {"deleted": True}

    def real_calls(self):
        return [c for c in self.calls if not c[2]]


def _foundry(scene_id="scene1", existing=None):
    f = MagicMock()
    f.get_active_scene_id = AsyncMock(return_value=scene_id)
    f.canvas_get = AsyncMock(return_value=existing or [])
    f.canvas_create = AsyncMock(return_value={"ok": True})
    f.clear_canvas_layer = AsyncMock()
    return f


@pytest.fixture
def writes_on(monkeypatch):
    monkeypatch.setattr(settings, "world_cli_writes_enabled", True)


def _state(cli):
    return SimpleNamespace(world_cli=cli)


@pytest.mark.asyncio
async def test_off_by_default_and_without_a_client_or_scene_the_relay_is_used(monkeypatch):
    cli = FakeCLI()
    assert await place_via_world_cli(_state(cli), _foundry(), "walls", WALLS, False) is None   # flag off
    assert cli.calls == []
    monkeypatch.setattr(settings, "world_cli_writes_enabled", True)
    assert await place_via_world_cli(_state(None), _foundry(), "walls", WALLS, False) is None
    assert await place_via_world_cli(_state(cli), _foundry(scene_id=None), "walls", WALLS, False) is None
    assert await place_via_world_cli(_state(cli), _foundry(), "tiles", WALLS, False) is None   # not a supported layer
    assert cli.calls == []


@pytest.mark.asyncio
async def test_a_dry_run_precedes_the_real_bulk_create(writes_on):
    cli = FakeCLI()
    out = await place_via_world_cli(_state(cli), _foundry(), "walls", WALLS, False)
    assert out == {"success": True, "via": "world-cli", "created": 2, "cleared": 0, "sceneId": "scene1"}
    assert [(c[0], c[2]) for c in cli.calls] == [("scene.wall.create-many", True), ("scene.wall.create-many", False)]
    assert cli.calls[1][1] == {"sceneId": "scene1", "data": WALLS}


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [WorldCLIError("INVALID_MESSAGE", "bad doc"), WorldCLIError("TIMEOUT", "slow"), WorldCLIError("DAEMON_LOST", "gone")])
async def test_any_failure_of_the_dry_run_falls_back_because_a_dry_run_persists_nothing(writes_on, error):
    cli = FakeCLI({("scene.wall.create-many", True): error})
    assert await place_via_world_cli(_state(cli), _foundry(), "walls", WALLS, False) is None
    assert cli.real_calls() == []


@pytest.mark.asyncio
async def test_clearing_deletes_the_existing_placeables_by_id_before_creating(writes_on):
    cli = FakeCLI()
    existing = [{"_id": "w1"}, {"id": "w2"}, {"c": [0, 0, 1, 1]}]       # both id spellings; one without an id
    out = await place_via_world_cli(_state(cli), _foundry(existing=existing), "walls", WALLS, True)
    assert out["success"] and out["cleared"] == 2
    assert [c[0] for c in cli.real_calls()] == ["scene.wall.delete-many", "scene.wall.create-many"]
    assert cli.real_calls()[0][1] == {"sceneId": "scene1", "ids": ["w1", "w2"]}

    nothing = FakeCLI()
    out = await place_via_world_cli(_state(nothing), _foundry(existing=[]), "walls", WALLS, True)
    assert out["cleared"] == 0 and "scene.wall.delete-many" not in [c[0] for c in nothing.calls]


@pytest.mark.asyncio
async def test_an_approval_gated_clear_that_nothing_executed_falls_back_with_nothing_created(writes_on):
    cli = FakeCLI({("scene.wall.delete-many", False): WorldCLIError("APPROVAL_PENDING", "needs the GM")})
    out = await place_via_world_cli(_state(cli), _foundry(existing=[{"_id": "w1"}]), "walls", WALLS, True)
    assert out is None
    assert [c[0] for c in cli.real_calls()] == ["scene.wall.delete-many"]     # no create after a refused clear


@pytest.mark.asyncio
async def test_a_create_refused_before_dispatch_falls_back(writes_on):
    cli = FakeCLI({("scene.wall.create-many", False): WorldCLIError("COMMAND_DENIED", "denied")})
    assert await place_via_world_cli(_state(cli), _foundry(), "walls", WALLS, False) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [
    WorldCLIError("TIMEOUT", "no answer"), WorldCLIError("DAEMON_LOST", "dropped"),
    WorldCLIError("BRIDGE_DISCONNECTED", "bridge dropped"), WorldCLIError("UPDATE_FAILED", "x", {"partial": True}),
])
async def test_a_write_that_may_have_applied_is_reported_and_not_handed_to_the_relay(writes_on, error):
    cli = FakeCLI({("scene.wall.create-many", False): error})
    out = await place_via_world_cli(_state(cli), _foundry(), "walls", WALLS, False)
    assert out["success"] is False and out["maybe_applied"] is True and error.code in out["error"]


@pytest.mark.asyncio
async def test_a_partial_bulk_result_is_a_failure_not_a_retry(writes_on):
    partial = {"complete": False, "outcomes": [{"index": 0, "id": "a", "status": "created"}, {"index": 1, "status": "failed"}]}
    cli = FakeCLI({("scene.wall.create-many", False): partial})
    out = await place_via_world_cli(_state(cli), _foundry(), "walls", WALLS, False)
    assert out["success"] is False and "1 of 2 walls" in out["error"] and out["maybe_applied"]


# ── the executors: the relay is called exactly when it should be ────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("executor,layer,docs", [
    (execute_place_walls, "walls", WALLS),
    (execute_place_lights, "lights", [{"x": 64, "y": 64, "config": {"dim": 30}}]),
    (execute_place_sounds, "sounds", [{"x": 64, "y": 64, "path": "a.ogg"}]),
])
async def test_executors_use_world_cli_then_the_relay_only_when_nothing_ran(writes_on, executor, layer, docs):
    kw = {layer: docs}
    foundry = _foundry()
    ok = await executor(foundry=foundry, app_state=_state(FakeCLI()), **kw)
    assert ok["success"] and ok["result"]["via"] == "world-cli" and ok["count"] == len(docs)
    foundry.canvas_create.assert_not_awaited()

    foundry = _foundry()
    refused = FakeCLI({(f"scene.{layer[:-1]}.create-many", False): WorldCLIError("COMMAND_DENIED", "no")})
    out = await executor(foundry=foundry, app_state=_state(refused), **kw)
    foundry.canvas_create.assert_awaited_once_with(layer, docs)          # fallback: exactly one relay write
    assert out["result"] == {"ok": True}

    foundry = _foundry()
    timed_out = FakeCLI({(f"scene.{layer[:-1]}.create-many", False): WorldCLIError("TIMEOUT", "slow")})
    out = await executor(foundry=foundry, app_state=_state(timed_out), **kw)
    foundry.canvas_create.assert_not_awaited()                           # never twice
    assert out["success"] is False and "TIMEOUT" in out["error"]


@pytest.mark.asyncio
async def test_without_an_app_state_the_executors_behave_exactly_as_before(writes_on):
    foundry = _foundry()
    out = await execute_place_walls(WALLS, foundry=foundry)
    foundry.canvas_create.assert_awaited_once_with("walls", WALLS)
    assert out == {"type": "place_walls", "count": 2, "result": {"ok": True}}


@pytest.mark.asyncio
async def test_the_dispatcher_injects_app_state_so_the_fast_path_is_reachable(writes_on):
    foundry = _foundry()
    state = SimpleNamespace(world_cli=FakeCLI())
    result = await ActionDispatcher(foundry, app_state=state).execute({"type": "place_walls", "walls": WALLS})
    assert result["success"] and result["result"]["via"] == "world-cli"
    foundry.canvas_create.assert_not_awaited()
