"""FoundryClient connection lifecycle, transport send/retry, event routing and hooks."""
import asyncio

import pytest

import foundry.client as client_mod
import foundry.playwright_transport as pt_mod
from foundry.client import FoundryClient
from test_foundry_client_cov_common import FakePW, client_with_pw


def run(coro):
    return asyncio.run(coro)


# ── connect / ensure_connected / disconnect ──────────────────────────────────

def test_connect_retries_with_doubling_backoff_then_succeeds(monkeypatch):
    sleeps = []

    async def fake_sleep(d):
        sleeps.append(d)

    monkeypatch.setattr(client_mod.asyncio, "sleep", fake_sleep)
    c = FoundryClient()
    results = iter([False, False, True])

    async def attempt():
        return next(results)

    c._connect_playwright = attempt
    c._closing = True
    assert run(c.connect(max_retries=5)) is True
    assert sleeps == [1.0, 2.0]
    assert c._closing is False


def test_connect_gives_up_after_max_retries_without_trailing_sleep(monkeypatch):
    sleeps = []

    async def fake_sleep(d):
        sleeps.append(d)

    monkeypatch.setattr(client_mod.asyncio, "sleep", fake_sleep)
    c = FoundryClient()
    calls = []

    async def attempt():
        calls.append(1)
        return False

    c._connect_playwright = attempt
    assert run(c.connect(max_retries=3)) is False
    assert len(calls) == 3
    assert sleeps == [1.0, 2.0]  # no sleep after the final failure


def test_connect_backoff_is_capped_at_30s(monkeypatch):
    sleeps = []

    async def fake_sleep(d):
        sleeps.append(d)

    monkeypatch.setattr(client_mod.asyncio, "sleep", fake_sleep)
    c = FoundryClient()

    async def attempt():
        return False

    c._connect_playwright = attempt
    run(c.connect(max_retries=9))
    assert max(sleeps) == 30.0


def test_ensure_connected_reconnects_when_transport_missing_or_down():
    c = FoundryClient()
    seen = []

    async def fake_connect(max_retries=5):
        seen.append(max_retries)

    c.connect = fake_connect
    run(c.ensure_connected())  # no transport
    c._pw = FakePW()
    c._pw.connected = False
    run(c.ensure_connected())  # transport down
    assert seen == [1, 1]


def test_ensure_connected_is_noop_when_up():
    c = client_with_pw()
    seen = []

    async def fake_connect(max_retries=5):
        seen.append(1)

    c.connect = fake_connect
    run(c.ensure_connected())
    assert seen == []


def test_disconnect_closes_transport_cancels_worker_and_marks_closing():
    async def scenario():
        c = FoundryClient()
        pw = FakePW()
        c._pw = pw
        c._connected = True
        c._event_worker_task = c._spawn_background_task(c._event_worker())
        await asyncio.sleep(0)
        await c.disconnect()
        return c, pw

    c, pw = run(scenario())
    assert pw.closed is True
    assert c._pw is None
    assert c._connected is False
    assert c._closing is True
    assert c._event_worker_task is None
    assert not c._background_tasks


def test_disconnect_without_transport_or_worker():
    c = FoundryClient()
    run(c.disconnect())
    assert c._closing is True and c._pw is None


# ── background tasks ─────────────────────────────────────────────────────────

def test_spawn_background_task_tracks_then_discards_on_completion():
    async def scenario():
        c = FoundryClient()

        async def work():
            return 7

        t = c._spawn_background_task(work())
        assert t in c._background_tasks
        assert await t == 7
        await asyncio.sleep(0)
        return c

    assert run(scenario())._background_tasks == set()


def test_cancel_all_background_tasks_noop_when_empty():
    c = FoundryClient()
    run(c.cancel_all_background_tasks())
    assert c._background_tasks == set()


def test_cancel_all_background_tasks_cancels_running_and_logs_task_errors(caplog):
    async def scenario():
        c = FoundryClient()

        async def forever():
            await asyncio.sleep(60)

        async def boom():
            raise ValueError("kaput")

        running = c._spawn_background_task(forever())
        failed = c._spawn_background_task(boom())
        await asyncio.sleep(0)  # boom finishes with an exception, still tracked by reference
        c._background_tasks.add(failed)
        await c.cancel_all_background_tasks()
        return c, running

    with caplog.at_level("WARNING"):
        c, running = run(scenario())
    assert running.cancelled()
    assert c._background_tasks == set()
    assert any("kaput" in r.message for r in caplog.records)


def test_cancel_all_background_tasks_reports_tasks_that_ignore_cancellation(monkeypatch, caplog):
    real_wait = asyncio.wait

    async def short_wait(tasks, timeout=None):
        return await real_wait(tasks, timeout=0.05)

    monkeypatch.setattr(client_mod.asyncio, "wait", short_wait)

    async def scenario():
        c = FoundryClient()
        release = asyncio.Event()

        async def stubborn():
            while not release.is_set():
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    pass

        t = c._spawn_background_task(stubborn())
        await asyncio.sleep(0)
        await c.cancel_all_background_tasks()
        release.set()
        await t
        return c

    with caplog.at_level("ERROR"):
        c = run(scenario())
    assert c._background_tasks == set()
    assert any("did not complete" in r.message for r in caplog.records)


def test_cancel_all_background_tasks_swallows_shutdown_runtime_error(monkeypatch):
    async def bad_wait(tasks, timeout=None):
        raise RuntimeError("loop closing")

    monkeypatch.setattr(client_mod.asyncio, "wait", bad_wait)

    async def scenario():
        c = FoundryClient()

        async def forever():
            await asyncio.sleep(60)

        t = c._spawn_background_task(forever())
        await asyncio.sleep(0)
        await c.cancel_all_background_tasks()
        return c, t

    c, t = run(scenario())
    assert c._background_tasks == set()


# ── event worker / dispatch ──────────────────────────────────────────────────

def test_event_worker_runs_handlers_in_order_and_isolates_handler_errors(caplog):
    seen = []

    async def scenario():
        c = FoundryClient()

        async def bad(data):
            raise RuntimeError("handler blew up")

        async def good(data):
            seen.append(data["n"])

        c.subscribe("chat-events", bad)
        c.subscribe("chat-events", good)
        worker = asyncio.create_task(c._event_worker())
        c._event_queue.put_nowait(("chat-events", {"n": 1}))
        c._event_queue.put_nowait(("chat-events", {"n": 2}))
        c._event_queue.put_nowait(("nobody-listens", {"n": 3}))
        await asyncio.wait_for(c._event_queue.join(), 2)
        worker.cancel()

    with caplog.at_level("ERROR"):
        run(scenario())
    assert seen == [1, 2]  # a failing handler does not stop its sibling or later events
    assert sum("Handler error on channel chat-events" in r.message for r in caplog.records) == 2


def test_dispatch_event_queues_only_registered_known_channels():
    async def scenario():
        c = FoundryClient()

        async def h(_):
            pass

        c.subscribe("chat-events", h)
        await c._dispatch_event({"type": "chat-event", "x": 1})
        await c._dispatch_event({"type": "roll-event"})       # known type, nobody subscribed
        await c._dispatch_event({"type": "mystery-event"})    # unknown type
        await c._dispatch_event({})
        return c

    c = run(scenario())
    assert c._event_queue.qsize() == 1
    assert c._event_queue.get_nowait() == ("chat-events", {"type": "chat-event", "x": 1})


def test_playwright_event_and_state_callbacks():
    async def scenario():
        c = FoundryClient()

        async def h(_):
            pass

        c.subscribe("hooks", h)
        await c._on_playwright_event({"type": "hook", "hook": "canvasReady"})
        c._on_playwright_state(True)
        up = c.is_connected
        c._on_playwright_state(False)
        return c, up

    c, up = run(scenario())
    assert c._event_queue.get_nowait()[0] == "hooks"
    assert up is True and c.is_connected is False


# ── _connect_playwright ──────────────────────────────────────────────────────

def test_connect_playwright_success_creates_transport_wires_callbacks_and_starts_worker(monkeypatch):
    created = []

    class T(FakePW):
        def __init__(self):
            super().__init__()
            created.append(self)

    monkeypatch.setattr(pt_mod, "PlaywrightTransport", T)

    async def scenario():
        c = FoundryClient()
        ok = await c._connect_playwright()
        first_worker = c._event_worker_task
        ok2 = await c._connect_playwright()  # worker alive: not replaced
        same = c._event_worker_task is first_worker
        await c.disconnect()
        return c, ok, ok2, same

    c, ok, ok2, same = run(scenario())
    assert ok and ok2 and same
    assert len(created) == 1  # transport reused on the second connect
    assert created[0].connects == 2
    assert created[0].on_state == c._on_playwright_state
    assert created[0].on_event == c._on_playwright_event


def test_connect_playwright_restarts_finished_worker(monkeypatch):
    monkeypatch.setattr(pt_mod, "PlaywrightTransport", FakePW)

    async def scenario():
        c = FoundryClient()
        await c._connect_playwright()
        old = c._event_worker_task
        old.cancel()
        await asyncio.gather(old, return_exceptions=True)
        await c._connect_playwright()
        new = c._event_worker_task
        await c.disconnect()
        return old, new

    old, new = run(scenario())
    assert new is not old


def test_connect_playwright_failure_records_error_and_stays_disconnected(monkeypatch):
    class Boom(FakePW):
        def __init__(self):
            super().__init__()
            self.connect_error = RuntimeError("no browser")

    monkeypatch.setattr(pt_mod, "PlaywrightTransport", Boom)
    c = FoundryClient()
    assert run(c._connect_playwright()) is False
    assert c._last_connect_error == "no browser"
    assert c.is_connected is False
    assert c._event_worker_task is None


# ── _send / _send_with_retry ─────────────────────────────────────────────────

def test_send_without_transport_raises_connection_error():
    with pytest.raises(ConnectionError, match="Not connected"):
        run(FoundryClient()._send("structure"))


def test_send_passes_params_and_returns_reply():
    c = client_with_pw({"roll": {"total": 4}})
    assert run(c._send("roll", formula="1d4")) == {"total": 4}
    assert c._pw.sent == [("roll", {"formula": "1d4"})]


def test_send_times_out_as_connection_error_using_override(monkeypatch):
    async def never(**kw):
        await asyncio.sleep(10)

    c = client_with_pw({"x": never})
    with pytest.raises(ConnectionError, match="RPC request x timed out"):
        run(c._send("x", _timeout=0.01))


def test_send_uses_configured_default_timeout(monkeypatch):
    monkeypatch.setattr(client_mod.settings, "relay_rpc_timeout", 0.01)

    async def never(**kw):
        await asyncio.sleep(10)

    c = client_with_pw({"x": never})
    with pytest.raises(ConnectionError, match="timed out"):
        run(c._send("x"))


def test_send_raises_runtime_error_on_error_reply():
    c = client_with_pw({"roll": {"error": "bad formula"}})
    with pytest.raises(RuntimeError, match=r"Foundry error \[roll\]: bad formula"):
        run(c._send("roll"))


def test_send_returns_non_dict_reply_untouched():
    c = client_with_pw({"get-users": [1, 2]})
    assert run(c._send("get-users")) == [1, 2]


def test_send_with_retry_retries_timeout_with_backoff_then_succeeds(monkeypatch):
    sleeps = []

    async def fake_sleep(d):
        sleeps.append(d)

    monkeypatch.setattr(client_mod.asyncio, "sleep", fake_sleep)
    c = FoundryClient()
    attempts = []

    async def flaky(msg_type, _timeout=None, **params):
        attempts.append((msg_type, _timeout, params))
        if len(attempts) < 3:
            raise ConnectionError(f"RPC request {msg_type} timed out")
        return {"ok": 1}

    c._send = flaky
    assert run(c._send_with_retry("search", max_retries=3, _timeout=5, q="a")) == {"ok": 1}
    assert sleeps == [1, 2]
    assert attempts[0] == ("search", 5, {"q": "a"})


def test_send_with_retry_raises_after_last_attempt(monkeypatch):
    async def fake_sleep(d):
        pass

    monkeypatch.setattr(client_mod.asyncio, "sleep", fake_sleep)
    c = FoundryClient()
    n = []

    async def always(msg_type, _timeout=None, **params):
        n.append(1)
        raise ConnectionError("x timed out")

    c._send = always
    with pytest.raises(ConnectionError, match="timed out"):
        run(c._send_with_retry("x", max_retries=2))
    assert len(n) == 2


def test_send_with_retry_does_not_retry_non_timeout_errors():
    c = FoundryClient()
    n = []

    async def down(msg_type, _timeout=None, **params):
        n.append(1)
        raise ConnectionError("Not connected to Foundry")

    c._send = down
    with pytest.raises(ConnectionError, match="Not connected"):
        run(c._send_with_retry("x", max_retries=3))
    assert len(n) == 1


# ── subscribe / channels ─────────────────────────────────────────────────────

def test_subscribe_to_channel_enables_once():
    c = client_with_pw()
    run(c.subscribe_to_channel("hooks"))
    run(c.subscribe_to_channel("hooks"))
    assert c._pw.channels == ["hooks"]


def test_subscribe_to_channel_failure_is_forgotten_so_it_retries():
    c = FoundryClient()  # no transport
    run(c.subscribe_to_channel("chat-events"))
    assert "chat-events" not in c._subscribed_channels
    c._pw = FakePW()
    run(c.subscribe_to_channel("chat-events"))
    assert c._pw.channels == ["chat-events"]
    assert "chat-events" in c._subscribed_channels


def test_subscribe_registers_handlers_in_order():
    c = FoundryClient()

    async def a(_):
        pass

    async def b(_):
        pass

    c.subscribe("hooks", a)
    c.subscribe("hooks", b)
    assert c._handlers["hooks"] == [a, b]
