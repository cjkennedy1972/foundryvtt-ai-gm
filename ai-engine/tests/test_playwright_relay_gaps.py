"""Message types the relay used to serve that the playwright transport now handles itself."""

import asyncio

import pytest

from foundry.client import FoundryClient
from foundry.playwright_transport import HANDLERS, PlaywrightTransport


class FakePage:
    def __init__(self, result=None):
        self.result = result
        self.calls = []

    async def evaluate(self, script, arg=None):
        self.calls.append((script, arg))
        return self.result

    def is_closed(self):
        return False


class FakeBrowser:
    def is_connected(self):
        return True


def _transport(result=None) -> tuple[PlaywrightTransport, FakePage]:
    t = PlaywrightTransport()
    page = FakePage(result=result)
    t._page = page
    t._browser = FakeBrowser()
    return t, page


def test_remove_effect_is_handled_by_the_transport():
    assert "remove-effect" in HANDLERS
    t, page = _transport(result={"removed": True})
    result = asyncio.run(t.send("remove-effect", uuid="Actor.a1", statusId="blinded"))
    assert result == {"type": "remove-effect-result", "data": {"removed": True}}
    assert page.calls[0][1] == ["removeEffect", {"uuid": "Actor.a1", "statusId": "blinded"}]


def test_upload_file_is_handled_by_the_transport():
    assert "upload-file" in HANDLERS
    t, page = _transport(result={"path": "ai-gm-maps/x.png"})
    asyncio.run(t.send("upload-file", path="ai-gm-maps", filename="x.png", source="data",
                       mimeType="image/png", overwrite=True, fileData="data:image/png;base64,AAAA"))
    op, params = page.calls[0][1]
    assert op == "uploadFile"
    assert params["fileData"].startswith("data:image/png;base64,")
    assert params["path"] == "ai-gm-maps"


def test_client_upload_file_returns_the_saved_path(monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "foundry_backend", "playwright")
    client = FoundryClient()
    t, page = _transport(result={"path": "ai-gm-maps/x.png", "status": "success"})
    client._pw = t
    client._connected = True
    saved = asyncio.run(client.upload_file(b"\x89PNG", "ai-gm-maps", "x.png"))
    assert saved == {"path": "ai-gm-maps/x.png", "status": "success"}
    op, params = page.calls[0][1]
    assert op == "uploadFile"
    assert params["fileData"].startswith("data:image/png;base64,")


@pytest.mark.parametrize("msg_type", ["remove-effect", "upload-file"])
def test_unported_guard_still_applies_to_others(msg_type):
    # Sanity: the table is the only route in; nothing bypasses the connected check.
    t = PlaywrightTransport()
    with pytest.raises(ConnectionError):
        asyncio.run(t.send(msg_type, uuid="Actor.a1", statusId="x", path="p", filename="f",
                           source="data", mimeType="image/png", overwrite=True, fileData="data:,"))
