"""Playwright transport: message routing and FoundryClient delegation, against a fake page.

No browser is launched; the live check is the spike in the scratch directory.
"""

import asyncio

import pytest

from foundry.client import FoundryClient
from foundry.playwright_transport import PlaywrightTransport


class FakePage:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    async def evaluate(self, script, arg=None):
        self.calls.append((script, arg))
        if self.error:
            raise self.error
        return self.result

    def is_closed(self):
        return False


def _transport(page) -> PlaywrightTransport:
    t = PlaywrightTransport()
    t._page = page
    t._browser = FakeBrowser()
    return t


def test_execute_js_wraps_value_as_relay_result():
    page = FakePage(result={"id": "w1"})
    result = asyncio.run(_transport(page).send("execute-js", script="return {id: 'w1'};"))
    assert result == {"result": {"id": "w1"}}
    assert page.calls[0][1] == "return {id: 'w1'};"


def test_execute_js_error_raises_like_relay():
    page = FakePage(error=ValueError("boom"))
    with pytest.raises(RuntimeError, match="execute-js"):
        asyncio.run(_transport(page).send("execute-js", script="throw 1"))


def test_chat_send_passes_content_and_whisper_names():
    page = FakePage(result={"id": "m1"})
    result = asyncio.run(
        _transport(page).send("chat-send", content="hi", speaker="", whisper=["Sara"])
    )
    assert result == {"id": "m1"}
    assert page.calls[0][1] == {"content": "hi", "whisper": ["Sara"]}


def test_unported_type_fails_loudly():
    with pytest.raises(NotImplementedError, match="create-folder"):
        asyncio.run(_transport(FakePage()).send("create-folder", name="Loot"))


def test_send_when_disconnected_raises():
    page = FakePage()
    page.is_closed = lambda: True
    with pytest.raises(ConnectionError):
        asyncio.run(_transport(page).send("execute-js", script="return 1;"))


def test_foundry_client_routes_execute_js_through_transport(monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "foundry_backend", "playwright")
    client = FoundryClient()
    client._pw = _transport(FakePage(result=42))
    client._connected = True
    res = asyncio.run(client.execute_js("return 42;"))
    assert res == {"result": 42}


def test_page_event_json_reaches_on_event():
    t = _transport(FakePage())
    seen = []

    async def sink(msg):
        seen.append(msg)

    t.on_event = sink
    asyncio.run(t._on_page_event('{"type": "hook", "hook": "pauseGame", "data": {"paused": true}}'))
    assert seen == [{"type": "hook", "hook": "pauseGame", "data": {"paused": True}}]


def test_chat_event_routes_to_channel_queue():
    client = FoundryClient()
    client.subscribe("chat-events", lambda data: None)
    msg = {"type": "chat-event", "data": {"eventType": "create", "data": {"content": "hi"}}}
    asyncio.run(client._on_playwright_event(msg))
    assert client._event_queue.get_nowait() == ("chat-events", msg)


def test_subscribe_enables_channel_once_on_playwright(monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "foundry_backend", "playwright")
    calls = []

    class FakeTransport:
        async def enable_channel(self, channel):
            calls.append(channel)
            return True

    client = FoundryClient()
    client._pw = FakeTransport()
    asyncio.run(client.subscribe_to_channel("combat-events"))
    asyncio.run(client.subscribe_to_channel("combat-events"))
    assert calls == ["combat-events"]


def test_get_users_returns_module_envelope():
    page = FakePage(result=[{"id": "u1", "name": "ai-gm", "isGM": True}])
    result = asyncio.run(_transport(page).send("get-users"))
    assert result == {"type": "get-users-result", "data": [{"id": "u1", "name": "ai-gm", "isGM": True}]}
    assert page.calls[0][1] == ["getUsers", {}]


def test_playlist_play_passes_name_to_its_op():
    page = FakePage(result={"playlist": {"id": "p1", "sounds": []}})
    result = asyncio.run(_transport(page).send("playlist-play", playlistName="Ambience"))
    assert result["data"] == {"playlist": {"id": "p1", "sounds": []}}
    assert page.calls[0][1] == ["playlistPlay", {"playlistName": "Ambience"}]


def test_play_sound_passes_src_and_volume():
    page = FakePage(result={"src": "a.ogg", "volume": 0.3, "loop": False})
    asyncio.run(_transport(page).send("play-sound", src="a.ogg", volume=0.3))
    assert page.calls[0][1] == ["playSound", {"src": "a.ogg", "volume": 0.3}]


def test_module_op_error_raises_with_message_type():
    page = FakePage(error=ValueError("Playlist not found: Nope"))
    with pytest.raises(RuntimeError, match="playlist-volume.*Playlist not found"):
        asyncio.run(_transport(page).send("playlist-volume", playlistName="Nope", volume=0.5))


@pytest.mark.parametrize(
    "msg_type, op, params",
    [
        ("get-scene", "getScene", {"name": "Tavern"}),
        ("get-canvas-documents", "getCanvasDocuments", {"documentType": "walls"}),
        ("update-canvas-document", "updateCanvasDocument",
         {"documentType": "tokens", "className": "Token", "uuid": "Scene.s1.Token.t1", "data": {"x": 100}}),
        ("delete-canvas-document", "deleteCanvasDocument",
         {"documentType": "walls", "className": "Wall", "ids": ["w1", "w2"]}),
        ("move-token", "moveToken", {"tokenId": "t1", "x": 300, "y": 400}),
    ],
)
def test_canvas_and_scene_ops_get_their_params(msg_type, op, params):
    page = FakePage(result={"ok": True})
    result = asyncio.run(_transport(page).send(msg_type, **params))
    assert result == {"type": f"{msg_type}-result", "data": {"ok": True}}
    assert page.calls[0][1] == [op, params]


@pytest.mark.parametrize(
    "msg_type, op, params",
    [
        ("create", "createEntity", {"entityType": "Actor", "data": {"name": "Gnoll"}}),
        ("update", "updateEntity", {"uuid": "Actor.a1", "data": {"name": "Gnoll x"}}),
        ("increase", "increaseAttribute", {"attribute": "system.attributes.hp.value", "amount": 3, "uuid": "Actor.a1"}),
        ("decrease", "decreaseAttribute", {"attribute": "system.attributes.hp.value", "amount": 2, "uuid": "Actor.a1"}),
        ("update-scene", "updateScene", {"name": "Tavern", "data": {"grid": {"size": 120}}}),
    ],
)
def test_entity_and_attribute_ops_keep_module_shape(msg_type, op, params):
    # The module puts create/update/increase/decrease results at the top level, not under data.
    page = FakePage(result={"uuid": "Actor.a1", "results": []})
    result = asyncio.run(_transport(page).send(msg_type, **params))
    if msg_type in ("create", "update", "increase", "decrease"):
        assert result == {"type": f"{msg_type}-result", "uuid": "Actor.a1", "results": []}
    else:
        assert result == {"type": f"{msg_type}-result", "data": {"uuid": "Actor.a1", "results": []}}
    assert page.calls[0][1] == [op, params]


@pytest.mark.parametrize(
    "msg_type, op, params",
    [
        ("short-rest", "shortRest", {"actorUuid": "Actor.a1", "autoHD": True}),
        ("long-rest", "longRest", {"actorUuid": "Actor.a1", "newDay": True}),
        ("ability-save", "abilitySave", {"actorUuid": "Actor.a1", "ability": "dex"}),
        ("death-save", "deathSave", {"actorUuid": "Actor.a1"}),
        ("break-concentration", "breakConcentration", {"actorUuid": "Actor.a1"}),
        ("add-effect", "addEffect", {"uuid": "Actor.a1", "statusId": "blinded"}),
    ],
)
def test_dnd5e_ops_wrap_data(msg_type, op, params):
    page = FakePage(result={"actorUuid": "Actor.a1"})
    result = asyncio.run(_transport(page).send(msg_type, **params))
    assert result == {"type": f"{msg_type}-result", "data": {"actorUuid": "Actor.a1"}}
    assert page.calls[0][1] == [op, params]


@pytest.mark.parametrize(
    "msg_type, op, params",
    [
        ("start-encounter", "startEncounter", {"name": "Ambush", "rollAll": False, "startWithSelected": True}),
        ("end-encounter", "endEncounter", {}),
    ],
)
def test_encounter_ops_keep_flat_shape(msg_type, op, params):
    page = FakePage(result={"encounterId": "c1"})
    result = asyncio.run(_transport(page).send(msg_type, **params))
    assert result == {"type": f"{msg_type}-result", "encounterId": "c1"}
    assert page.calls[0][1] == [op, params]


def test_search_and_structure_keep_module_shape():
    page = FakePage(result={"query": "dra", "filter": None, "results": []})
    result = asyncio.run(_transport(page).send("search", query="dra"))
    assert result == {"type": "search-result", "query": "dra", "filter": None, "results": []}
    assert page.calls[0][1] == ["search", {"query": "dra"}]

    page = FakePage(result={"folders": {}})
    result = asyncio.run(_transport(page).send("structure", types="Actor"))
    assert result == {"type": "structure-result", "data": {"folders": {}}}
    assert page.calls[0][1] == ["structure", {"types": "Actor"}]


class FakeBrowser:
    def __init__(self):
        self.handlers = {}
        self.closed = False

    def is_connected(self):
        return not self.closed

    def on(self, event, handler):
        self.handlers.setdefault(event, []).append(handler)

    async def close(self):
        self.closed = True


def _live_transport(page=None, browser=None):
    t = PlaywrightTransport()
    t._page = page or FakePage(result={"ok": True})
    t._browser = browser or FakeBrowser()
    return t


def test_send_reconnects_lazily_before_the_call():
    t = PlaywrightTransport()
    calls = []

    async def fake_connect():
        calls.append(1)
        t._page = FakePage(result=7)
        t._browser = FakeBrowser()

    t.connect = fake_connect
    result = asyncio.run(t.send("execute-js", script="return 7;"))
    assert result == {"result": 7}
    assert calls == [1]


def test_send_reports_connection_error_when_reconnect_fails():
    t = PlaywrightTransport()

    async def failing_connect():
        raise RuntimeError("login rejected")

    t.connect = failing_connect
    with pytest.raises(ConnectionError, match="login rejected"):
        asyncio.run(t.send("execute-js", script="return 1;"))


def test_losing_the_current_browser_marks_disconnected_and_notifies():
    t = _live_transport()
    states = []
    t.on_state = states.append
    t._RECONNECT_DELAYS = (60,)  # keep the scheduled reconnect asleep for this test
    browser = t._browser

    async def run():
        t._on_browser_lost(browser)
        assert not t.connected
        task = t._reconnect_task
        assert task is not None and not task.done()
        task.cancel()

    asyncio.run(run())
    assert states == [False]


def test_stale_browser_disconnect_is_ignored():
    t = _live_transport()
    states = []
    t.on_state = states.append
    old = FakeBrowser()
    t._on_browser_lost(old)  # not the current browser
    assert states == []
    assert t.connected


def test_reconnect_loop_retries_until_connect_succeeds():
    t = PlaywrightTransport()
    t._RECONNECT_DELAYS = (0, 0, 0)
    attempts = []

    async def flaky_connect():
        attempts.append(1)
        if len(attempts) < 3:
            raise RuntimeError("not yet")
        t._page = FakePage()
        t._browser = FakeBrowser()

    t.connect = flaky_connect
    asyncio.run(t._reconnect_loop())
    assert len(attempts) == 3


def test_enabled_channels_are_remembered_for_reconnect():
    t = _live_transport()
    asyncio.run(t.enable_channel("chat-events"))
    asyncio.run(t.enable_channel("hooks"))
    assert t._channels == {"chat-events", "hooks"}
