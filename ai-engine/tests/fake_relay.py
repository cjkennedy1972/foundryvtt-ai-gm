#!/usr/bin/env python3
"""An in-process relay that speaks the protocol the real one speaks.

Every constant here was captured from a live relay (go-relay on the amd64
docker host, Foundry v13 + the REST API module) rather than guessed:

    auth ok          -> {"type": "connected", "clientId": ..., "eventChannels": [...],
                         "supportedTypes": [...94 of them...]}
    rpc ok           -> {"type": "<message>-result", "requestId": ..., "clientId": ..., ...}
    rpc unknown type -> {"type": "error", "requestId": ..., "error": "Unknown message type: \\"x\\""}
    auth rejected    -> close 4002, reason one of
                        "Invalid auth message" | "Invalid API key"
                        | "Invalid clientId" | "No connected Foundry client found"

test_fake_relay_matches_the_real_one.py checks those against a live relay
when one is configured, so this cannot drift quietly — which is the failure
mode that made three of the mocks in this suite wrong.
"""

import asyncio
import json

import websockets

# ── captured from the live relay ──────────────────────────────────────────
ACK_TYPE = "connected"
EVENT_CHANNELS = [
    "chat-events", "roll-events", "hooks",
    "combat-events", "actor-events", "scene-events",
]
CLOSE_CODE = 4002
REASON_BAD_MESSAGE = "Invalid auth message"
REASON_BAD_KEY = "Invalid API key"
REASON_BAD_CLIENT = "Invalid clientId"
REASON_NO_FOUNDRY = "No connected Foundry client found"
# A representative slice; the live relay advertises 94.
SUPPORTED_TYPES = [
    "search", "structure", "chat-send", "roll", "execute-js",
    "add-effect", "remove-effect", "increase", "decrease",
    "start-encounter", "end-encounter", "playlist-play", "playlist-volume",
]


class FakeRelay:
    """`async with FakeRelay(...) as relay:` → relay.ws_url."""

    def __init__(
        self,
        *,
        token: str = "valid-token",
        client_id: str | None = "test-client",
        foundry_connected: bool = True,
        responses: dict | None = None,
        ack_delay: float = 0.0,
        drop_after: int | None = None,
    ):
        self.token = token
        self.client_id = client_id
        self.foundry_connected = foundry_connected
        self.responses = responses or {}
        self.ack_delay = ack_delay
        self.drop_after = drop_after
        self.received: list[dict] = []
        self.auth_frames: list[dict] = []
        self.connections = 0
        self._server = None
        self._sockets: list = []

    async def __aenter__(self):
        self._server = await websockets.serve(self._handle, "127.0.0.1", 0)
        port = self._server.sockets[0].getsockname()[1]
        self.ws_url = f"ws://127.0.0.1:{port}/ws/api"
        return self

    async def __aexit__(self, *exc):
        self._server.close()
        await self._server.wait_closed()

    async def push(self, message: dict):
        """Send an unsolicited event to every live socket, as the relay does."""
        for ws in list(self._sockets):
            try:
                await ws.send(json.dumps(message))
            except Exception:
                pass

    async def _handle(self, ws, path=None):
        self.connections += 1
        try:
            raw = await ws.recv()
        except Exception:
            return
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            await ws.close(CLOSE_CODE, REASON_BAD_MESSAGE)
            return

        if isinstance(msg, dict):
            self.auth_frames.append(msg)
        if not isinstance(msg, dict) or msg.get("type") != "auth" or not msg.get("token"):
            await ws.close(CLOSE_CODE, REASON_BAD_MESSAGE)
            return
        if msg.get("token") != self.token:
            await ws.close(CLOSE_CODE, REASON_BAD_KEY)
            return
        if self.client_id is not None and msg.get("clientId") not in (None, self.client_id):
            await ws.close(CLOSE_CODE, REASON_BAD_CLIENT)
            return
        if not self.foundry_connected:
            await ws.close(CLOSE_CODE, REASON_NO_FOUNDRY)
            return

        if self.ack_delay:
            await asyncio.sleep(self.ack_delay)
        await ws.send(json.dumps({
            "type": ACK_TYPE,
            "clientId": msg.get("clientId") or self.client_id,
            "eventChannels": EVENT_CHANNELS,
            "supportedTypes": SUPPORTED_TYPES,
        }))

        self._sockets.append(ws)
        try:
            handled = 0
            async for raw in ws:
                try:
                    req = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                self.received.append(req)
                handled += 1
                if self.drop_after is not None and handled > self.drop_after:
                    await ws.close(1001, "going away")
                    return
                await ws.send(json.dumps(self._reply(req)))
        except websockets.ConnectionClosed:
            pass
        finally:
            if ws in self._sockets:
                self._sockets.remove(ws)

    def _reply(self, req: dict) -> dict:
        mtype = req.get("type", "")
        rid = req.get("requestId")
        if mtype not in SUPPORTED_TYPES and mtype not in self.responses:
            return {"type": "error", "requestId": rid,
                    "error": f'Unknown message type: "{mtype}"'}
        body = dict(self.responses.get(mtype, {}))
        body.setdefault("type", f"{mtype}-result")
        body["requestId"] = rid
        body.setdefault("clientId", self.client_id)
        return body
