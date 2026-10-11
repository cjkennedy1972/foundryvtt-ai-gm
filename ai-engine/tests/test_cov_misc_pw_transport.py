"""PlaywrightTransport lifecycle: connect, teardown, loss detection and reconnect (fake Playwright)."""

import asyncio
import sys
import types

import pytest

from config import settings
from foundry import playwright_transport as pt
from foundry.playwright_transport import PlaywrightTransport


class Page:
    def __init__(self):
        self.closed = False
        self.calls = []
        self.handlers = {}
        self.exposed = {}

    def on(self, evt, cb):
        self.handlers[evt] = cb

    def is_closed(self):
        return self.closed

    async def expose_function(self, name, fn):
        self.exposed[name] = fn

    async def goto(self, url, **kw):
        self.calls.append(("goto", url))

    async def wait_for_selector(self, sel, **kw):
        self.calls.append(("wait", sel))

    async def fill(self, sel, val):
        self.calls.append(("fill", sel, val))

    async def click(self, sel):
        self.calls.append(("click", sel))

    async def wait_for_function(self, js, **kw):
        self.calls.append(("ready", js))

    async def evaluate(self, script, arg=None):
        self.calls.append(("eval", arg))
        return True


class Browser:
    def __init__(self, close_error=None):
        self.page = Page()
        self.handlers = {}
        self.alive = True
        self.close_error = close_error
        self.closed = False

    def on(self, evt, cb):
        self.handlers[evt] = cb

    def is_connected(self):
        return self.alive

    async def new_context(self, **kw):
        outer = self

        class Ctx:
            async def new_page(self_inner):
                return outer.page
        return Ctx()

    async def close(self):
        self.closed = True
        if self.close_error:
            raise self.close_error


class PW:
    def __init__(self, browser, stop_error=None):
        self.browser = browser
        self.stopped = False
        self.stop_error = stop_error
        outer = self

        class Chromium:
            async def launch(self, **kw):
                return outer.browser
        self.chromium = Chromium()

    async def stop(self):
        self.stopped = True
        if self.stop_error:
            raise self.stop_error


@pytest.fixture
def fake_pw(monkeypatch):
    browser = Browser()
    pw = PW(browser)

    class Starter:
        async def start(self):
            return pw

    mod = types.ModuleType("playwright.async_api")
    mod.async_playwright = lambda: Starter()
    monkeypatch.setitem(sys.modules, "playwright", types.ModuleType("playwright"))
    monkeypatch.setitem(sys.modules, "playwright.async_api", mod)
    monkeypatch.setattr(settings, "foundry_password", "pw", raising=False)
    monkeypatch.setattr(settings, "foundry_username", "gm", raising=False)
    monkeypatch.setattr(settings, "foundry_url", "http://f:30000/", raising=False)
    return browser, pw


@pytest.mark.asyncio
async def test_connect_logs_in_installs_channels_and_notifies(fake_pw):
    browser, _ = fake_pw
    t = PlaywrightTransport()
    t._channels = {"hooks", "chat-events"}
    states = []
    t.on_state = states.append
    await t.connect()
    calls = browser.page.calls
    assert ("goto", "http://f:30000/join") in calls
    assert ("fill", "input[name=username]", "gm") in calls
    assert ("fill", "input[name=password]", "pw") in calls
    assert ("click", "button[name=join]") in calls
    installed = [c[1] for c in calls if c[0] == "eval" and c[1] in ("hooks", "chat-events")]
    assert installed == ["chat-events", "hooks"]                  # sorted, re-installed on connect
    assert "__aigmEmit" in browser.page.exposed
    assert t.connected and states == [True]
    await t.connect()                                              # already connected: no-op
    assert states == [True]


@pytest.mark.asyncio
async def test_connect_requires_credentials(fake_pw, monkeypatch):
    monkeypatch.setattr(settings, "foundry_password", "", raising=False)
    with pytest.raises(RuntimeError, match="FOUNDRY_PASSWORD"):
        await PlaywrightTransport().connect()
    monkeypatch.setattr(settings, "foundry_password", "pw", raising=False)
    monkeypatch.setattr(settings, "foundry_username", "", raising=False)
    with pytest.raises(RuntimeError, match="FOUNDRY_USERNAME"):
        await PlaywrightTransport().connect()


@pytest.mark.asyncio
async def test_failed_login_tears_the_browser_down(fake_pw):
    browser, pw = fake_pw

    async def boom(*a, **k):
        raise TimeoutError("login")
    browser.page.wait_for_function = boom
    t = PlaywrightTransport()
    with pytest.raises(TimeoutError):
        await t.connect()
    assert browser.closed and pw.stopped and t._page is None and t._browser is None


@pytest.mark.asyncio
async def test_close_cancels_reconnect_and_swallows_teardown_errors(fake_pw):
    browser, pw = fake_pw
    browser.close_error = RuntimeError("already dead")
    pw.stop_error = RuntimeError("stop failed")
    t = PlaywrightTransport()
    states = []
    t.on_state = states.append
    await t.connect()
    t._reconnect_task = asyncio.ensure_future(asyncio.sleep(60))
    task = t._reconnect_task
    await t.close()
    await asyncio.sleep(0)
    assert task.cancelled() and t._closing and t._page is None
    assert states == [True, False]


@pytest.mark.asyncio
async def test_browser_loss_schedules_one_reconnect_and_ignores_stale_events(fake_pw, monkeypatch):
    browser, _ = fake_pw
    t = PlaywrightTransport()
    states = []
    t.on_state = states.append
    await t.connect()
    started = []

    async def fake_loop():
        started.append(1)
        await asyncio.sleep(60)
    monkeypatch.setattr(t, "_reconnect_loop", fake_loop)

    t._on_browser_lost(object())                 # a different, replaced browser: ignored
    assert t._page is not None and not started
    browser.handlers["disconnected"]()           # the real one
    assert t._page is None and states == [True, False]
    first = t._reconnect_task
    t._page = browser.page                        # pretend it came back, then it drops again
    t._on_browser_lost(browser)
    assert t._reconnect_task is first             # a live reconnect task is not duplicated
    first.cancel()
    t._closing = True
    t._on_browser_lost(browser)                   # closing: no state change
    assert states == [True, False, False]


@pytest.mark.asyncio
async def test_page_crash_is_treated_as_loss(fake_pw, monkeypatch):
    browser, _ = fake_pw
    t = PlaywrightTransport()
    await t.connect()
    monkeypatch.setattr(t, "_reconnect_loop", lambda: asyncio.sleep(0))
    browser.page.handlers["crash"]()
    assert t._page is None
    await t._reconnect_task


@pytest.mark.asyncio
async def test_reconnect_loop_retries_then_succeeds(monkeypatch):
    t = PlaywrightTransport()
    sleeps, attempts = [], []

    async def fake_sleep(d):
        sleeps.append(d)

    async def connect():
        attempts.append(1)
        if len(attempts) < 3:
            raise RuntimeError("still down")
    monkeypatch.setattr(pt.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(t, "connect", connect)
    await t._reconnect_loop()
    assert sleeps == [2, 5, 10] and len(attempts) == 3


@pytest.mark.asyncio
async def test_reconnect_loop_gives_up_after_all_delays(monkeypatch, caplog):
    t = PlaywrightTransport()

    async def fake_sleep(d):
        pass

    async def connect():
        raise RuntimeError("down")
    monkeypatch.setattr(pt.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(t, "connect", connect)
    await t._reconnect_loop()
    assert "gave up reconnecting" in caplog.text


@pytest.mark.asyncio
async def test_enable_channel_connects_when_needed_and_records_channel(fake_pw):
    browser, _ = fake_pw
    t = PlaywrightTransport()
    assert await t.enable_channel("hooks") is True
    assert "hooks" in t._channels and t.connected
    assert ("eval", "hooks") in browser.page.calls


@pytest.mark.asyncio
async def test_page_event_without_listener_is_dropped_and_send_reconnects_lazily(fake_pw):
    browser, _ = fake_pw
    t = PlaywrightTransport()
    await t._on_page_event('{"type": "x"}')       # no on_event: no error
    res = await t.send("execute-js", script="return 1;")   # not connected yet: connects first
    assert res == {"result": True} and t.connected


@pytest.mark.asyncio
async def test_chat_send_page_error_is_wrapped_and_finished_reconnect_task_not_cancelled(fake_pw):
    browser, _ = fake_pw
    t = PlaywrightTransport()
    await t.connect()

    async def boom(script, arg=None):
        raise ValueError("page died")
    browser.page.evaluate = boom
    with pytest.raises(RuntimeError, match=r"Foundry error \[chat-send\]: page died"):
        await t.send("chat-send", content="hi")
    done = asyncio.ensure_future(asyncio.sleep(0))
    await done
    t._reconnect_task = done
    await t.close()                                 # a finished task is left alone
    assert not done.cancelled()
