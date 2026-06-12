"""FoundryVTT Client — WebSocket communication with the Go relay server."""

import asyncio
import json
import logging
import uuid
from typing import Callable, Dict, List, Optional
import websockets

from config import settings

logger = logging.getLogger(__name__)

# Maps relay event push type → the channel name callers register handlers on.
_EVENT_TYPE_TO_CHANNEL = {
    "chat-event":   "chat-events",
    "roll-event":   "roll-events",
    "combat-event": "combat-events",
    "scene-event":  "scene-events",
    "actor-event":  "actor-events",
    "hook":         "hooks",
}


class FoundryClient:
    """WebSocket client for the FoundryVTT relay server.

    Protocol (relay expects):
    1. After WS handshake, send {"type":"auth","token":"<key>"} as the first
       message.  Relay replies {"type":"connected",...} or closes 4002.
    2. All requests use flat JSON: {"type":"<method>","requestId":"<id>",...params}.
       Params are top-level fields — NOT nested under a "params" key.
    3. Replies carry {"type":"<method>-result","requestId":"<id>","data":{...}}.
       Errors carry {"type":"error","requestId":"<id>","error":"..."}.
    4. Subscribe via {"type":"subscribe","requestId":"<id>","channel":"<name>"}.
       Relay acks with {"type":"subscribed","channel":"<name>","requestId":"<id>"}.
    5. Event pushes have no requestId; they are routed by type to channel handlers.
    """

    def __init__(self):
        self.ws_url = settings.relay_ws_url
        self.api_key = settings.relay_api_key
        self._ai_name = None
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._connected = False
        self._handlers: Dict[str, List[Callable]] = {}
        self._subscribed_channels: set = set()
        self._message_id = 0
        self._ai_tone = settings.ai_tone
        self._npc_context = ""
        self._world_context = ""
        self._rpc_futures: Dict[str, asyncio.Future] = {}
        self._reader_task: Optional[asyncio.Task] = None

    def _next_request_id(self) -> str:
        self._message_id += 1
        return f"gm-{self._message_id}-{uuid.uuid4().hex[:6]}"

    def set_ai_name(self, name: str):
        self._ai_name = name
        logger.info(f"GM speaker name updated to: {name}")

    def _get_speaker_name(self) -> str:
        return self._ai_name if self._ai_name else settings.ai_name

    async def connect(self):
        """Connect to the relay and complete the auth handshake."""
        try:
            self._ws = await websockets.connect(self.ws_url)
            # Relay requires the first message to be an auth token.
            await self._ws.send(json.dumps({"type": "auth", "token": self.api_key}))
            # Wait for the connected ack (relay closes 4002 on bad auth).
            ack_raw = await asyncio.wait_for(self._ws.recv(), timeout=10)
            ack = json.loads(ack_raw)
            if ack.get("type") != "connected":
                logger.error(f"Unexpected auth response: {ack}")
                await self._ws.close()
                return False
            self._connected = True
            logger.info("Connected to FoundryVTT relay")
            self._reader_task = asyncio.create_task(self._reader_loop())
            return True
        except Exception as e:
            logger.error(f"Failed to connect to relay: {e}")
            self._connected = False
            return False

    async def disconnect(self):
        """Disconnect from the relay server."""
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
            self._reader_task = None

        for future in self._rpc_futures.values():
            if not future.done():
                future.set_exception(ConnectionError("Disconnected"))
        self._rpc_futures.clear()

        if self._ws:
            await self._ws.close()
            self._connected = False
            logger.info("Disconnected from relay")

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def _reader_loop(self):
        """Single reader — routes replies to RPC futures, events to handlers."""
        logger.info("WebSocket reader started")
        try:
            while True:
                message = await self._ws.recv()
                await self._dispatch_message(message)
        except websockets.exceptions.ConnectionClosed:
            logger.warning("Relay connection closed")
            self._connected = False
        except asyncio.CancelledError:
            logger.info("Reader task cancelled")
            raise
        except Exception as e:
            logger.error(f"Reader loop error: {e}")
            self._connected = False

    async def _dispatch_message(self, message: str):
        """Parse a relay message and route it."""
        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            logger.warning(f"Failed to parse message: {message[:100]}")
            return

        # RPC reply: has requestId and that id is in our futures map.
        request_id = data.get("requestId")
        if request_id and request_id in self._rpc_futures:
            future = self._rpc_futures.pop(request_id)
            if not future.done():
                future.set_result(data)
            return

        # Event push: map relay event type → registered channel handlers.
        msg_type = data.get("type", "")
        channel = _EVENT_TYPE_TO_CHANNEL.get(msg_type)
        if channel and channel in self._handlers:
            for handler in self._handlers[channel]:
                try:
                    await handler(data)
                except Exception as e:
                    logger.error(f"Handler error on channel {channel}: {e}")

    async def _send(self, msg_type: str, **params) -> dict:
        """Send a request and await its reply.

        Builds {"type": msg_type, "requestId": ..., **params} — flat, no
        "params" nesting — which is what the relay expects.
        """
        if not self._ws or not self._connected:
            raise ConnectionError("Not connected to relay")
        request_id = self._next_request_id()
        payload = {"type": msg_type, "requestId": request_id, **params}

        loop = asyncio.get_event_loop()
        future = loop.create_future()
        self._rpc_futures[request_id] = future
        await self._ws.send(json.dumps(payload))

        try:
            return await asyncio.wait_for(future, timeout=30)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            self._rpc_futures.pop(request_id, None)
            raise ConnectionError(f"RPC request {request_id} timed out")

    async def subscribe_to_channel(self, channel: str):
        """Subscribe to an event channel on the relay."""
        if channel in self._subscribed_channels:
            return
        try:
            await self._send("subscribe", channel=channel)
            self._subscribed_channels.add(channel)
            logger.info(f"Subscribed to channel: {channel}")
        except Exception as e:
            logger.error(f"Failed to subscribe to {channel}: {e}")

    def subscribe(self, channel: str, handler: Callable):
        """Register a push-event handler for a channel."""
        if channel not in self._handlers:
            self._handlers[channel] = []
        self._handlers[channel].append(handler)

    # --- FoundryVTT API methods ---

    async def chat_message(self, text: str, speaker: str = "", whisper: List[str] = None) -> dict:
        return await self._send("chat-send", content=text, speaker=speaker, whisper=whisper or [])

    async def roll(self, formula: str, speaker: str = "", flavor: str = None) -> dict:
        return await self._send("roll", formula=formula, speaker=speaker, flavor=flavor)

    async def get_structure(self) -> dict:
        return await self._send("structure")

    async def search(self, query: str) -> dict:
        return await self._send("search", query=query)

    async def get_actors(self, world_only: bool = False) -> list:
        try:
            result = await self._send("search", query="actor")
            actors = []
            raw_data = result.get("data", result.get("results", []))
            if isinstance(raw_data, dict):
                raw_data = raw_data.get("actors", raw_data.get("entries", []))
            if isinstance(raw_data, list):
                for entry in raw_data:
                    if entry.get("type") in ("Actor", "actor") or "token" in str(entry).lower():
                        actors.append({
                            "name": entry.get("name", "Unknown"),
                            "hp": entry.get("hp", entry.get("data", {}).get("attributes", {}).get("hp", {}).get("value", "?")),
                            "max_hp": entry.get("max_hp", entry.get("data", {}).get("attributes", {}).get("hp", {}).get("max", "?")),
                            "uuid": entry.get("uuid", entry.get("id", "")),
                            "type": entry.get("type", "unknown"),
                        })
            return actors
        except Exception as e:
            logger.error(f"Failed to get actors: {e}")
            return []

    async def get_scenes(self) -> list:
        try:
            result = await self._send("search", query="scene")
            raw_data = result.get("data", result.get("results", []))
            scenes = []
            if isinstance(raw_data, dict):
                raw_data = raw_data.get("scenes", raw_data.get("entries", []))
            if isinstance(raw_data, list):
                for entry in raw_data:
                    scenes.append({
                        "name": entry.get("name", entry.get("title", "Unknown")),
                        "uuid": entry.get("uuid", entry.get("id", "")),
                        "token_count": entry.get("tokenCount", entry.get("tokens", {})),
                        "active": entry.get("active", False),
                    })
            return scenes
        except Exception as e:
            logger.error(f"Failed to get scenes: {e}")
            return []

    async def get_scene_details(self, scene_name: str = None) -> dict:
        try:
            if scene_name:
                return await self._send("get-scene", name=scene_name)
            return await self._send("get-scene")
        except Exception as e:
            logger.error(f"Failed to get scene details: {e}")
            return {}

    async def get_scene_tokens(self, scene_name: str = None) -> list:
        try:
            details = await self.get_scene_details(scene_name)
            tokens = details.get("tokens", details.get("data", {}).get("tokens", []))
            return [
                {
                    "name": t.get("name", t.get("tname", "Unknown")),
                    "x": t.get("x", t.get("position", {}).get("x", 0)),
                    "y": t.get("y", t.get("position", {}).get("y", 0)),
                    "width": t.get("width", t.get("scale", {}).get("x", 1)),
                    "height": t.get("height", t.get("scale", {}).get("y", 1)),
                    "actorUuid": t.get("actorUuid", t.get("actor_id", "")),
                    "id": t.get("id", t.get("_id", "")),
                    "emitter": t.get("emitter", 0),
                    "brightness": t.get("brightness", 1),
                    "disposition": t.get("disposition", 1),
                }
                for t in tokens
            ]
        except Exception as e:
            logger.error(f"Failed to get scene tokens: {e}")
            return []

    async def set_active_scene(self, scene_name: str) -> dict:
        return await self._send("switch-scene", scene=scene_name)

    async def update_entity(self, uuid: str = None, data: dict = None, token_id: str = None) -> dict:
        kwargs = {}
        if uuid:
            kwargs["uuid"] = uuid
        if data:
            kwargs["data"] = data
        if token_id:
            kwargs["token_id"] = token_id
        return await self._send("update", **kwargs)

    async def decrease_attribute(self, attribute_path: str, amount: int, actor_uuid: str) -> dict:
        return await self._send("decrease", attribute=attribute_path, amount=amount, actorUuid=actor_uuid)

    async def increase_attribute(self, attribute_path: str, amount: int, actor_uuid: str) -> dict:
        return await self._send("increase", attribute=attribute_path, amount=amount, actorUuid=actor_uuid)

    async def play_sound(self, sound_name: str) -> dict:
        return await self._send("play-sound", name=sound_name)

    async def start_encounter(self, tokens: list = None) -> dict:
        return await self._send("start-encounter", tokens=tokens or [])

    async def end_encounter(self) -> dict:
        return await self._send("end-encounter")

    async def get_users(self) -> list:
        try:
            result = await self._send("get-users")
            users = result.get("users", result.get("data", {}).get("users", []))
            return users if isinstance(users, list) else []
        except Exception as e:
            logger.error(f"Failed to get users: {e}")
            return []

    async def get_rooms(self) -> list:
        """Get world structure (maps to relay 'structure' request)."""
        try:
            result = await self._send("structure")
            rooms = result.get("rooms", result.get("data", {}).get("rooms", []))
            return rooms if isinstance(rooms, list) else []
        except Exception as e:
            logger.error(f"Failed to get rooms: {e}")
            return []

    async def set_npc_context(self, context: str):
        self._npc_context = context
        logger.info(f"NPC context updated ({len(context)} chars)")

    async def set_world_context(self, context: str):
        self._world_context = context
        logger.info(f"World context updated ({len(context)} chars)")

    async def set_ai_tone(self, tone: str):
        self._ai_tone = tone
        logger.info(f"AI tone updated: {tone[:50]}...")

    def reset_message_id(self):
        self._message_id = 0
