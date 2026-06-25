"""FoundryVTT Client — WebSocket communication with the Go relay server."""

import asyncio
import base64
import json
import logging
import uuid
from typing import Any, Callable, Dict, List, Optional
import httpx
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
        self._reconnecting: bool = False
        # Optional async callback to relaunch the headless Foundry session when
        # the relay reports no connected Foundry client. Wired in main.py.
        self._relaunch_headless: Optional[Callable] = None
        self._last_connect_error: str = ""

    def _next_request_id(self) -> str:
        self._message_id += 1
        return f"gm-{self._message_id}-{uuid.uuid4().hex[:6]}"

    def set_ai_name(self, name: str):
        self._ai_name = name
        logger.info(f"GM speaker name updated to: {name}")

    def _get_speaker_name(self) -> str:
        return self._ai_name if self._ai_name else settings.ai_name

    async def connect(self, max_retries: int = 5):
        """Connect to the relay and complete the auth handshake.

        Retries with exponential backoff on failure.  Callers that need
        to keep the connection alive should loop until this returns True.
        """
        # Clean up any previous connection state before attempting new connection
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except (asyncio.CancelledError, Exception):
                pass
            self._reader_task = None

        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

        # Fail all pending futures from the old connection
        for future in self._rpc_futures.values():
            if not future.done():
                future.set_exception(ConnectionError("Connection reset"))
        self._rpc_futures.clear()

        base_delay = 2  # seconds
        for attempt in range(max_retries):
            try:
                self._ws = await websockets.connect(self.ws_url)
                # Relay requires the first message to be an auth token.
                # Include clientId when connecting via a headless Chrome session
                # so the relay routes to the right Foundry world.
                auth_msg: dict = {"type": "auth", "token": self.api_key}
                if settings.relay_headless_client_id:
                    auth_msg["clientId"] = settings.relay_headless_client_id
                await self._ws.send(json.dumps(auth_msg))
                # Wait for the connected ack (relay closes 4002 on bad auth).
                ack_raw = await asyncio.wait_for(self._ws.recv(), timeout=10)
                ack = json.loads(ack_raw)
                if ack.get("type") != "connected":
                    logger.error(f"Unexpected auth response: {ack}")
                    await self._ws.close()
                    self._connected = False
                    continue
                self._connected = True
                logger.info(f"Connected to FoundryVTT relay (attempt {attempt + 1})")
                self._reader_task = asyncio.create_task(self._reader_loop())
                # Re-subscribe to any channels registered before this connection
                if self._subscribed_channels:
                    for ch in list(self._subscribed_channels):
                        try:
                            await self._send("subscribe", channel=ch)
                            logger.info(f"Re-subscribed to channel: {ch}")
                        except Exception as sub_e:
                            logger.error(f"Failed to re-subscribe to {ch}: {sub_e}")
                return True
            except Exception as e:
                self._connected = False
                self._last_connect_error = str(e)
                # Only sleep if not the last attempt
                if attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)
                    logger.warning(
                        f"Failed to connect to relay (attempt {attempt + 1}/{max_retries}): {e}. "
                        f"Retrying in {delay}s…"
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.warning(
                        f"Failed to connect to relay (attempt {attempt + 1}/{max_retries}): {e}"
                    )

        logger.error(f"Failed to connect to relay after {max_retries} attempts")
        return False

    async def ensure_connected(self):
        """Non-blocking reconnection check.

        If the connection is down (not connected or reader task finished),
        attempts a reconnection in the background.
        """
        if self._reconnecting:
            return  # Already attempting to reconnect
        if self._connected and self._reader_task and not self._reader_task.done():
            return  # Already healthy
        if self._connected and self._reader_task and self._reader_task.done():
            # Reader crashed — cancel it to avoid resource warnings
            try:
                self._reader_task.cancel()
                await self._reader_task
            except (asyncio.CancelledError, Exception):
                pass
            self._reader_task = None
            self._connected = False
        # Attempt reconnection in the background
        asyncio.create_task(self._reconnect())

    async def _reconnect(self):
        """Background reconnection with exponential backoff."""
        if self._reconnecting:
            return
        self._reconnecting = True
        try:
            logger.info("Reconnection attempt started…")
            success = await self.connect(max_retries=3)
            # Self-heal: if the relay has no Foundry client (the headless
            # browser's module dropped or its tab died), relaunch the headless
            # session and try once more. A plain reconnect can never recover
            # this on its own.
            if (
                not success
                and self._relaunch_headless
                and "No connected Foundry client" in self._last_connect_error
            ):
                logger.warning(
                    "Relay has no Foundry client — relaunching headless session…"
                )
                try:
                    await self._relaunch_headless()
                    success = await self.connect(max_retries=3)
                except Exception as e:
                    logger.error(f"Headless relaunch failed: {e}")
            if success:
                logger.info("Reconnected to FoundryVTT relay")
            else:
                logger.warning("Reconnection attempts failed — will retry later")
        finally:
            self._reconnecting = False

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
        finally:
            # Fail all pending RPC futures so callers don't hang
            for future in self._rpc_futures.values():
                if not future.done():
                    future.set_exception(ConnectionError("Reader loop exited"))

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

    async def _send(self, msg_type: str, *, _timeout: Optional[float] = None, **params) -> dict:
        """Send a request and await its reply.

        Builds {"type": msg_type, "requestId": ..., **params} — flat, no
        "params" nesting — which is what the relay expects.

        _timeout overrides the default reply timeout (settings.relay_rpc_timeout).
        """
        if not self._ws or not self._connected:
            raise ConnectionError("Not connected to relay")
        request_id = self._next_request_id()
        payload = {"type": msg_type, "requestId": request_id, **params}

        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self._rpc_futures[request_id] = future
        await self._ws.send(json.dumps(payload))

        timeout = _timeout if _timeout is not None else settings.relay_rpc_timeout
        try:
            result = await asyncio.wait_for(future, timeout=timeout)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            self._rpc_futures.pop(request_id, None)
            raise ConnectionError(f"RPC request {request_id} timed out")
        # Relay/Foundry returns {"type":"error","error":"..."} for failures;
        # raise so callers get a real exception rather than silently returning
        # an error dict that most callers ignore.
        if isinstance(result, dict) and result.get("type") == "error":
            raise RuntimeError(f"Foundry error [{msg_type}]: {result.get('error', result)}")
        return result

    async def _send_with_retry(self, msg_type: str, max_retries: int = 2, _timeout: Optional[float] = None, **params) -> dict:
        """Send a request with retry logic for transient failures.

        For search-heavy operations like get_actors(), retry on timeout since
        the relay may be temporarily overloaded or Foundry unresponsive.
        """
        for attempt in range(max_retries):
            try:
                return await self._send(msg_type, _timeout=_timeout, **params)
            except ConnectionError as e:
                if "timed out" not in str(e) or attempt == max_retries - 1:
                    raise
                # Exponential backoff before retry: 1s, 2s, etc.
                wait_time = 2 ** attempt
                logger.warning(f"RPC request {msg_type} timed out; retrying in {wait_time}s (attempt {attempt + 1}/{max_retries})")
                await asyncio.sleep(wait_time)

    async def subscribe_to_channel(self, channel: str):
        """Subscribe to an event channel on the relay."""
        if channel in self._subscribed_channels:
            return
        # Always register so reconnect knows to re-subscribe
        self._subscribed_channels.add(channel)
        try:
            await self._send("subscribe", channel=channel)
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
        return await self._send_with_retry("chat-send", max_retries=3, content=text, speaker=speaker, whisper=whisper or [])

    async def roll(self, formula: str, speaker: str = "", flavor: str = None) -> dict:
        return await self._send("roll", formula=formula, speaker=speaker, flavor=flavor)

    async def get_structure(self) -> dict:
        return await self._send("structure")

    async def get_scene_by_name(self, name: str) -> Optional[dict]:
        """Get a scene's full data (including levels) by name."""
        try:
            result = await self._send("get-scene", name=name)
            logger.info(f"get-scene result type: {type(result).__name__}, is_dict: {isinstance(result, dict)}")
            if isinstance(result, dict):
                logger.info(f"get-scene keys: {list(result.keys())}, has_data_key: {'data' in result}")
                # Response is wrapped in {type, data, requestId, clientId}
                if "data" in result:
                    logger.info(f"Extracting data from wrapper, returning scene with {len(result['data'].get('levels', [])) if isinstance(result['data'], dict) else '?'} levels")
                    return result["data"]
            if isinstance(result, dict):
                logger.info(f"Returning result as-is (not wrapped)")
                return result
            if isinstance(result, list) and result:
                logger.info(f"Returning first item from list")
                return result[0]
            return None
        except Exception as e:
            logger.error(f"Failed to get scene '{name}': {e}")
            return None

    async def update_scene(self, name: str, data: dict) -> dict:
        """Update fields on an existing scene, targeted by name (no recreation)."""
        return await self._send("update-scene", name=name, data=data)

    async def update_actor(self, actor_name: str, actor_data: dict) -> dict:
        """Update an actor by name (e.g., set img to portrait URL).

        Searches for actor by: 1) scene tokens, 2) direct name search via relay, 3) partial match search.
        """
        actor_uuid = None

        # Strategy 1: Find actor through scene tokens
        logger.info(f"Looking for actor '{actor_name}' through scene tokens...")
        try:
            scenes = await self.get_scenes()
            for scene in scenes:
                tokens = await self.get_scene_tokens(scene.get("name"))
                for token in tokens:
                    token_name = token.get("name", "")
                    if token_name.lower() == actor_name.lower():
                        actor_uuid = token.get("actorUuid")
                        logger.info(f"Found actor '{actor_name}' via token in scene '{scene.get('name')}': {actor_uuid}")
                        break
                if actor_uuid:
                    break
        except Exception as e:
            logger.warning(f"Failed to search scene tokens: {e}")

        # Strategy 2: Search directly by actor name using relay search
        if not actor_uuid:
            logger.info(f"Not found via tokens, searching relay for '{actor_name}'...")
            try:
                # Try searching by the actor's name directly
                search_result = await self._send("search", query=actor_name)
                results = search_result.get("results", [])
                if isinstance(results, list):
                    for entry in results:
                        # Find actors matching the search
                        if entry.get("documentType") == "Actor" and entry.get("name", "").lower() == actor_name.lower():
                            actor_uuid = entry.get("uuid")
                            logger.info(f"Found actor '{actor_name}' via direct search: {actor_uuid}")
                            break
            except Exception as e:
                logger.debug(f"Direct name search failed: {e}")

        # Strategy 3: Fall back to get_actors search if still not found
        if not actor_uuid:
            logger.info(f"Not found via direct search, trying get_actors...")
            actors = await self.get_actors(world_only=True)
            actor = next((a for a in actors if a.get("name", "").lower() == actor_name.lower()), None)
            if actor:
                actor_uuid = actor.get("uuid")
                logger.info(f"Found actor '{actor_name}' via world actors: {actor_uuid}")

        # Strategy 4: Try compendium/all actors if still not found
        all_actors = None
        if not actor_uuid:
            logger.info(f"Not found in world actors, searching all actors...")
            all_actors = await self.get_actors(world_only=False)
            actor = next((a for a in all_actors if a.get("name", "").lower() == actor_name.lower()), None)
            if actor:
                actor_uuid = actor.get("uuid")
                logger.info(f"Found actor '{actor_name}' via all actors search: {actor_uuid}")

        if not actor_uuid:
            # Log available actors for debugging (reuse result from Strategy 4)
            if all_actors is None:
                all_actors = await self.get_actors(world_only=False)
            available_names = [a.get("name", "?") for a in all_actors[:20]]
            logger.warning(f"Actor '{actor_name}' not found. Available actors: {available_names}")
            return None

        # Update the actor using its UUID
        return await self.update_entity(uuid=actor_uuid, data=actor_data)

    async def upload_file(
        self,
        file_bytes: bytes,
        path: str,
        filename: str,
        mime_type: str = "image/png",
        source: str = "data",
        overwrite: bool = True,
    ) -> dict:
        """Upload a file into Foundry's data directory via the relay's REST /upload.

        Returns the relay's JSON response, which includes the saved path that can
        be used as a scene ``background.src`` or actor ``img``.
        """
        url = settings.relay_url.rstrip("/") + "/upload"
        params: Dict[str, str] = {}
        if settings.relay_headless_client_id:
            params["clientId"] = settings.relay_headless_client_id

        b64_data = base64.b64encode(file_bytes).decode("ascii")
        data_url = f"data:{mime_type};base64,{b64_data}"
        body = {
            "path": path,
            "filename": filename,
            "source": source,
            "mimeType": mime_type,
            "overwrite": overwrite,
            "fileData": data_url,
        }
        # REST endpoints require a scoped key; master key is WebSocket-only
        rest_key = settings.relay_scoped_key or self.api_key
        headers = {"x-api-key": rest_key}

        # Use synchronous httpx in a thread pool to avoid async timeout issues with large uploads
        def _do_upload():
            with httpx.Client(timeout=300) as client:
                resp = client.post(url, params=params, json=body, headers=headers)
                resp.raise_for_status()
                return resp.json()

        last_exc = None
        for attempt in range(3):
            try:
                result = await asyncio.to_thread(_do_upload)
                return result
            except Exception as e:
                last_exc = e
                # 408 means the relay timed out waiting for Foundry — wait and retry
                is_408 = "408" in str(e)
                if is_408 and attempt < 2:
                    wait = 5 * (attempt + 1)
                    logger.warning(f"Upload got 408 (attempt {attempt + 1}/3), retrying in {wait}s...")
                    await asyncio.sleep(wait)
                    continue
                logger.exception(f"Upload failed: {e}")
                raise
        raise last_exc

    async def search(self, query: str) -> dict:
        return await self._send("search", query=query)

    async def get_actors(self, world_only: bool = False) -> list:
        try:
            # Use excludeCompendiums parameter when filtering to world-only actors
            search_params = {"query": "actor"}
            if world_only:
                search_params["excludeCompendiums"] = True

            result = await self._send_with_retry("search", max_retries=1, **search_params)
            logger.debug(f"Relay search returned: {json.dumps(result, default=str)}")
            actors = []
            raw_data = result.get("results", result.get("data", []))
            if isinstance(raw_data, dict):
                raw_data = raw_data.get("actors", raw_data.get("entries", []))
            if isinstance(raw_data, list):
                for entry in raw_data:
                    # Filter by documentType (from relay search results)
                    if entry.get("documentType") != "Actor":
                        continue
                    # Double-check: filter to only world entities (not compendium)
                    # Compendium entries have a non-null package field
                    if world_only and entry.get("package"):
                        continue

                    actors.append({
                        "name": entry.get("name", "Unknown"),
                        "uuid": entry.get("uuid", entry.get("id", "")),
                        "type": entry.get("subType", "unknown"),
                        "package": entry.get("package"),  # None for world entities
                    })
            logger.info(f"get_actors found {len(actors)} actors (world_only={world_only}): {[a['name'] for a in actors]}")
            return actors
        except Exception as e:
            logger.error(f"Failed to get actors: {e}")
            return []

    async def get_scenes(self) -> list:
        try:
            result = await self._send_with_retry("search", max_retries=1, query="scene")
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
            if not details:
                return []

            # Safe nested dict access: details.get("data", {}) can return None
            tokens = details.get("tokens")
            if not tokens:
                data = details.get("data") or {}
                tokens = (data.get("tokens") if isinstance(data, dict) else None) or []
            if not tokens:
                return []
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
                    # Preserve absence as None: consumers (combat loop) decide how
                    # to classify unknown disposition. Defaulting to friendly here
                    # caused hostile tokens to be mistaken for PCs and stall combat.
                    "disposition": t.get("disposition"),
                }
                for t in tokens
            ]
        except Exception as e:
            logger.error(f"Failed to get scene tokens: {e}")
            return []

    async def set_active_scene(self, scene_name: str) -> dict:
        # Resolve the name inside Foundry (sees ALL scenes, tolerant of a
        # missing leading "The", case, and partial matches) then activate it.
        # The LLM often drops articles (e.g. "Summit Gatehouse" vs
        # "The Summit Gatehouse"), which the strict relay lookup rejects.
        # A scene switch triggers a full canvas redraw, so use the canvas timeout.
        want = json.dumps(scene_name)
        js = (
            f"const want={want};"
            "const norm=s=>String(s).toLowerCase().replace(/^the\\s+/,'').trim();"
            "let sc=game.scenes.getName(want)"
            "||game.scenes.find(s=>s.name.toLowerCase()===want.toLowerCase())"
            "||game.scenes.find(s=>norm(s.name)===norm(want))"
            "||game.scenes.find(s=>norm(s.name).includes(norm(want))||norm(want).includes(norm(s.name)));"
            "if(!sc)return{ok:false,error:'Scene not found',available:game.scenes.map(s=>s.name)};"
            "await sc.activate();"
            # Auto-place player-character tokens not already on this scene so
            # players keep vision/control when the GM moves the party.
            "let placedPCs=0;"
            "try{"
            "  const gs=sc.grid?.size||100;"
            "  const pcs=[...new Map(game.users.filter(u=>u.character&&u.role<4).map(u=>[u.character.id,u.character])).values()];"
            "  const toCreate=[]; let i=0;"
            "  for(const a of pcs){"
            "    if(sc.tokens.some(t=>t.actorId===a.id))continue;"
            "    const td=await a.getTokenDocument({x:Math.round(sc.width/2)+i*gs,y:Math.round(sc.height/2),hidden:false});"
            "    const o=td.toObject(); delete o._id; toCreate.push(o); i++;"
            "  }"
            "  if(toCreate.length){await sc.createEmbeddedDocuments('Token',toCreate); placedPCs=toCreate.length;}"
            "}catch(e){console.warn('aigm: PC token placement failed',e);}"
            "return{ok:true,name:sc.name,placedPCs};"
        )
        try:
            res = await self.execute_js(js, _timeout=settings.relay_rpc_timeout_canvas)
            result = res.get("result") if isinstance(res, dict) else None
            if isinstance(result, dict) and result.get("ok"):
                if result.get("name") != scene_name:
                    logger.info(f"set_active_scene: resolved '{scene_name}' -> '{result['name']}'")
                return result
            logger.warning(f"set_active_scene: {scene_name!r} not matched; available={result.get('available') if isinstance(result, dict) else '?'}")
        except Exception as e:
            logger.warning(f"set_active_scene via execute-js failed ({e}); falling back to switch-scene")
        # Fallback: strict relay lookup
        return await self._send(
            "switch-scene", name=scene_name, _timeout=settings.relay_rpc_timeout_canvas
        )

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

    async def play_sound(self, sound_name: str, volume: float = 0.5) -> dict:
        return await self._send("play-sound", name=sound_name, volume=volume)

    async def play_playlist(self, playlist_name: str, volume: float = 0.5) -> dict:
        return await self._send("play-playlist", name=playlist_name, volume=volume)

    async def roll_initiative(self) -> dict:
        return await self._send("roll-initiative")

    async def start_encounter(self, tokens: list = None) -> dict:
        return await self._send("start-encounter", tokens=tokens or [])

    async def end_encounter(self) -> dict:
        return await self._send("end-encounter")

    async def use_spell_slot(self, actor_uuid: str, spell_level: int) -> dict:
        return await self._send("use-spell-slot", actor_uuid=actor_uuid, level=spell_level)

    async def track_action(self, actor_uuid: str, action_type: str) -> dict:
        return await self._send("track-action", actor_uuid=actor_uuid, action_type=action_type)

    async def request_skill_check(
        self, actor_uuid: str, skill: str, dc: int,
        reason: str = None, advantage: bool = None
    ) -> dict:
        return await self._send(
            "request-skill-check",
            actor_uuid=actor_uuid,
            skill=skill,
            dc=dc,
            reason=reason,
            advantage=advantage,
        )

    async def apply_condition(
        self, actor_uuid: str, condition: str, duration: str = None
    ) -> dict:
        return await self._send(
            "apply-condition",
            actor_uuid=actor_uuid,
            condition=condition,
            duration=duration,
        )

    async def opportunity_attack(self, attacker_uuid: str, target_uuid: str) -> dict:
        return await self._send(
            "opportunity-attack",
            attacker_uuid=attacker_uuid,
            target_uuid=target_uuid,
        )

    async def get_tactical_data(self, actor_uuid: str) -> dict:
        return await self._send("get-tactical-data", actor_uuid=actor_uuid)

    async def get_users(self) -> list:
        try:
            result = await self._send("get-users")
            if isinstance(result, list):
                return result
            data = result.get("data", result.get("users", []))
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                users = data.get("users", [])
                return users if isinstance(users, list) else []
            return []
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

    # --- FoundryVTT World Scanning ---

    async def scan_world(self) -> dict:
        """Comprehensive scan of the connected FoundryVTT world.

        Returns a structured summary of the world including:
        - World metadata (name, version, systems/modules)
        - Scenes (maps) with token counts and lighting
        - Actors (NPCs, monsters, PCs) with key attributes
        - Items/equipment discovered
        - Journal entries
        - Active quests/encounters
        - Modules/add-ons and their capabilities
        """
        scan_result: Dict[str, Any] = {
            "world": {},
            "scenes": [],
            "actors": [],
            "items": [],
            "journal": [],
            "quests": [],
            "modules": [],
        }

        try:
            # 1. World structure — name, version, modules
            structure = await self.get_structure()
            world_data = structure.get("world", structure.get("data", {}))
            scan_result["world"] = {
                "name": world_data.get("name", "Unknown"),
                "version": world_data.get("version", ""),
                "systems": [
                    {"name": s.get("name", ""), "version": s.get("version", ""), "enabled": s.get("active", s.get("enabled", False))}
                    for s in world_data.get("systems", world_data.get("modules", []))
                ],
                "rooms": world_data.get("rooms", []),
                "totalActors": world_data.get("totalActors", len(world_data.get("actors", []))),
                "totalItems": world_data.get("totalItems", 0),
            }

            # 2. Scenes (maps) — reuse the structure already fetched above
            scenes_raw = structure.get("scenes", structure.get("data", {}).get("scenes", []))
            if isinstance(scenes_raw, dict):
                scenes_raw = list(scenes_raw.values())
            for scene in scenes_raw:
                scan_result["scenes"].append({
                    "id": scene.get("_id", scene.get("id", "")),
                    "name": scene.get("name", scene.get("title", "Unknown")),
                    "width": scene.get("width", 0),
                    "height": scene.get("height", 0),
                    "tokenCount": scene.get("tokenCount", scene.get("tokens", {})),
                    "active": scene.get("active", False),
                    "background": scene.get("background", {}).get("src", ""),
                    "fogOfWar": scene.get("fogOfWar", False),
                    "darkness": scene.get("darkness", 0),
                    "timedLights": bool(scene.get("timedLights", [])),
                })

            # 3. Actors (NPCs, monsters)
            actors_result = await self.get_actors()
            scan_result["actors"] = actors_result

            # 4. Items
            try:
                items_result = await self._send("search", query="item")
                items_raw = items_result.get("data", items_result.get("results", []))
                if isinstance(items_raw, dict):
                    items_raw = items_raw.get("items", items_raw.get("entries", []))
                if isinstance(items_raw, list):
                    for item in items_raw:
                        scan_result["items"].append({
                            "name": item.get("name", "Unknown"),
                            "type": item.get("type", item.get("kind", "unknown")),
                            "subtype": item.get("subtype", item.get("data", {}).get("subtype", "")),
                            "uuid": item.get("uuid", item.get("id", "")),
                            "rarity": item.get("rarity", item.get("data", {}).get("rarity", "")),
                            "equipped": item.get("equipped", False),
                        })
            except Exception:
                scan_result["items"] = []

            # 5. Journal entries
            try:
                journal_result = await self._send("search", query="journal")
                journal_raw = journal_result.get("data", journal_result.get("results", []))
                if isinstance(journal_raw, dict):
                    journal_raw = journal_raw.get("journal", journal_raw.get("entries", []))
                if isinstance(journal_raw, list):
                    for entry in journal_raw:
                        scan_result["journal"].append({
                            "name": entry.get("name", entry.get("title", "Unknown")),
                            "uuid": entry.get("uuid", entry.get("id", "")),
                            "parent": entry.get("parent", entry.get("parentId", "")),
                            "sorting": entry.get("sorting", 0),
                        })
            except Exception:
                scan_result["journal"] = []

            # 6. Combat encounters / active quests
            try:
                encounters_result = await self._send("search", query="combat")
                encounters_raw = encounters_result.get("data", encounters_result.get("results", []))
                if isinstance(encounters_raw, dict):
                    encounters_raw = encounters_raw.get("encounters", encounters_raw.get("combats", []))
                if isinstance(encounters_raw, list):
                    for enc in encounters_raw:
                        scan_result["quests"].append({
                            "name": enc.get("name", enc.get("title", "Encounter")),
                            "uuid": enc.get("uuid", enc.get("id", "")),
                            "active": enc.get("active", False),
                            "tokenCount": len(enc.get("tokens", enc.get("actors", []))),
                            "round": enc.get("round", 0),
                            "turn": enc.get("turn", 0),
                        })
            except Exception:
                scan_result["quests"] = []

            # 7. Modules/Add-ons info from world structure
            modules = scan_result["world"].get("systems", [])
            scan_result["modules"] = modules

        except Exception as e:
            logger.error(f"World scan failed: {e}")
            scan_result["error"] = str(e)

        return scan_result

    async def discover_addon_capabilities(self, scan_data: dict) -> dict:
        """Analyze a world scan and produce a structured capability list.

        Based on the scanned modules, actors, scenes, and items, produce
        a human-readable summary of what the GM can use when building a
        campaign in this FoundryVTT world.
        """
        capabilities: Dict[str, Any] = {
            "available_maps": len(scan_data.get("scenes", [])),
            "scenes_with_fog": sum(1 for s in scan_data.get("scenes", []) if s.get("fogOfWar")),
            "scenes_with_lighting": sum(1 for s in scan_data.get("scenes", []) if s.get("timedLights")),
            "total_actors": len(scan_data.get("actors", [])),
            "actors_with_combat": sum(1 for a in scan_data.get("actors", [])
                                       if a.get("hp") is not None and a.get("hp") != "?"),
            "total_items": len(scan_data.get("items", [])),
            "total_journal_entries": len(scan_data.get("journal", [])),
            "active_encounters": sum(1 for q in scan_data.get("quests", []) if q.get("active")),
            "modules": [],
            "suggestions": [],
        }

        # Module capabilities
        for mod in scan_data.get("world", {}).get("systems", []):
            mod_info = {
                "name": mod.get("name", "Unknown"),
                "version": mod.get("version", ""),
                "enabled": mod.get("enabled", mod.get("active", False)),
            }
            capabilities["modules"].append(mod_info)

            # Classify modules by type
            mod_name = mod.get("name", "").lower()
            if "combat" in mod_name or "initiative" in mod_name:
                capabilities["suggestions"].append(
                    f"Combat Tracker add-on '{mod_info['name']}' available — use for encounter management"
                )
            elif "lighting" in mod_name or "light" in mod_name:
                capabilities["suggestions"].append(
                    f"Lighting system '{mod_info['name']}' available — use for atmospheric scenes"
                )
            elif "inventory" in mod_name or "loot" in mod_name:
                capabilities["suggestions"].append(
                    f"Inventory/Loot system '{mod_info['name']}' available — use for treasure generation"
                )
            elif "spell" in mod_name or "magic" in mod_name:
                capabilities["suggestions"].append(
                    f"Spell/Magic system '{mod_info['name']}' available — use for magic effects"
                )

        if capabilities["scenes_with_fog"] == 0:
            capabilities["suggestions"].append(
                "No fog-of-war scenes detected — consider enabling fog for exploration campaigns"
            )
        if capabilities["available_maps"] == 0:
            capabilities["suggestions"].append(
                "No scenes/maps found — campaign will need to create new maps"
            )

        return capabilities

    async def set_npc_context(self, context: str):
        self._npc_context = context
        logger.info(f"NPC context updated ({len(context)} chars)")

    async def set_world_context(self, context: str):
        self._world_context = context
        logger.info(f"World context updated ({len(context)} chars)")

    async def set_ai_tone(self, tone: str):
        self._ai_tone = tone
        logger.info(f"AI tone updated: {tone[:50]}...")

    async def get_world_info(self) -> dict:
        """Get world metadata, active modules, and connected users."""
        try:
            result = await self._send("world-info")
            return result.get("data", result) if isinstance(result, dict) else {}
        except Exception as e:
            logger.error(f"Failed to get world info: {e}")
            return {}

    # --- Canvas document operations (walls, lights, sounds, tokens, tiles) ---

    # Maps our plural doc_type to the canonical Foundry embedded Document name
    # that Scene.createEmbeddedDocuments() requires (passed as `className`).
    _CANVAS_DOC_CLASS = {
        "walls": "Wall",
        "lights": "AmbientLight",
        "sounds": "AmbientSound",
        "tokens": "Token",
        "tiles": "Tile",
        "drawings": "Drawing",
        "notes": "Note",
        "templates": "MeasuredTemplate",
        "regions": "Region",
    }

    async def canvas_create(self, doc_type: str, data: Any) -> dict:
        """Create canvas embedded documents.

        doc_type: 'walls', 'lights', 'sounds', 'tokens', 'tiles',
                  'drawings', 'notes', 'templates', 'regions'
        data: list of document dicts, or a single dict (auto-wrapped)
        """
        if isinstance(data, dict):
            data = [data]
        class_name = self._CANVAS_DOC_CLASS.get(doc_type, doc_type)
        return await self._send(
            "create-canvas-document", documentType=doc_type, className=class_name, data=data,
            _timeout=settings.relay_rpc_timeout_canvas,
        )

    async def canvas_get(self, doc_type: str) -> list:
        """Get all canvas embedded documents of a given type on the active scene."""
        try:
            result = await self._send("get-canvas-documents", documentType=doc_type)
            docs = result.get("data", result.get("documents", result.get("results", [])))
            return docs if isinstance(docs, list) else []
        except Exception as e:
            logger.error(f"Failed to get canvas documents ({doc_type}): {e}")
            return []

    async def canvas_update(self, doc_type: str, updates: dict, uuid: str = None) -> dict:
        """Update a canvas embedded document."""
        kwargs: Dict[str, Any] = {
            "documentType": doc_type,
            "className": self._CANVAS_DOC_CLASS.get(doc_type, doc_type),
            "data": updates,
        }
        if uuid:
            kwargs["uuid"] = uuid
        return await self._send("update-canvas-document", **kwargs)

    async def canvas_delete(self, doc_type: str, uuid: str = None, ids: list = None) -> dict:
        """Delete canvas embedded document(s)."""
        kwargs: Dict[str, Any] = {
            "documentType": doc_type,
            "className": self._CANVAS_DOC_CLASS.get(doc_type, doc_type),
        }
        if uuid:
            kwargs["uuid"] = uuid
        if ids:
            kwargs["ids"] = ids
        return await self._send("delete-canvas-document", **kwargs)

    async def execute_js(self, code: str, _timeout: Optional[float] = None) -> dict:
        """Execute arbitrary JavaScript in the connected Foundry world.

        Requires the execute:js scope on the API key. Use for operations
        not covered by the relay's structured endpoints. _timeout overrides
        the default reply timeout (e.g. canvas ops pass a longer value).
        """
        return await self._send("execute-js", script=code, _timeout=_timeout)

    async def create_entity(self, entity_type: str, data: dict) -> dict:
        """Create a Foundry document (Scene, Actor, Item, JournalEntry, etc.)"""
        return await self._send("create", entityType=entity_type, data=data)

    async def move_token(self, token_id: str, x: float, y: float) -> dict:
        """Move a token to absolute pixel coordinates on the active scene."""
        return await self._send("move-token", tokenId=token_id, x=x, y=y)

    async def place_token(
        self,
        actor_name: str,
        x: float,
        y: float,
        disposition: int = 0,
        hidden: bool = False,
    ) -> dict:
        """Place an actor's token on the current scene at (x, y) pixels.

        Looks up the actor UUID by name, then creates a Token canvas document.
        disposition: -1=hostile, 0=neutral, 1=friendly
        """
        actors = await self.get_actors(world_only=True)
        actor = next(
            (a for a in actors if a.get("name", "").lower() == actor_name.lower()),
            None,
        )
        if not actor:
            logger.warning(f"place_token: actor '{actor_name}' not found in world actors")
            return {"error": f"Actor '{actor_name}' not found"}

        token_data = {
            "name": actor_name,
            "actorId": actor.get("uuid", "").split(".")[-1],
            "actorLink": False,
            "x": x,
            "y": y,
            "disposition": disposition,
            "hidden": hidden,
            "width": 1,
            "height": 1,
        }
        return await self.canvas_create("tokens", token_data)

    async def clear_canvas_layer(self, doc_type: str) -> dict:
        """Remove all documents from a canvas layer (e.g. 'walls', 'lights').

        Uses execute-js so a single round-trip clears everything atomically.
        """
        type_map = {
            "walls": "Wall",
            "lights": "AmbientLight",
            "sounds": "AmbientSound",
            "tokens": "Token",
            "tiles": "Tile",
            "drawings": "Drawing",
            "notes": "Note",
            "templates": "MeasuredTemplate",
        }
        foundry_type = type_map.get(doc_type, doc_type)
        code = (
            f"const scene = canvas.scene;"
            f"const ids = scene.{doc_type}.map(d => d.id);"
            f"if (ids.length) await scene.deleteEmbeddedDocuments('{foundry_type}', ids);"
            f"ids.length"
        )
        try:
            return await self.execute_js(code, _timeout=settings.relay_rpc_timeout_canvas)
        except Exception as e:
            logger.warning(f"clear_canvas_layer({doc_type}) via execute-js failed: {e}. Trying canvas_delete.")
            return await self.canvas_delete(doc_type)

    async def configure_scene(self, updates: dict, scene_name: str = None) -> dict:
        """Update scene-level settings (darkness, fog, global illumination, etc.)"""
        if scene_name:
            return await self.update_scene(scene_name, updates)
        # Update the currently active scene via execute-js
        code = f"await canvas.scene.update({json.dumps(updates)}); true"
        try:
            return await self.execute_js(code, _timeout=settings.relay_rpc_timeout_canvas)
        except Exception as e:
            logger.warning(f"configure_scene via execute-js failed: {e}")
            return {"error": str(e)}

    def reset_message_id(self):
        """Reset the message ID counter for a new session.

        Only resets if there are no pending RPC futures to avoid
        requestId collisions.
        """
        if not self._rpc_futures:
            self._message_id = 0
        else:
            logger.warning(
                f"Skipping message ID reset: {len(self._rpc_futures)} "
                "pending RPC futures from previous session"
            )
