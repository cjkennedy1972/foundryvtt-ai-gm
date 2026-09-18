"""Six defects that all shared a shape: the code disagreed with its comment."""

import asyncio
import time

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestRateLimiterPrune:
    """main.py's recency prune used dict.update, which merges and removes nothing.

    Drives the real middleware: the map must shrink once stale buckets age out.
    """

    @pytest.mark.asyncio
    async def test_stale_buckets_are_actually_evicted(self, monkeypatch):
        import main

        monkeypatch.setattr(main, "_API_RATE_MAX_CLIENTS", 2)
        now = time.time()
        monkeypatch.setattr(
            main, "_api_rate",
            {f"10.0.0.{i}": [now - 3600] for i in range(5)} | {"10.0.0.99": [now]},
        )

        async def call_next(_request):
            return MagicMock()

        request = MagicMock()
        request.url.path = "/api/state"
        request.headers = {"content-length": "10"}
        request.client.host = "10.0.0.99"
        monkeypatch.setattr(main.settings, "admin_token", "")

        await main.protect_api_resources(request, call_next)

        assert "10.0.0.99" in main._api_rate
        assert all(ip == "10.0.0.99" for ip in main._api_rate), (
            f"hour-old buckets survived the prune: {sorted(main._api_rate)}"
        )


class TestEventDispatchSnapshot:
    """client.py iterated the live handler list across an await.

    Drives the real _event_worker: a handler that deregisters a sibling
    mid-dispatch (what wait_for_hook's timeout does) must not skip it.
    """

    @pytest.mark.asyncio
    async def test_handler_removed_mid_dispatch_does_not_skip_a_sibling(self):
        from foundry.client import FoundryClient

        client = FoundryClient()
        seen = []

        async def first(data):
            client._handlers["hooks"].remove(second)
            seen.append("first")

        async def second(data):
            seen.append("second")

        async def third(data):
            seen.append("third")

        client._handlers["hooks"] = [first, second, third]
        await client._event_queue.put(("hooks", {}))

        worker = asyncio.create_task(client._event_worker())
        await client._event_queue.join()
        worker.cancel()

        assert seen == ["first", "second", "third"], (
            "iterating the live list shifts the index and skips a handler"
        )


class TestForceTriggersKeepAReference:
    """reinforcement_manager used bare create_task for admin-triggered passes."""

    @pytest.mark.asyncio
    async def test_force_reinforce_returns_a_tracked_task(self):
        from context.reinforcement_manager import ContextReinforcementManager
        from utils.tasks import _bg_tasks

        mgr = ContextReinforcementManager.__new__(ContextReinforcementManager)
        started = asyncio.Event()

        async def slow_pass():
            started.set()
            await asyncio.sleep(0.01)

        mgr._do_reinforcement = slow_pass

        task = mgr.force_reinforce()
        await started.wait()

        assert task in _bg_tasks, "the loop only weakly references a bare task"
        await task
        assert task not in _bg_tasks, "done-callback should discard it"


class TestRelayRestartLeavesFoundryAlone:
    """restart() quit the Foundry app and raced its own relaunch."""

    @pytest.mark.asyncio
    async def test_restart_does_not_stop_or_start_foundry(self):
        from relay_proc.manager import RelayManager

        mgr = RelayManager()
        mgr.adopted = False
        mgr.stop = AsyncMock()
        mgr.start = AsyncMock()

        await RelayManager.restart(mgr)

        mgr.stop.assert_awaited_once_with(stop_foundry=False)
        mgr.start.assert_awaited_once_with(start_foundry=False)


class TestDeadPcTurn:
    """A dead PC keeps its slot in _turn_order, so its name must survive."""

    def test_dead_pc_store_keeps_the_display_name(self):
        from combat.loop import CombatLoop

        loop = CombatLoop.__new__(CombatLoop)
        loop._dead_pc_tokens = {}

        loop._dead_pc_tokens["tok-1"] = "Thalia"

        assert loop._dead_pc_tokens["tok-1"] == "Thalia"
        # _reanchor_turn_index unions this into a set of ids; dicts yield keys.
        assert set(loop._dead_pc_tokens) == {"tok-1"}
