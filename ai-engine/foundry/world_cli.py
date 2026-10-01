"""Client for the fvtt-world-cli daemon: typed, validated commands run inside an open GM session.

A second command surface beside the relay (foundry/client.py). The daemon listens on a
loopback WebSocket; this client authenticates with the device credential the CLI keeps in
its per-user config, then sends `command.request` frames and matches `command.response`
frames by id. It carries no event feed, so it cannot replace the relay, only complement it.

The protocol version is the product release version and must match the installed CLI,
daemon and Foundry module exactly; the daemon refuses a mismatch, and that refusal is
surfaced here as a WorldCLIError rather than negotiated around.
"""

import asyncio
import json
import logging
import os
import uuid
from typing import Any, Dict, Optional

import websockets

logger = logging.getLogger(__name__)


class WorldCLIError(Exception):
    """A command the daemon or bridge refused, or a daemon that could not be reached.

    `code` is the protocol's error code (APPROVAL_PENDING, COMMAND_DENIED, SCENE_NOT_FOUND,
    BRIDGE_NOT_READY...) or one of ours: DAEMON_UNAVAILABLE, NOT_CONFIGURED, TIMEOUT.
    """

    def __init__(self, code: str, message: str, details: Optional[dict] = None):
        super().__init__(f"{code}: {message}")
        self.code, self.message, self.details = code, message, details or {}


def _read_credential(config_path: str) -> str:
    try:
        with open(os.path.expanduser(config_path), encoding="utf-8") as f:
            credential = json.load(f).get("deviceCredential")
    except (OSError, ValueError) as e:
        raise WorldCLIError("NOT_CONFIGURED", f"Cannot read the World CLI config at {config_path}: {e}") from e
    if not credential:
        raise WorldCLIError("NOT_CONFIGURED", f"{config_path} has no deviceCredential; run `fvtt-world-cli bridge serve` once.")
    return credential


class WorldCLI:
    def __init__(self, url: str, protocol_version: str, config_path: str, timeout: float = 60.0):
        self.url, self.protocol_version, self.config_path, self.timeout = url, protocol_version, config_path, timeout
        self._ws = None
        self._reader: Optional[asyncio.Task] = None
        self._pending: Dict[str, asyncio.Future] = {}
        self._connect_lock = asyncio.Lock()

    async def _ensure_connected(self) -> None:
        async with self._connect_lock:
            if self._ws is not None and self._reader is not None and not self._reader.done():
                return
            credential = _read_credential(self.config_path)
            try:
                ws = await websockets.connect(self.url, max_size=None, open_timeout=10)
                await ws.send(json.dumps({
                    "protocolVersion": self.protocol_version, "type": "client.hello",
                    "credential": credential, "client": "cli",
                }))
                ack = json.loads(await asyncio.wait_for(ws.recv(), 10))
            except (OSError, websockets.WebSocketException, asyncio.TimeoutError, ValueError) as e:
                raise WorldCLIError("DAEMON_UNAVAILABLE", f"Cannot reach the World CLI daemon at {self.url}: {e}") from e
            if not (ack.get("type") == "client.hello.ack" and ack.get("ok")):
                await ws.close()
                err = ack.get("error") or {}
                raise WorldCLIError(err.get("code", "HELLO_REJECTED"), err.get("message", f"Daemon rejected the hello: {ack}"), err.get("details"))
            self._ws = ws
            self._reader = asyncio.create_task(self._read(ws))

    async def _read(self, ws) -> None:
        try:
            async for raw in ws:
                frame = json.loads(raw)
                future = self._pending.pop(frame.get("id"), None)
                if future and not future.done():
                    future.set_result(frame)
        except (websockets.WebSocketException, ValueError):
            pass
        finally:
            # Whatever ended the read loop, no caller may wait forever on a dead socket.
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(WorldCLIError("DAEMON_UNAVAILABLE", "The World CLI daemon closed the connection."))
            self._pending.clear()

    async def call(
        self, command: str, params: Optional[Dict[str, Any]] = None, *,
        dry_run: bool = False, idempotency_key: Optional[str] = None, timeout: Optional[float] = None,
    ) -> Any:
        """Run one command and return its `result`; raise WorldCLIError on any refusal."""
        await self._ensure_connected()
        body = dict(params or {})
        if dry_run:
            body["dryRun"] = True
        if idempotency_key:
            body["idempotencyKey"] = idempotency_key
        request_id = str(uuid.uuid4())
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            await self._ws.send(json.dumps({
                "protocolVersion": self.protocol_version, "type": "command.request",
                "id": request_id, "command": command, "params": body,
            }))
            frame = await asyncio.wait_for(future, timeout or self.timeout)
        except asyncio.TimeoutError as e:
            raise WorldCLIError("TIMEOUT", f"{command} got no answer in {timeout or self.timeout:g}s") from e
        except websockets.WebSocketException as e:
            raise WorldCLIError("DAEMON_UNAVAILABLE", f"Lost the World CLI daemon while sending {command}: {e}") from e
        finally:
            self._pending.pop(request_id, None)
        if not frame.get("ok"):
            err = frame.get("error") or {}
            raise WorldCLIError(err.get("code", "UNKNOWN"), err.get("message", "The command failed."), err.get("details"))
        return frame.get("result")

    async def close(self) -> None:
        if self._reader:
            self._reader.cancel()
        if self._ws is not None:
            await self._ws.close()
        self._ws = self._reader = None
