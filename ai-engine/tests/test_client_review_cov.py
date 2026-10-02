"""FoundryClient: connection edge cases, retry rules, routed reads, fallbacks and error shapes
that the existing client suites leave untested. Messages asserted here are the relay's real types
(see tests/fake_relay.py and relay protocol tests)."""
import asyncio
import json
import logging
from unittest.mock import AsyncMock

import pytest

import foundry.client as fc
from config import settings
from fake_relay import FakeRelay
from foundry import world_cli_reads
from foundry.client import FoundryClient


def run(coro):
    return asyncio.run(coro)


def _client(send=None, js=None):
    c = FoundryClient()
    c._send = AsyncMock(return_value=send if send is not None else {"data": {}})
    c._send_with_retry = AsyncMock(return_value=send if send is not None else {"data": {}})
    c.execute_js = AsyncMock(return_value=js if js is not None else {"result": None})
    return c


class FakeRouter:
    """Answers reads by adapter name; anything not listed returns None (= use the old path)."""
    reads = True
    writes = False

    def __init__(self, **answers):
        self.answers, self.calls = answers, []

    def tripped(self):
        return False

    async def read(self, adapter, *args):
        self.calls.append((adapter.__name__, args))
        return self.answers.get(adapter.__name__)


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setattr(settings, "relay_api_key", "valid-token")
    monkeypatch.setattr(settings, "relay_headless_client_id", "test-client", raising=False)


# ── connect ─────────────────────────────────────────────────────────────────

class FakeWS:
    def __init__(self, ack=None, hang=False):
        self.ack, self.hang, self.sent, self.closed = ack, hang, [], 0

    async def send(self, raw):
        self.sent.append(json.loads(raw))

    async def recv(self):
        if self.hang:
            raise asyncio.TimeoutError()
        return json.dumps(self.ack)

    async def close(self):
        self.closed += 1


def _patch_connect(monkeypatch, sockets):
    made = []

    async def connect(url):
        ws = sockets[min(len(made), len(sockets) - 1)]
        made.append(ws)
        return ws

    async def nosleep(_):
        pass

    monkeypatch.setattr(fc.websockets, "connect", connect)
    monkeypatch.setattr(fc.asyncio, "sleep", nosleep)
    return made


def test_a_handshake_that_times_out_closes_the_socket_and_retries_then_gives_up(monkeypatch):
    ws = FakeWS(hang=True)
    made = _patch_connect(monkeypatch, [ws])
    c = FoundryClient()
    assert run(c.connect(max_retries=3)) is False
    assert len(made) == 3 and ws.closed == 3 and c.is_connected is False
    assert ws.sent[0] == {"type": "auth", "token": "valid-token", "clientId": "test-client"}


def test_a_non_connected_ack_is_not_treated_as_a_session(monkeypatch):
    ws = FakeWS(ack={"type": "error", "error": "nope"})
    _patch_connect(monkeypatch, [ws])
    c = FoundryClient()
    assert run(c.connect(max_retries=1)) is False
    assert ws.closed == 1 and not c.is_connected and c._reader_task is None


def test_connect_failure_records_the_error_for_the_self_heal_check(monkeypatch):
    async def boom(url):
        raise OSError("refused")

    async def nosleep(_):
        pass

    monkeypatch.setattr(fc.websockets, "connect", boom)
    monkeypatch.setattr(fc.asyncio, "sleep", nosleep)
    c = FoundryClient()
    assert run(c.connect(max_retries=2)) is False and c._last_connect_error == "refused"


@pytest.mark.asyncio
async def test_connect_drops_stale_events_survives_a_dead_old_socket_and_resubscribes():
    async with FakeRelay() as relay:
        c = FoundryClient()
        c.ws_url = relay.ws_url
        c._event_queue.put_nowait(("chat-events", {"stale": True}))
        c._event_queue.put_nowait(("chat-events", {"stale": True}))

        class Dead:
            async def close(self):
                raise RuntimeError("already gone")

        c._ws = Dead()
        c._subscribed_channels = {"chat-events"}
        try:
            assert await c.connect(max_retries=1) is True
            assert c._event_queue.empty(), "events from the old connection must not be replayed"
            subs = [m for m in relay.received if m["type"] == "subscribe"]
            assert [m["channel"] for m in subs] == ["chat-events"]
            assert c.is_connected          # the relay answered the unknown subscribe with an error; connect still succeeded
        finally:
            await c.disconnect()


@pytest.mark.asyncio
async def test_a_crashed_reader_is_cleaned_up_before_reconnecting():
    c = FoundryClient()
    spawned = []
    c._spawn_background_task = lambda coro: (coro.close(), spawned.append(1))
    c._connected = True

    async def boom():
        raise RuntimeError("reader died")

    c._reader_task = asyncio.ensure_future(boom())
    await asyncio.sleep(0.01)
    await c.ensure_connected()
    assert c._connected is False and c._reader_task is None and spawned == [1]


@pytest.mark.asyncio
async def test_the_supervisor_heals_a_dropped_connection_and_survives_a_failed_attempt():
    c = FoundryClient()
    c._supervisor_interval = 0.01
    calls = []

    async def ensure():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("relay still down")
        c._closing = True            # stop after the second attempt

    c.ensure_connected = ensure
    await asyncio.wait_for(c._supervisor_loop(), timeout=2)
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_the_supervisor_leaves_a_healthy_connection_alone():
    c = FoundryClient()
    c._supervisor_interval = 0.01
    c._connected = True
    c._reader_task = asyncio.ensure_future(asyncio.sleep(10))
    c.ensure_connected = AsyncMock()

    async def stop():
        await asyncio.sleep(0.05)
        c._closing = True

    await asyncio.wait_for(asyncio.gather(c._supervisor_loop(), stop()), timeout=2)
    c._reader_task.cancel()
    c.ensure_connected.assert_not_called()


@pytest.mark.asyncio
async def test_disconnect_fails_pending_calls_with_a_connection_error():
    c = FoundryClient()
    fut = asyncio.get_running_loop().create_future()
    c._rpc_futures["r1"] = fut
    await c.disconnect()
    with pytest.raises(ConnectionError, match="Disconnected"):
        await fut
    assert c._rpc_futures == {}


@pytest.mark.asyncio
async def test_background_task_cancellation_reports_stragglers_and_clears_the_set(monkeypatch, caplog):
    c = FoundryClient()
    real_wait = asyncio.wait

    release = asyncio.Event()

    async def stubborn():
        while not release.is_set():
            try:
                await asyncio.sleep(0.01)
            except asyncio.CancelledError:
                pass

    async def quick(aws, timeout=None, **kw):
        return await real_wait(aws, timeout=0.05, **kw)

    monkeypatch.setattr(fc.asyncio, "wait", quick)
    t = c._spawn_background_task(stubborn())
    await asyncio.sleep(0.01)
    with caplog.at_level(logging.ERROR):
        await c.cancel_all_background_tasks()
    release.set()
    assert "did not complete" in caplog.text and c._background_tasks == set()
    await asyncio.wait_for(t, timeout=1)


@pytest.mark.asyncio
async def test_a_task_that_raises_while_cancelled_is_logged_not_propagated(caplog):
    c = FoundryClient()

    async def bad():
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            raise ValueError("cleanup blew up")

    c._spawn_background_task(bad())
    await asyncio.sleep(0.01)
    with caplog.at_level(logging.WARNING):
        await c.cancel_all_background_tasks()
    assert "cleanup blew up" in caplog.text


# ── reader / dispatch / event worker ────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_reader_crash_marks_disconnected_and_fails_waiters_instead_of_hanging_them():
    c = FoundryClient()
    c._connected = True

    class WS:
        async def recv(self):
            raise ValueError("garbled frame")

    c._ws = WS()
    fut = asyncio.get_running_loop().create_future()
    c._rpc_futures["r"] = fut
    await c._reader_loop()
    assert c._connected is False
    with pytest.raises(ConnectionError, match="Reader loop exited"):
        await fut
    assert c._rpc_futures == {}


@pytest.mark.asyncio
async def test_dispatch_routes_replies_events_and_ignores_junk():
    c = FoundryClient()
    seen = []

    async def handler(d):
        seen.append(d)

    c.subscribe("chat-events", handler)
    await c._dispatch_message("not json {{")
    fut = asyncio.get_running_loop().create_future()
    c._rpc_futures["r1"] = fut
    await c._dispatch_message(json.dumps({"requestId": "r1", "type": "x-result", "data": 1}))
    assert (await fut)["data"] == 1 and "r1" not in c._rpc_futures
    await c._dispatch_message(json.dumps({"type": "chat-event", "n": 1}))
    await c._dispatch_message(json.dumps({"type": "roll-event"}))          # nobody subscribed to roll-events
    await c._dispatch_message(json.dumps({"type": "mystery"}))
    await c._dispatch_message(json.dumps({"type": "chat-event", "requestId": "late-reply", "n": 2}))   # unknown id: treated as an event
    assert c._event_queue.qsize() == 2
    worker = asyncio.ensure_future(c._event_worker())
    await asyncio.sleep(0.02)
    worker.cancel()
    assert [d["n"] for d in seen] == [1, 2]


@pytest.mark.asyncio
async def test_one_failing_handler_does_not_stop_the_next_handler_or_the_worker():
    c = FoundryClient()
    seen = []

    async def bad(d):
        raise RuntimeError("handler bug")

    async def good(d):
        seen.append(d["n"])

    c.subscribe("hooks", bad)
    c.subscribe("hooks", good)
    c._event_queue.put_nowait(("hooks", {"n": 1}))
    c._event_queue.put_nowait(("hooks", {"n": 2}))
    worker = asyncio.ensure_future(c._event_worker())
    await asyncio.wait_for(c._event_queue.join(), timeout=2)
    worker.cancel()
    assert seen == [1, 2]


# ── _send / _send_with_retry / subscribe / hooks ────────────────────────────

@pytest.mark.asyncio
async def test_send_waits_for_a_reconnect_when_the_socket_is_down_and_posts_a_flat_payload():
    c = FoundryClient()
    sent = []

    class WS:
        async def send(self, raw):
            sent.append(json.loads(raw))
            await c._dispatch_message(json.dumps({"requestId": sent[-1]["requestId"], "type": "roll-result", "ok": 1}))

    async def revive(wait=15.0):
        c._ws, c._connected = WS(), True

    c._await_reconnect = revive
    out = await c._send("roll", formula="1d20")
    assert out["ok"] == 1
    assert sent[0]["type"] == "roll" and sent[0]["formula"] == "1d20" and sent[0]["requestId"].startswith("gm-1-")


@pytest.mark.asyncio
async def test_send_propagates_a_failed_reconnect():
    c = FoundryClient()
    c._await_reconnect = AsyncMock(side_effect=ConnectionError("Not connected to relay"))
    with pytest.raises(ConnectionError):
        await c._send("roll")


def _flaky(c, errors):
    calls = []

    async def send(msg_type, _timeout=None, **p):
        calls.append(msg_type)
        if errors and len(calls) <= len(errors):
            raise errors[len(calls) - 1]
        return {"ok": True}

    c._send = send
    return calls


def test_a_timed_out_rpc_is_retried_with_backoff(monkeypatch):
    slept = []

    async def sleep(s):
        slept.append(s)

    monkeypatch.setattr(fc.asyncio, "sleep", sleep)
    c = FoundryClient()
    calls = _flaky(c, [ConnectionError("RPC request x timed out")])
    assert run(c._send_with_retry("search", max_retries=2)) == {"ok": True}
    assert calls == ["search", "search"] and slept == [1]


def test_non_timeout_connection_errors_and_the_final_timeout_are_not_retried(monkeypatch):
    async def sleep(s):
        pass

    monkeypatch.setattr(fc.asyncio, "sleep", sleep)
    c = FoundryClient()
    calls = _flaky(c, [ConnectionError("Not connected to relay")])
    with pytest.raises(ConnectionError, match="Not connected"):
        run(c._send_with_retry("search", max_retries=3))
    assert calls == ["search"]
    c2 = FoundryClient()
    calls2 = _flaky(c2, [ConnectionError("timed out")] * 2)
    with pytest.raises(ConnectionError, match="timed out"):
        run(c2._send_with_retry("search", max_retries=2))
    assert len(calls2) == 2


GONE = RuntimeError("Foundry error [x]: Foundry client is no longer connected")


def test_a_dropped_foundry_client_is_waited_out_then_the_request_is_resent(monkeypatch):
    c = FoundryClient()
    calls = _flaky(c, [GONE])
    ticks = []

    async def sleep(s):
        ticks.append(s)
        if len(ticks) == 3:
            c._connected = True          # the self-heal reconnect landed

    monkeypatch.setattr(fc.asyncio, "sleep", sleep)
    assert run(c._send_with_retry("roll", max_retries=2)) == {"ok": True}
    assert calls == ["roll", "roll"] and len(ticks) == 3


def test_if_the_client_never_comes_back_the_original_error_is_raised(monkeypatch):
    c = FoundryClient()
    _flaky(c, [GONE])
    monkeypatch.setattr(fc, "_RECONNECT_WAIT_S", 2)

    async def sleep(s):
        pass

    monkeypatch.setattr(fc.asyncio, "sleep", sleep)
    with pytest.raises(RuntimeError, match="no longer connected"):
        run(c._send_with_retry("roll", max_retries=2))


def test_other_relay_errors_are_not_retried():
    c = FoundryClient()
    calls = _flaky(c, [RuntimeError("Foundry error [x]: bad request")])
    with pytest.raises(RuntimeError, match="bad request"):
        run(c._send_with_retry("roll", max_retries=3))
    assert len(calls) == 1


def test_subscribing_twice_sends_once_and_a_failed_subscribe_is_still_remembered_for_reconnect():
    c = _client()
    run(c.subscribe_to_channel("hooks"))
    run(c.subscribe_to_channel("hooks"))
    c._send.assert_awaited_once_with("subscribe", channel="hooks")
    c2 = _client()
    c2._send.side_effect = ConnectionError("down")
    run(c2.subscribe_to_channel("chat-events"))          # must not raise
    assert "chat-events" in c2._subscribed_channels


def test_wait_for_hook_times_out_false_and_tolerates_its_handler_being_removed():
    c = _client()

    async def go():
        async def clear():
            await asyncio.sleep(0.01)
            c._handlers["hooks"].clear()

        asyncio.ensure_future(clear())
        return await c.wait_for_hook("canvasReady", timeout=0.05)

    assert run(go()) is False


def test_activate_scene_installs_the_listener_before_activating_and_returns_the_result():
    c = _client()
    order = []

    async def activate(name):
        order.append(("activate", len(c._handlers.get("hooks", []))))
        await c._dispatch_message(json.dumps({"type": "hook", "hook": "canvasReady"}))
        return {"ok": name}

    c.set_active_scene = activate

    async def go():
        worker = asyncio.ensure_future(c._event_worker())
        r = await c.activate_scene_and_wait("Crypt", timeout=2)
        worker.cancel()
        return r

    assert run(go()) == {"ok": "Crypt"}
    assert order == [("activate", 1)] and c._handlers["hooks"] == []


def test_activate_scene_still_returns_when_canvas_ready_never_fires(caplog):
    c = _client()
    c.set_active_scene = AsyncMock(return_value={"ok": 1})
    assert run(c.activate_scene_and_wait("Crypt", timeout=0.02)) == {"ok": 1}
    assert c._handlers["hooks"] == []


# ── simple relay messages ───────────────────────────────────────────────────

def test_relay_message_types_and_param_names():
    c = _client()
    run(c.get_structure())
    c._send.assert_awaited_with("structure")
    run(c.search("goblin"))
    c._send.assert_awaited_with("search", query="goblin")
    run(c.play_sound("a/b.mp3", 0.3))
    c._send.assert_awaited_with("play-sound", src="a/b.mp3", volume=0.3)


@pytest.mark.parametrize("adv,expected", [(True, {"advantage": True}), (False, {"disadvantage": True}), (None, {})])
def test_death_save_advantage_is_tri_state(adv, expected):
    c = _client()
    run(c.request_death_save("Actor.1", advantage=adv))
    c._send.assert_awaited_once_with("death-save", actorUuid="Actor.1", **expected)


def test_playlist_volume_failure_does_not_lose_the_playlist_result():
    started = {"data": {"playlist": {"sounds": [{"id": "s1", "name": "A", "playing": True}, {"id": "s2", "playing": False}, {"playing": True}]}}}
    c = _client()

    async def send(t, **p):
        if t == "playlist-volume":
            raise RuntimeError("boom")
        return started

    c._send = send
    assert run(c.play_playlist("Tavern", 0.2)) is started


def test_playlist_volume_is_applied_to_playing_sounds_only():
    started = {"data": {"playlist": {"sounds": [{"id": "s1", "playing": True}, {"id": "s2", "playing": False}]}}}
    c = _client()
    sent = []

    async def send(t, **p):
        sent.append((t, p))
        return started

    c._send = send
    run(c.play_playlist("Tavern", 0.2))
    assert sent == [("playlist-play", {"playlistName": "Tavern"}),
                    ("playlist-volume", {"playlistName": "Tavern", "soundId": "s1", "volume": 0.2})]


def test_start_encounter_still_starts_when_token_selection_errors():
    c = _client()
    c.execute_js.side_effect = RuntimeError("js gate closed")
    run(c.start_encounter(["t1"], roll_all=True, name="Ambush"))
    c._send.assert_awaited_once_with("start-encounter", startWithSelected=True, rollAll=True, name="Ambush")


def test_start_encounter_without_tokens_asks_for_an_empty_encounter():
    c = _client()
    run(c.start_encounter([], roll_all=False))
    c._send.assert_awaited_once_with("start-encounter", tokens=[], rollAll=False)


def test_solo_death_setback_adds_exhaustion_only_after_foundry_confirms():
    c = _client(js={"result": {"ok": True, "hp": 1}})
    c.add_effect = AsyncMock(return_value={})
    assert run(c.apply_solo_death_setback("Actor.1")) == {"ok": True, "hp": 1, "exhaustion": True}
    c.add_effect.assert_awaited_once_with("Actor.1", "exhaustion")
    bad = _client(js={"result": {"ok": False, "error": "no actor"}})
    bad.add_effect = AsyncMock()
    with pytest.raises(RuntimeError, match="no actor"):
        run(bad.apply_solo_death_setback("Actor.1"))
    bad.add_effect.assert_not_called()
    none = _client(js={"result": None})
    with pytest.raises(RuntimeError, match="solo death setback failed"):
        run(none.apply_solo_death_setback("Actor.1"))


@pytest.mark.parametrize("method,args,default", [
    ("get_passive_perception", ("A",), {"passivePerception": 10}),
    ("check_spell_ritual", ("A", "Detect Magic"), {"isRitual": False}),
    ("get_spell_slots", ("A",), {}),
    ("get_multiattack_count", ("A",), {"count": 1, "description": ""}),
    ("get_legendary_resistance", ("A",), {"value": 0, "max": 0}),
    ("spend_legendary_resistance", ("A",), {"ok": False, "used": False}),
])
def test_script_reads_return_the_documented_default_when_foundry_fails(method, args, default):
    boom = _client()
    boom.execute_js.side_effect = RuntimeError("relay down")
    assert run(getattr(boom, method)(*args)) == default
    notdict = _client()
    notdict.execute_js.return_value = "junk"
    assert run(getattr(notdict, method)(*args)) == default


def test_script_reads_return_foundrys_result_when_it_answers():
    c = _client(js={"result": {"count": 3, "description": "three"}})
    assert run(c.get_multiattack_count("A")) == {"count": 3, "description": "three"}


def test_contested_check_reports_errors_as_data():
    c = _client()
    c.execute_js.side_effect = RuntimeError("nope")
    assert run(c.contested_check("A", "B")) == {"error": "nope"}
    c2 = _client()
    c2.execute_js.return_value = "junk"
    assert run(c2.contested_check("A", "B")) == {"error": "No result"}


# ── update_actor lookup strategies ──────────────────────────────────────────

def _actor_client():
    c = _client()
    c.get_scenes = AsyncMock(return_value=[])
    c.get_actors = AsyncMock(return_value=[])
    c.update_entity = AsyncMock(return_value={"updated": True})
    return c


def test_update_actor_finds_the_actor_through_a_scene_token_first():
    c = _actor_client()
    c.get_scenes = AsyncMock(return_value=[{"name": "Hall"}, {"name": "Crypt"}])
    c.get_scene_tokens = AsyncMock(side_effect=[[{"name": "Other"}], [{"name": "GOBLIN", "actorUuid": "Actor.g"}]])
    assert run(c.update_actor("goblin", {"img": "x.png"})) == {"updated": True}
    c.update_entity.assert_awaited_once_with(uuid="Actor.g", data={"img": "x.png"})
    c._send.assert_not_called()


def test_update_actor_survives_a_scene_scan_failure_and_uses_relay_search():
    c = _actor_client()
    c.get_scenes = AsyncMock(side_effect=RuntimeError("scan failed"))
    c._send.return_value = {"results": [{"documentType": "Item", "name": "Goblin", "uuid": "Item.1"},
                                        {"documentType": "Actor", "name": "goblin", "uuid": "Actor.g"}]}
    run(c.update_actor("Goblin", {"x": 1}))
    c.update_entity.assert_awaited_once_with(uuid="Actor.g", data={"x": 1})


def test_update_actor_falls_back_to_world_then_all_actors_then_gives_up_returning_none():
    c = _actor_client()
    c._send.side_effect = RuntimeError("search broken")
    c.get_actors = AsyncMock(side_effect=[[], [{"name": "Goblin", "uuid": "Compendium.g"}]])
    run(c.update_actor("Goblin", {}))
    assert [call.kwargs for call in c.get_actors.await_args_list] == [{"world_only": True}, {"world_only": False}]
    c.update_entity.assert_awaited_once_with(uuid="Compendium.g", data={})
    nf = _actor_client()
    nf._send.return_value = {"results": []}
    nf.get_actors = AsyncMock(return_value=[{"name": "Elf"}])
    assert run(nf.update_actor("Goblin", {})) is None
    nf.update_entity.assert_not_called()


# ── routed reads and their fallbacks ────────────────────────────────────────

def _routed(**answers):
    c = _client()
    c.world_cli_router = FakeRouter(**answers)
    return c


def test_routed_reads_return_the_adapter_answer_without_touching_the_relay():
    c = _routed(player_actor_mapping={"actor_names": {"H": "u"}, "actor_uuids": {}},
                world_metadata={"name": "W"}, scene_details={"data": {"x": 1}}, active_scene={"id": "s1", "name": "Hall"},
                scene_names=["A", "", "B"], scene_tokens=[{"id": "t"}], playlists=[{"id": "p"}], users=[{"id": "u"}],
                active_modules={"modules": []}, canvas_documents=[{"id": "w1"}], actor_dispositions={"Orc": -1})
    assert run(c.get_player_actor_mapping()) == {"actor_names": {"H": "u"}, "actor_uuids": {}}
    assert run(c._world_metadata()) == {"name": "W"}
    assert run(c.get_scene_details("Hall")) == {"data": {"x": 1}}
    assert run(c._get_active_scene_name()) == "Hall"
    assert run(c.get_active_scene_id()) == "s1"
    assert run(c.list_scene_names()) == ["A", "B"]                      # blanks dropped
    assert run(c.get_scene_tokens("Hall")) == [{"id": "t"}]
    assert run(c.get_playlists()) == [{"id": "p"}]
    assert run(c.get_users()) == [{"id": "u"}]
    assert run(c.get_active_modules_info(True)) == {"modules": []}
    assert run(c.canvas_get("walls")) == [{"id": "w1"}]
    assert run(c.get_actor_dispositions(["Orc"])) == {"Orc": -1}
    c._send.assert_not_called()
    c.execute_js.assert_not_called()


def test_scene_lookups_without_a_router_ask_foundry_and_reject_non_strings():
    c = _client(js={"result": "Crypt"})
    assert run(c._get_active_scene_name()) == "Crypt" and run(c.get_active_scene_id()) == "Crypt"
    c.execute_js.return_value = {"result": 42}
    assert run(c._get_active_scene_name()) is None and run(c.get_active_scene_id()) is None
    c.execute_js.side_effect = RuntimeError("x")
    assert run(c._get_active_scene_name()) is None and run(c.get_active_scene_id()) is None
    assert run(c.list_scene_names()) == []
    c2 = _client(js={"result": ["A", None, "B"]})
    assert run(c2.list_scene_names()) == ["A", "B"]


def test_get_scene_tokens_swallows_failures_into_an_empty_list():
    c = _client()
    c.get_scene_details = AsyncMock(side_effect=RuntimeError("boom"))
    assert run(c.get_scene_tokens("Hall")) == []


def test_get_actors_strict_raises_on_transport_failure_but_lenient_returns_empty():
    c = _client()
    c._send_with_retry.side_effect = ConnectionError("timed out")
    assert run(c.get_actors()) == []
    with pytest.raises(ConnectionError):
        run(c.get_actors(strict=True))


def test_active_modules_with_world_asks_foundry_for_both():
    c = _client()
    c.execute_js = AsyncMock(side_effect=[{"result": [{"id": "m", "active": True}]}, {"result": {"title": "T", "id": "w"}}])
    assert run(c.get_active_modules_info(include_world=True)) == {"modules": [{"id": "m", "active": True}], "world": {"title": "T", "id": "w"}}
    c2 = _client()
    c2.execute_js = AsyncMock(side_effect=[{"result": "junk"}, {"result": None}])
    assert run(c2.get_active_modules_info(include_world=True)) == {"modules": [], "world": {}}


# ── world scan ──────────────────────────────────────────────────────────────

def test_scan_world_normalises_each_document_kind_and_survives_per_kind_failures():
    c = _client()
    c._world_metadata = AsyncMock(return_value={"name": "W", "systems": [{"name": "dnd5e"}]})
    c.get_scenes = AsyncMock(return_value=[{"name": "Hall"}])
    c.get_actors = AsyncMock(return_value=[{"name": "A"}])

    async def docs(kind):
        if kind == "Item":
            raise RuntimeError("search failed")
        if kind == "JournalEntry":
            raise RuntimeError("search failed")
        return [{"title": "Siege", "id": "Combat.1", "active": True, "actors": [1, 2, 3], "round": 2}]

    c._search_documents = docs
    out = run(c.scan_world())
    assert out["items"] == [] and out["journal"] == [] and "error" not in out
    assert out["quests"] == [{"name": "Siege", "uuid": "Combat.1", "active": True, "tokenCount": 3, "round": 2, "turn": 0}]
    assert out["modules"] == [{"name": "dnd5e"}]


def test_scan_world_reports_a_fatal_error_but_still_returns_the_skeleton():
    c = _client()
    c._world_metadata = AsyncMock(side_effect=RuntimeError("no world"))
    out = run(c.scan_world())
    assert out["error"] == "no world" and out["scenes"] == [] and out["actors"] == []


def test_scan_world_item_and_journal_shapes():
    c = _client()
    c._world_metadata = AsyncMock(return_value={"systems": []})
    c.get_scenes = AsyncMock(return_value=[])
    c.get_actors = AsyncMock(return_value=[])
    c._search_documents = AsyncMock(side_effect=[
        [{"name": "Sword", "kind": "weapon", "data": {"subtype": "sword", "rarity": "rare"}, "id": "Item.1", "equipped": True}],
        [{"title": "Notes", "parentId": "f1"}], []])
    out = run(c.scan_world())
    assert out["items"] == [{"name": "Sword", "type": "weapon", "subtype": "sword", "uuid": "Item.1", "rarity": "rare", "equipped": True}]
    assert out["journal"] == [{"name": "Notes", "uuid": "", "parent": "f1", "sorting": 0}]


def test_addon_capabilities_classify_modules_and_flag_missing_fog_and_maps():
    c = _client()
    scan = {"scenes": [], "actors": [{"hp": 5}, {"hp": None}, {"hp": "?"}],
            "world": {"systems": [{"name": "Dynamic Lighting+"}, {"name": "Loot Tables"}, {"name": "Magic Missiles"},
                                  {"name": "Initiative Pro", "active": True}, {"name": "Weather"}]}}
    out = run(c.discover_addon_capabilities(scan))
    s = " ".join(out["suggestions"])
    assert "Lighting system 'Dynamic Lighting+'" in s and "Inventory/Loot system 'Loot Tables'" in s
    assert "Spell/Magic system 'Magic Missiles'" in s and "Combat Tracker add-on 'Initiative Pro'" in s
    assert "Weather" not in s and "No fog-of-war" in s and "No scenes/maps" in s
    assert out["actors_with_combat"] == 1 and out["modules"][3]["enabled"] is True


# ── context setters ─────────────────────────────────────────────────────────

def test_contexts_are_truncated_to_the_configured_limit(monkeypatch, caplog):
    monkeypatch.setattr(settings, "context_max_chars", 10)
    c = FoundryClient()
    run(c.set_npc_context("a" * 25))
    run(c.set_world_context("short"))
    run(c.set_ai_tone("grim"))
    assert c._npc_context == "a" * 10 and c._world_context == "short" and c._ai_tone == "grim"


# ── canvas ──────────────────────────────────────────────────────────────────

def test_canvas_get_returns_empty_on_a_relay_error_and_unwraps_result_keys():
    c = _client(send={"results": [{"id": 1}]})
    assert run(c.canvas_get("walls")) == [{"id": 1}]
    c._send.assert_awaited_with("get-canvas-documents", documentType="walls")
    c._send.side_effect = RuntimeError("bad")
    assert run(c.canvas_get("walls")) == []


def test_a_single_canvas_document_is_wrapped_in_a_list_for_world_cli(monkeypatch):
    c = _client()
    captured = {}

    class W:
        reads, writes = True, True

        def tripped(self):
            return False

    c.world_cli_router = W()
    c.get_active_scene_id = AsyncMock(return_value="s1")

    def fake_create(scene_id, doc_type, data):
        captured["args"] = (scene_id, doc_type, data)
        return ("pre", "exec")

    monkeypatch.setattr(fc.world_cli_writes, "canvas_create", fake_create)
    c._cli_write = AsyncMock(return_value={"created": 1})
    assert run(c.canvas_create("walls", {"c": [0, 0, 1, 1]})) == {"created": 1}
    assert captured["args"] == ("s1", "walls", [{"c": [0, 0, 1, 1]}])
    c._send.assert_not_called()


def test_canvas_create_falls_back_to_the_relay_with_the_foundry_class_name():
    c = _client()
    run(c.canvas_create("lights", {"x": 1}))
    kw = c._send.await_args
    assert kw.args == ("create-canvas-document",)
    assert kw.kwargs["className"] == "AmbientLight" and kw.kwargs["data"] == [{"x": 1}] and kw.kwargs["documentType"] == "lights"


# ── create_player_character ─────────────────────────────────────────────────

def test_player_character_spec_is_json_encoded_truncated_and_normalised():
    c = _client(js={"result": {"ok": True, "uuid": "Actor.1"}})
    hostile = '"; alert(1); //' + "x" * 100
    out = run(c.create_player_character({"name": hostile, "class": "Wizard", "concept": "c" * 2000}, user_id="u1"))
    assert out == {"ok": True, "uuid": "Actor.1"}
    script = c.execute_js.await_args.args[0]
    spec_line = next(line for line in script.splitlines() if line.startswith("const spec = "))
    spec = json.loads(spec_line[len("const spec = "):].rstrip(";"))
    assert len(spec["name"]) == 60 and spec["name"].startswith('"; alert(1)')      # data, not code
    assert spec["class"] == "wizard" and spec["race"] == "human" and spec["background"] == "folk-hero"
    assert len(spec["concept"]) == 1000 and spec["userId"] == "u1"


def test_player_character_failures_come_back_as_ok_false():
    c = _client()
    c.execute_js.side_effect = RuntimeError("js gate closed")
    assert run(c.create_player_character({})) == {"ok": False, "error": "js gate closed"}
    c2 = _client(js={"result": "junk"})
    assert run(c2.create_player_character({})) == {"ok": False, "error": "Invalid Foundry response"}


# ── move_token ──────────────────────────────────────────────────────────────

def test_move_token_falls_back_to_the_relay_when_execute_js_itself_fails():
    c = _client()
    c.execute_js.side_effect = RuntimeError("js gate closed")
    run(c.move_token("tok1", 100, 200))
    c._send.assert_awaited_once_with("move-token", tokenId="tok1", x=100, y=200)


def test_move_token_reports_a_blocked_move_and_escapes_the_token_reference():
    c = _client(js={"result": {"ok": False, "error": "Foundry did not move the token"}})
    out = run(c.move_token('x"+evil()+"', 1.5, 2))
    assert out == {"ok": False, "error": "Foundry did not move the token"}
    js = c.execute_js.await_args.args[0]
    assert 'const want="x\\"+evil()+\\"";' in js and "x:1.5,y:2.0" in js
    assert _client(js={"result": None}).execute_js is not None
    c2 = _client(js={})
    assert run(c2.move_token("t", 0, 0)) == {"ok": False, "error": "move failed"}


# ── place_token ─────────────────────────────────────────────────────────────

def _placer(actor=None, js_answers=None, tokens=None):
    c = _client()
    c.get_actors = AsyncMock(return_value=[actor or {"name": "Goblin", "uuid": "Actor.g1", "img": "g.png"}])
    c.get_scene_tokens = AsyncMock(return_value=tokens or [])
    c.canvas_create = AsyncMock(return_value={"created": True})
    answers = list(js_answers or [{"result": None}, {"result": None}])
    c.execute_js = AsyncMock(side_effect=answers)
    return c


def test_place_token_builds_token_data_with_level_art_and_actor_link_off():
    c = _placer(js_answers=[{"result": "lvl1"}, {"result": "protos/g.webp"}])
    assert run(c.place_token(actor_name="goblin", x=100, y=200, disposition=-1, hidden=True)) == {"created": True}
    kind, data = c.canvas_create.await_args.args
    assert kind == "tokens"
    assert data == {"name": "Goblin", "actorId": "g1", "actorLink": False, "x": 100, "y": 200, "disposition": -1, "hidden": True,
                    "width": 1, "height": 1, "texture": {"src": "protos/g.webp"}, "level": "lvl1"}


def test_place_token_gives_player_owned_actors_vision_and_uses_router_art():
    c = _placer(actor={"name": "Hero", "uuid": "Actor.h1", "has_player_owner": True})
    c.world_cli_router = FakeRouter(actor_image="hero.png")
    run(c.place_token(uuid="Actor.h1"))
    data = c.canvas_create.await_args.args[1]
    assert data["sight"] == {"enabled": True, "range": 60, "visionMode": "basic"}
    assert data["texture"] == {"src": "hero.png"}
    assert "level" not in data and c.world_cli_router.calls[-1] == ("actor_image", ("h1",))


def test_place_token_still_creates_when_level_art_and_dedup_lookups_fail():
    c = _placer()
    c.get_scene_tokens = AsyncMock(side_effect=RuntimeError("scene read failed"))
    c.execute_js = AsyncMock(side_effect=RuntimeError("js down"))
    run(c.place_token(actor_name="Goblin", x=1, y=2))
    data = c.canvas_create.await_args.args[1]
    assert data["texture"] == {"src": "g.png"} and "level" not in data       # actor's own img is the fallback


def test_place_token_moves_an_existing_token_instead_of_duplicating_and_reports_failure():
    c = _placer(tokens=[{"id": "t9", "actorUuid": "g1"}])
    c.move_token = AsyncMock(return_value={"ok": True})
    assert run(c.place_token(actor_name="Goblin", x=5, y=6)) == {"moved": True, "token_id": "t9", "actor": "Goblin"}
    c.move_token.assert_awaited_once_with("t9", 5, 6)
    c.canvas_create.assert_not_called()
    c.move_token = AsyncMock(return_value={"error": "blocked"})
    out = run(c.place_token(actor_name="Goblin", x=5, y=6))
    assert out["error"] == "Failed to move 'Goblin' token: blocked" and out["token_id"] == "t9"


def test_place_token_for_an_unknown_actor_creates_nothing():
    c = _placer()
    assert run(c.place_token(actor_name="Dragon")) == {"error": "Actor 'Dragon' not found"}
    c.canvas_create.assert_not_called()


# ── misc ────────────────────────────────────────────────────────────────────

def test_get_actor_dispositions_js_path_escapes_names_and_defaults_to_empty():
    c = _client(js={"result": {"Orc": -1}})
    assert run(c.get_actor_dispositions(["O'rc", 'a"b'])) == {"Orc": -1}
    js = c.execute_js.await_args.args[0]
    assert json.dumps(["O'rc", 'a"b']) in js
    assert run(c.get_actor_dispositions([])) == {}
    c.execute_js.side_effect = RuntimeError("x")
    assert run(c.get_actor_dispositions(["Orc"])) == {}


def test_message_id_counter_only_resets_when_nothing_is_in_flight():
    c = FoundryClient()
    c._message_id = 7
    c.reset_message_id()
    assert c._message_id == 0
    c._message_id = 7
    c._rpc_futures["r"] = object()
    c.reset_message_id()
    assert c._message_id == 7          # resetting would let a new id collide with the pending one


def test_clear_canvas_layer_rejects_unknown_layers_instead_of_running_them_as_js():
    c = _client()
    out = run(c.clear_canvas_layer("walls'); evil(); ('"))
    assert out["success"] is False and "Unknown canvas layer" in out["error"]
    c.execute_js.assert_not_called()


def test_clear_canvas_layer_falls_back_to_canvas_delete_when_js_fails():
    c = _client()
    c.execute_js.side_effect = RuntimeError("js down")
    c.canvas_delete = AsyncMock(return_value={"deleted": True})
    assert run(c.clear_canvas_layer("walls")) == {"deleted": True}
    c.canvas_delete.assert_awaited_once_with("walls")


def test_configure_scene_returns_an_error_dict_when_the_js_fails():
    c = _client()
    c.execute_js.side_effect = RuntimeError("js down")
    assert run(c.configure_scene({"darkness": 1})) == {"error": "js down"}
    assert world_cli_reads  # module is the one the client routes through
