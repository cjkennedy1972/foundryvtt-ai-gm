import asyncio
import time

from foundry import playwright_transport as pt
from foundry.playwright_transport import PlaywrightTransport


class _Page:
    def __init__(self, in_game):
        self.in_game = in_game
        self.calls = 0

    async def evaluate(self, script, arg=None):
        assert script == pt._IN_GAME_JS
        self.calls += 1
        return self.in_game


def _transport(in_game, verified_at):
    t = PlaywrightTransport()
    t._page = _Page(in_game)
    t._verified_at = verified_at
    events = {"teardown": 0, "connect": 0, "state": []}

    async def teardown():
        events["teardown"] += 1

    async def connect_unlocked():
        events["connect"] += 1

    t._teardown = teardown
    t._connect_unlocked = connect_unlocked
    t.on_state = events["state"].append
    return t, events


def test_a_session_not_from_connect_is_not_probed():
    t, events = _transport(in_game=False, verified_at=None)

    asyncio.run(t._ensure_in_game())

    assert t._page.calls == 0 and events["connect"] == 0


def test_a_recent_check_is_not_repeated():
    t, events = _transport(in_game=False, verified_at=time.monotonic())

    asyncio.run(t._ensure_in_game())

    assert t._page.calls == 0 and events["connect"] == 0


def test_an_in_game_page_only_refreshes_the_timestamp():
    stale = time.monotonic() - 3600
    t, events = _transport(in_game=True, verified_at=stale)

    asyncio.run(t._ensure_in_game())

    assert t._page.calls == 1
    assert t._verified_at > stale
    assert events["teardown"] == 0 and events["connect"] == 0


def test_a_page_back_on_the_join_screen_is_logged_in_again():
    t, events = _transport(in_game=False, verified_at=time.monotonic() - 3600)

    asyncio.run(t._ensure_in_game())

    assert events["teardown"] == 1 and events["connect"] == 1
    assert events["state"] == [False]


def test_a_page_that_cannot_be_evaluated_counts_as_out_of_game():
    t, events = _transport(in_game=False, verified_at=time.monotonic() - 3600)

    class Broken:
        async def evaluate(self, script, arg=None):
            raise RuntimeError("page closed")

    t._page = Broken()
    asyncio.run(t._ensure_in_game())

    assert events["connect"] == 1
