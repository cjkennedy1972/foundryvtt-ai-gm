import asyncio

import pytest

from foundry.playwright_transport import PlaywrightTransport


class _JoinPage:
    """Just the calls _join makes. `options` maps the dropdown's user name to its value."""

    def __init__(self, options=None):
        self.options = options
        self.calls = []

    async def wait_for_selector(self, selector, **k):
        self.calls.append(("wait", selector))

    async def query_selector(self, selector):
        self.calls.append(("query", selector))
        return None if self.options is None else object()

    async def evaluate(self, script, arg):
        self.calls.append(("evaluate", arg))
        return self.options.get(arg)

    async def select_option(self, selector, value):
        self.calls.append(("select", selector, value))

    async def fill(self, selector, value):
        self.calls.append(("fill", selector, value))

    async def click(self, selector):
        self.calls.append(("click", selector))


def _join(page, username="Chris", password="pw"):
    t = PlaywrightTransport()
    t._page = page
    asyncio.run(t._join(username, password))


def test_picks_the_user_from_the_dropdown_when_foundry_lists_them():
    page = _JoinPage(options={"Chris": "u-chris"})

    _join(page)

    assert ("select", "select[name=userId]", "u-chris") in page.calls
    assert ("fill", "input[name=password]", "pw") in page.calls
    assert page.calls[-1] == ("click", "button[name=join]")
    assert not any(c[0] == "fill" and c[1] == "input[name=username]" for c in page.calls)


def test_a_missing_user_in_the_dropdown_fails_clearly():
    page = _JoinPage(options={"Sara": "u-sara"})

    with pytest.raises(RuntimeError, match="no user named 'Chris'"):
        _join(page)
    assert not any(c[0] == "click" for c in page.calls)


def test_falls_back_to_the_typed_user_name_on_older_builds():
    page = _JoinPage(options=None)

    _join(page)

    assert ("fill", "input[name=username]", "Chris") in page.calls
    assert not any(c[0] == "select" for c in page.calls)
