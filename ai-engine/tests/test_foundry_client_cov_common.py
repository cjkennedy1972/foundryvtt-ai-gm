"""Shared fakes for the test_foundry_client_cov_* files (no tests live here)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from foundry.client import FoundryClient  # noqa: E402


class FakePW:
    """Stands in for PlaywrightTransport: records sends, replies from `replies` by message type."""

    def __init__(self, replies=None):
        self.replies = dict(replies or {})
        self.sent = []
        self.connected = True
        self.closed = False
        self.channels = []
        self.on_event = None
        self.on_state = None
        self.connect_error = None
        self.connects = 0

    async def connect(self):
        self.connects += 1
        if self.connect_error:
            raise self.connect_error

    async def close(self):
        self.closed = True

    async def enable_channel(self, channel):
        self.channels.append(channel)

    async def send(self, msg_type, **params):
        self.sent.append((msg_type, params))
        reply = self.replies.get(msg_type, {})
        if isinstance(reply, Exception):
            raise reply
        if callable(reply):
            return await reply(**params)
        return reply


def client_with_pw(replies=None):
    c = FoundryClient()
    c._pw = FakePW(replies)
    return c


