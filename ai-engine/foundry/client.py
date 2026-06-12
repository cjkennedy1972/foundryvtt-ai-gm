"""FoundryVTT Client — WebSocket communication with the Go relay server."""

import asyncio
import json
import logging
import re
import time
import uuid
from typing import Any, Callable, Dict, List, Optional
import websockets

from config import settings

logger = logging.getLogger(__name__)


class FoundryClient:
    """WebSocket client for the FoundryVTT relay server.

    Architecture:
    - Exactly ONE reader coroutine (`_reader_loop`) consumes from the socket.
    - RPC responses are matched via request_id → Future dict.
    - Push messages are dispatched to channel handlers.
    - This eliminates the race where `_send()` and `start_listening()`
      both called `self._ws.recv()` on the same socket.
    """

    def __init__(self):
        self.ws_url = settings.relay_ws_url
        self.api_key = settings.relay_api_key
        self._ai_name = None
        self._ws: Optional[websockets.WebSocketClientConnection] = None
        self._connected = False
        self._handlers: Dict[str, List[Callable]] = {}
        self._subscribed_channels: set = set()
        self._message_id = 0
        self._ai_tone = settings.ai_tone
        self._npc_context = ""
        self._world_context = ""
        # RPC response tracking — eliminates read collision
        self._rpc_futures: Dict[str, asyncio.Future] = {}
        # Single reader task
        self._reader_task: Optional[asyncio.Task] = None

    def _next_request_id(self) -> str:
        self._message_id += 1
        return f"gm-{self._message_id}-{uuid.uuid4().hex[:6]}"

    def set_ai_name(self, name: str):
        """Override the GM speaker name for all outgoing messages."""
        self._ai_name = name
        logger.info(f"GM speaker name updated to: {name}")

    def _get_speaker_name(self) -> str:
        """Get the current speaker name for outgoing messages."""
        return self._ai_name if self._ai_name else settings.ai_name

    async def connect(self):
        """Connect to the relay server via WebSocket."""
        try:
            self._ws = await websockets.connect(
                self.ws_url,
                additional_headers={"X-Api-Key": self.api_key}
            )
            self._connected = True
            logger.info("Connected to FoundryVTT relay")
            # Start the single reader task
            self._reader_task = asyncio.create_task(self._reader_loop())
            return True
        except Exception as e:
            logger.error(f"Failed to connect to relay: {e}")
            self._connected = False
            return False

    async def disconnect(self):
        """Disconnect from the relay server."""
        # Cancel reader task
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
            self._reader_task = None

        # Resolve any pending RPC futures with an error
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
        """Single reader coroutine — routes messages to the right consumer.

        This is the ONLY code path that calls self._ws.recv().
        """
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
        """Parse a raw WebSocket message and route it to the correct handler."""
        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            logger.warning(f"Failed to parse message: {message[:100]}")
            return

        # --- Is this an RPC response? ---
        msg_id = data.get("id")
        if msg_id and msg_id in self._rpc_futures:
            future = self._rpc_futures.pop(msg_id)
            if not future.done():
                future.set_result(data)
            return

        # --- Otherwise it's a push notification — route to channel handlers ---
        msg_type = data.get("type", data.get("method", ""))
        channel = data.get("channel", "")

        if channel in self._handlers:
            for handler in self._handlers[channel]:
                try:
                    await handler(data)
                except Exception as e:
                    logger.error(f"Handler error on channel {channel}: {e}")

    async def _send(self, data: dict) -> dict:
        """Send an RPC request and wait for its matching response.

        Creates a Future, stores it by request_id, and returns when
        the reader task resolves it. Never calls recv() itself.
        """
        if not self._ws or not self._connected:
            raise ConnectionError("Not connected to relay")
        request_id = self._next_request_id()
        payload = {"jsonrpc": "2.0", "id": request_id, **data}

        # Register the future BEFORE sending so the reader loop can
        # resolve it even if the response arrives immediately.
        loop = asyncio.get_event_loop()
        future = loop.create_future()
        self._rpc_futures[request_id] = future
        await self._ws.send(json.dumps(payload))

        try:
            return await asyncio.wait_for(future, timeout=30)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            self._rpc_futures.pop(request_id, None)
            raise ConnectionError(f"RPC request {request_id} timed out")

    async def _send_notify(self, data: dict):
        """Send a notification (no response expected)."""
        if not self._ws or not self._connected:
            raise ConnectionError("Not connected to relay")
        request_id = self._next_request_id()
        payload = {"jsonrpc": "2.0", "id": request_id, "notification": True, **data}
        await self._ws.send(json.dumps(payload))

    async def subscribe_to_channel(self, channel: str):
        """Subscribe to a message channel on the relay server."""
        if channel in self._subscribed_channels:
            return
        try:
            result = await self._send({
                "method": "subscribe",
                "params": {"channel": channel}
            })
            self._subscribed_channels.add(channel)
            logger.info(f"Subscribed to channel: {channel}")
            return result
        except Exception as e:
            logger.error(f"Failed to subscribe to {channel}: {e}")
            return None

    def subscribe(self, channel: str, handler: Callable):
        """Register a handler for messages from a specific channel."""
        if channel not in self._handlers:
            self._handlers[channel] = []
        self._handlers[channel].append(handler)

    # --- FoundryVTT API Methods ---

    async def _rpc_call(self, method: str, params: dict = None) -> dict:
        """Make an RPC call to FoundryVTT via the relay."""
        result = await self._send({
            "method": method,
            "params": params or {}
        })
        return result

    async def chat_message(self, text: str, speaker: str = "", whisper: List[str] = None) -> dict:
        """Send a chat message to FoundryVTT."""
        return await self._send({
            "method": "chat-send",
            "params": {
                "content": text,
                "speaker": speaker,
                "whisper": whisper or []
            }
        })

    async def roll(self, formula: str, speaker: str = "", flavor: str = None) -> dict:
        """Roll dice in FoundryVTT."""
        return await self._send({
            "method": "roll",
            "params": {
                "formula": formula,
                "speaker": speaker,
                "flavor": flavor
            }
        })

    async def get_structure(self) -> dict:
        """Get the FoundryVTT world structure."""
        return await self._send({
            "method": "structure",
            "params": {}
        })

    async def search(self, query: str) -> dict:
        """Search the FoundryVTT world for entities."""
        return await self._send({
            "method": "search",
            "params": {"query": query}
        })

    async def get_actors(self, world_only: bool = False) -> list:
        """Get actor information from FoundryVTT."""
        try:
            result = await self._send({
                "method": "search",
                "params": {"query": "actor"}
            })
            # Filter for actual character/npc actors
            actors = []
            raw_data = result.get("data", result.get("results", []))
            if isinstance(raw_data, dict):
                raw_data = raw_data.get("actors", raw_data.get("entries", []))
            if isinstance(raw_data, list):
                for entry in raw_data:
                    if entry.get("type") in ("Actor", "actor") or "token" in str(entry).lower():
                        actor_info = {
                            "name": entry.get("name", "Unknown"),
                            "hp": entry.get("hp", entry.get("data", {}).get("attributes", {}).get("hp", {}).get("value", "?")),
                            "max_hp": entry.get("max_hp", entry.get("data", {}).get("attributes", {}).get("hp", {}).get("max", "?")),
                            "uuid": entry.get("uuid", entry.get("id", "")),
                            "type": entry.get("type", "unknown")
                        }
                        actors.append(actor_info)
            return actors
        except Exception as e:
            logger.error(f"Failed to get actors: {e}")
            return []

    async def get_scenes(self) -> list:
        """Get list of scenes in the world."""
        try:
            result = await self._send({
                "method": "search",
                "params": {"query": "scene"}
            })
            raw_data = result.get("data", result.get("results", []))
            scenes = []
            if isinstance(raw_data, dict):
                raw_data = raw_data.get("scenes", raw_data.get("entries", []))
            if isinstance(raw_data, list):
                for entry in raw_data:
                    scene_info = {
                        "name": entry.get("name", entry.get("title", "Unknown")),
                        "uuid": entry.get("uuid", entry.get("id", "")),
                        "token_count": entry.get("tokenCount", entry.get("tokens", {})),
                        "active": entry.get("active", False)
                    }
                    scenes.append(scene_info)
            return scenes
        except Exception as e:
            logger.error(f"Failed to get scenes: {e}")
            return []

    async def get_scene_details(self, scene_name: str = None) -> dict:
        """Get detailed information about a scene including tokens and tiles."""
        try:
            if scene_name:
                result = await self._send({
                    "method": "get-scene",
                    "params": {"name": scene_name}
                })
            else:
                result = await self._send({
                    "method": "get-scene",
                    "params": {}
                })
            return result
        except Exception as e:
            logger.error(f"Failed to get scene details: {e}")
            return {}

    async def get_scene_tokens(self, scene_name: str = None) -> list:
        """Get tokens in a scene with their positions."""
        try:
            details = await self.get_scene_details(scene_name)
            tokens = details.get("tokens", details.get("data", {}).get("tokens", []))
            result = []
            for t in tokens:
                token_info = {
                    "name": t.get("name", t.get("tname", "Unknown")),
                    "x": t.get("x", t.get("position", {}).get("x", 0)),
                    "y": t.get("y", t.get("position", {}).get("y", 0)),
                    "width": t.get("width", t.get("scale", {}).get("x", 1)),
                    "height": t.get("height", t.get("scale", {}).get("y", 1)),
                    "actorUuid": t.get("actorUuid", t.get("actor_id", "")),
                    "id": t.get("id", t.get("_id", "")),
                    "emitter": t.get("emitter", 0),
                    "brightness": t.get("brightness", 1),
                    "disposition": t.get("disposition", 1)
                }
                result.append(token_info)
            return result
        except Exception as e:
            logger.error(f"Failed to get scene tokens: {e}")
            return []

    async def set_active_scene(self, scene_name: str) -> dict:
        """Change the active scene/map in FoundryVTT."""
        return await self._send({
            "method": "switch-scene",
            "params": {"scene": scene_name}
        })

    async def update_entity(self, uuid: str = None, data: dict = None, token_id: str = None) -> dict:
        """Update an entity's properties in FoundryVTT."""
        params = {}
        if uuid:
            params["uuid"] = uuid
        if data:
            params["data"] = data
        if token_id:
            params["token_id"] = token_id
        return await self._send({
            "method": "update-entity",
            "params": params
        })

    async def decrease_attribute(self, attribute_path: str, amount: int, actor_uuid: str) -> dict:
        """Decrease an attribute value (e.g., HP) on an actor."""
        return await self._send({
            "method": "decrease-attribute",
            "params": {
                "attribute": attribute_path,
                "amount": amount,
                "actorUuid": actor_uuid
            }
        })

    async def increase_attribute(self, attribute_path: str, amount: int, actor_uuid: str) -> dict:
        """Increase an attribute value (e.g., HP healing) on an actor."""
        return await self._send({
            "method": "increase-attribute",
            "params": {
                "attribute": attribute_path,
                "amount": amount,
                "actorUuid": actor_uuid
            }
        })

    async def play_sound(self, sound_name: str) -> dict:
        """Play a sound effect in FoundryVTT."""
        return await self._send({
            "method": "play-sound",
            "params": {"name": sound_name}
        })

    async def start_encounter(self, tokens: list = None) -> dict:
        """Start a combat encounter."""
        return await self._send({
            "method": "start-encounter",
            "params": {"tokens": tokens or []}
        })

    async def end_encounter(self) -> dict:
        """End the current combat encounter."""
        return await self._send({
            "method": "end-encounter",
            "params": {}
        })

    async def get_users(self) -> list:
        """Get users currently in the FoundryVTT session."""
        try:
            result = await self._send({
                "method": "get-users",
                "params": {}
            })
            users = result.get("users", result.get("data", {}).get("users", []))
            return users if isinstance(users, list) else []
        except Exception as e:
            logger.error(f"Failed to get users: {e}")
            return []

    async def get_rooms(self) -> list:
        """Get rooms/sessions in FoundryVTT."""
        try:
            result = await self._send({
                "method": "get-rooms",
                "params": {}
            })
            rooms = result.get("rooms", result.get("data", {}).get("rooms", []))
            return rooms if isinstance(rooms, list) else []
        except Exception as e:
            logger.error(f"Failed to get rooms: {e}")
            return []

    async def set_npc_context(self, context: str):
        """Set NPC context for use in responses."""
        self._npc_context = context
        logger.info(f"NPC context updated ({len(context)} chars)")

    async def set_world_context(self, context: str):
        """Set world context for use in responses."""
        self._world_context = context
        logger.info(f"World context updated ({len(context)} chars)")

    async def set_ai_tone(self, tone: str):
        """Set the AI tone for responses."""
        self._ai_tone = tone
        logger.info(f"AI tone updated: {tone[:50]}...")

    def reset_message_id(self):
        """Reset the message ID counter."""
        self._message_id = 0
