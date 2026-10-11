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


class FakeRouter:
    """World CLI router double. reads: {adapter: value | callable(*args)}; records every call."""

    def __init__(self, reads=None, writes=True, write_result="WRITE-OK", tripped=False):
        self.reads = reads or {}
        self.writes = writes
        self._tripped = tripped
        self.write_result = write_result
        self.read_calls = []
        self.write_calls = []

    def tripped(self):
        return self._tripped

    async def read(self, adapter, *args):
        self.read_calls.append((adapter, args))
        value = self.reads.get(adapter)
        return value(*args) if callable(value) else value

    async def write(self, preflight, execute, *, same_key_retry=False):
        self.write_calls.append((preflight, execute, same_key_retry))
        return self.write_result


class _RecordingCLI:
    def __init__(self):
        self.calls = []

    async def call(self, command, params=None, **kw):
        self.calls.append((command, params, kw))
        return {}


async def command_of(write):
    """(command, params) the write's dry-run preflight would issue."""
    cli = _RecordingCLI()
    await write[0](cli)
    return cli.calls[0][0], cli.calls[0][1]


async def run_execute(write, responder):
    """Run the write's real execute() against a CLI whose replies come from responder(command, params).
    Returns (result, [(command, params), ...])."""
    cli = _RecordingCLI()

    async def call(command, params=None, **kw):
        cli.calls.append((command, params, kw))
        return responder(command, params)

    cli.call = call
    result = await write[1](cli, "key")
    return result, [(c, p) for c, p, _ in cli.calls]
