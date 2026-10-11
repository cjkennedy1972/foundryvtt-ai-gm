"""FoundryVTT client: the engine's interface to Foundry, over the playwright transport."""

import asyncio
import base64
import json
import logging
from typing import Any, Callable, Dict, List, Optional, TypedDict

from config import settings
from foundry import world_cli_reads, world_cli_writes

logger = logging.getLogger(__name__)


class ActiveModulesInfo(TypedDict, total=False):
    modules: List[dict]
    world: Dict[str, Any]

# Maps the event push type a transport emits to the channel name callers register handlers on.
_EVENT_TYPE_TO_CHANNEL = {
    "chat-event":   "chat-events",
    "roll-event":   "roll-events",
    "combat-event": "combat-events",
    "scene-event":  "scene-events",
    "actor-event":  "actor-events",
    "hook":         "hooks",
}


class FoundryClient:
    """The engine's connection to a FoundryVTT world.

    Requests go to the playwright transport (a headless GM browser run from this
    process). Each message type the engine uses has a handler there; event channels
    (chat, rolls, combat, scenes, pause) are pushed back through the transport and
    routed to the handlers registered with subscribe().
    """

    def __init__(self):
        self._ai_name = None
        self._pw = None  # PlaywrightTransport, created on connect
        self._connected = False
        self._closing: bool = False  # set on intentional disconnect
        self._handlers: Dict[str, List[Callable]] = {}
        self._subscribed_channels: set = set()
        self._ai_tone = settings.ai_tone
        self._npc_context = ""
        self._world_context = ""
        # Event handlers run on a dedicated worker draining this queue, NOT inline in the
        # transport callback. A handler (e.g. chat) may itself issue requests and await their
        # replies, so running it inline could block the callback that delivers them.
        self._event_queue: asyncio.Queue = asyncio.Queue()
        self._event_worker_task: Optional[asyncio.Task] = None
        # Background task tracking — all tasks spawned in this client are tracked here, so
        # they are cancelled on disconnect and do not survive shutdown.
        self._background_tasks: set = set()
        self._last_connect_error: str = ""
        # Track the active scene ID for operations that need scene context
        self._current_scene_id: Optional[str] = None

    # Prefers World CLI over the relay/execute_js where it can answer (foundry/world_cli_router.py); set at
    # startup when World CLI is configured. A class default, so a client built without __init__ still works.
    world_cli_router = None

    async def _cli_write(self, write, *, same_key_retry: bool = False):
        """The result of a World CLI write, or None meaning: use the method's original path. May raise
        WorldCLIWriteUncertain when the write could not be confirmed (the relay is deliberately not tried)."""
        router = self.world_cli_router
        if router is None or write is None or not router.writes:
            return None
        return await router.write(write[0], write[1], same_key_retry=same_key_retry)

    def _writes_via_cli(self) -> bool:
        router = self.world_cli_router
        return bool(router and router.writes and not router.tripped())

    async def _cli_read(self, adapter, *args):
        """A World CLI answer from `adapter`, or None meaning: use the method's original path."""
        router = self.world_cli_router
        return await router.read(adapter, *args) if router else None

    def _track_scene(self, scene_id: str):
        """Track the active scene ID for subsequent operations."""
        if scene_id:
            self._current_scene_id = scene_id
            logger.debug(f"[FoundryClient] Tracking active scene: {scene_id}")

    def set_ai_name(self, name: str):
        self._ai_name = name
        logger.info(f"GM speaker name updated to: {name}")

    def _get_speaker_name(self) -> str:
        return self._ai_name if self._ai_name else settings.ai_name

    async def connect(self, max_retries: int = 5) -> bool:
        """Log into Foundry through the playwright transport, retrying with backoff.

        Callers that need the connection to be up should loop until this returns True.
        """
        self._closing = False
        delay = 1.0
        for attempt in range(max_retries):
            if await self._connect_playwright():
                return True
            if attempt < max_retries - 1:
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30.0)
        return False

    async def ensure_connected(self):
        """Reconnect if the Foundry connection is down. The transport also reconnects on its own."""
        if self._pw is None or not self._pw.connected:
            await self.connect(max_retries=1)

    async def disconnect(self):
        """Close the Foundry connection and stop background work."""
        self._closing = True
        if self._pw is not None:
            await self._pw.close()
            self._pw = None
        self._connected = False
        await self.cancel_all_background_tasks()
        if self._event_worker_task:
            self._event_worker_task.cancel()
            try:
                await self._event_worker_task
            except asyncio.CancelledError:
                pass
            self._event_worker_task = None
        logger.info("Disconnected from Foundry")

    def _spawn_background_task(self, coro):
        """Spawn a background task and track it for cancellation on disconnect."""
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

    async def cancel_all_background_tasks(self):
        """Cancel all tracked background tasks and wait for them to finish.

        Detects hung tasks that don't complete cancellation within a timeout.
        """
        if not self._background_tasks:
            return

        logger.debug(f"Cancelling {len(self._background_tasks)} background tasks")
        for task in self._background_tasks:
            if not task.done():
                task.cancel()

        # Wait for all tasks to complete cancellation with a timeout to detect
        # hung tasks that ignore cancellation. asyncio.wait, not wait_for(gather):
        # wait_for cancels the gather on timeout and then waits for it to finish,
        # which a task that swallows cancellation never does, so the "timeout" hung.
        try:
            done, pending = await asyncio.wait(set(self._background_tasks), timeout=5.0)
            for task in done:
                if not task.cancelled() and task.exception() is not None:
                    logger.warning(f"Task raised exception during cancellation: {task.exception()}")
            if pending:
                logger.error(
                    f"Background task cancellation timeout: {len(pending)} tasks did not complete"
                )
        except (asyncio.CancelledError, RuntimeError):
            # Expected during shutdown
            pass

        self._background_tasks.clear()

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def _event_worker(self):
        """Run queued event handlers off the reader loop, in arrival order.

        Handlers are awaited one at a time so a single event channel cannot
        process two events concurrently (e.g. two player messages racing into
        overlapping LLM turns), while the reader loop remains free to route the
        RPC replies those handlers depend on.
        """
        while True:
            channel, data = await self._event_queue.get()
            try:
                # Snapshot: `await handler(data)` yields, and a timing-out
                # wait_for_hook removes its own handler from this same list
                # (see the finally in wait_for_hook). Mutating mid-iteration
                # shifted the index and skipped the next handler, so a sibling
                # waiter silently missed the event it was waiting for.
                for handler in list(self._handlers.get(channel, [])):
                    try:
                        await handler(data)
                    except Exception as e:
                        logger.error(f"Handler error on channel {channel}: {e}", exc_info=True)
            finally:
                self._event_queue.task_done()

    async def _dispatch_event(self, message: dict):
        """Route an event pushed by the transport to the handlers registered on its channel."""
        msg_type = message.get("type", "")
        channel = _EVENT_TYPE_TO_CHANNEL.get(msg_type)
        if channel and channel in self._handlers:
            self._event_queue.put_nowait((channel, message))

    async def _connect_playwright(self) -> bool:
        from foundry.playwright_transport import PlaywrightTransport

        if self._pw is None:
            self._pw = PlaywrightTransport()
            self._pw.on_event = self._on_playwright_event
            self._pw.on_state = self._on_playwright_state
        try:
            await self._pw.connect()
        except Exception as e:
            self._last_connect_error = str(e)
            logger.error(f"Playwright transport failed to connect: {e}")
            return False
        self._connected = True
        if self._event_worker_task is None or self._event_worker_task.done():
            self._event_worker_task = self._spawn_background_task(self._event_worker())
        return True

    async def _on_playwright_event(self, message: dict):
        # Same routing as a relay push, so handlers see identical messages on both backends.
        await self._dispatch_event(message)

    def _on_playwright_state(self, up: bool):
        self._connected = up

    async def _send(self, msg_type: str, *, _timeout: Optional[float] = None, **params) -> dict:
        """Send a request to Foundry through the transport and await its reply.

        _timeout overrides the default reply timeout (settings.relay_rpc_timeout).
        """
        if self._pw is None:
            raise ConnectionError("Not connected to Foundry")
        timeout = _timeout if _timeout is not None else settings.relay_rpc_timeout
        try:
            result = await asyncio.wait_for(self._pw.send(msg_type, **params), timeout=timeout)
        except asyncio.TimeoutError:
            raise ConnectionError(f"RPC request {msg_type} timed out")
        if isinstance(result, dict) and result.get("error"):
            raise RuntimeError(f"Foundry error [{msg_type}]: {result['error']}")
        return result

    async def _send_with_retry(self, msg_type: str, max_retries: int = 2, _timeout: Optional[float] = None, **params) -> dict:
        """Send a request with retry logic for transient timeouts.

        For search-heavy operations like get_actors(), retry on timeout since
        Foundry may be briefly unresponsive.
        """
        for attempt in range(max_retries):
            try:
                return await self._send(msg_type, _timeout=_timeout, **params)
            except ConnectionError as e:
                if "timed out" not in str(e) or attempt == max_retries - 1:
                    raise
                wait_time = 2 ** attempt
                logger.warning(f"RPC request {msg_type} timed out; retrying in {wait_time}s (attempt {attempt + 1}/{max_retries})")
                await asyncio.sleep(wait_time)

    async def subscribe_to_channel(self, channel: str):
        """Install the Foundry hooks for an event channel. Idempotent."""
        if channel in self._subscribed_channels:
            return
        self._subscribed_channels.add(channel)
        try:
            if self._pw is None:
                raise ConnectionError("Not connected to Foundry")
            await self._pw.enable_channel(channel)
            logger.info(f"Subscribed to channel: {channel}")
        except Exception as e:
            logger.error(f"Failed to subscribe to {channel}: {e}", exc_info=True)

    def subscribe(self, channel: str, handler: Callable):
        """Register a push-event handler for a channel."""
        if channel not in self._handlers:
            self._handlers[channel] = []
        self._handlers[channel].append(handler)

    async def wait_for_hook(self, hook_name: str, timeout: float = 10.0) -> bool:
        """Wait for a specific Foundry hook to fire, replacing sleep-based waits.

        Subscribes to the hooks channel and waits for the named hook, returning
        when it fires or timeout expires. Used to properly synchronize scene
        operations (canvas ready, token created, etc.) instead of sleeping.

        ponytail: global event handler, per-scene handlers if hooks become noisy
        """
        await self.subscribe_to_channel("hooks")

        hook_fired = asyncio.Event()

        # Async because the event worker awaits every handler; a plain function
        # here raised "await None" TypeErrors on each hook event.
        async def handler(event: dict):
            if event.get("hook") == hook_name:
                hook_fired.set()

        self.subscribe("hooks", handler)

        try:
            await asyncio.wait_for(hook_fired.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            logger.warning(f"Hook '{hook_name}' did not fire within {timeout}s")
            return False
        finally:
            # Deregister — otherwise every call leaks a permanent handler that
            # runs on all future hook events.
            try:
                self._handlers.get("hooks", []).remove(handler)
            except ValueError:
                pass

    async def activate_scene_and_wait(self, scene_name: str, timeout: float = 10.0) -> dict:
        """Activate a scene while listening for its canvas-ready hook.

        The listener is installed before activation. Registering it after
        ``set_active_scene`` returns is racy because Foundry can emit
        ``canvasReady`` during ``Scene.activate()`` itself.
        """
        await self.subscribe_to_channel("hooks")

        hook_fired = asyncio.Event()

        async def handler(event: dict):
            if event.get("hook") == "canvasReady":
                hook_fired.set()

        self.subscribe("hooks", handler)
        try:
            result = await self.set_active_scene(scene_name)
            try:
                await asyncio.wait_for(hook_fired.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                logger.warning(f"Hook 'canvasReady' did not fire within {timeout}s")
            return result
        finally:
            try:
                self._handlers.get("hooks", []).remove(handler)
            except ValueError:
                pass

    # --- FoundryVTT API methods ---

    async def chat_message(self, text: str, speaker: str = "", whisper: List[str] = None) -> dict:
        # Public messages only: a whisper takes user NAMES here and World CLI wants ids. The speaker is not
        # sent, as before: the relay never set an alias either, and the engine's echo guard treats an empty
        # alias as "posted by the AI".
        if not whisper:
            routed = await self._cli_write(world_cli_writes.chat_create(text), same_key_retry=True)
            if routed is not None:
                return routed
        return await self._send_with_retry("chat-send", max_retries=3, content=text, speaker=speaker, whisper=whisper or [])

    async def roll(self, formula: str, speaker: str = "", flavor: str = None) -> dict:
        # createChatMessage=True makes this a real Foundry roll posted to chat so
        # the 3D dice addon (Dice So Nice) animates it for every client. Without
        # it the relay does a silent manual roll (chatMessageCreated:false) that
        # no dice addon ever sees.
        return await self._send(
            "roll", formula=formula, speaker=speaker, flavor=flavor,
            createChatMessage=True,
        )

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
                logger.info("Returning result as-is (not wrapped)")
                return result
            if isinstance(result, list) and result:
                logger.info("Returning first item from list")
                return result[0]
            return None
        except Exception as e:
            logger.error(f"Failed to get scene '{name}': {e}", exc_info=True)
            return None

    async def update_scene(self, name: str, data: dict) -> dict:
        """Update fields on an existing scene, targeted by name (no recreation)."""
        if self._writes_via_cli():
            scene_id = await self._cli_read(world_cli_reads.scene_id, name)
            if scene_id:
                routed = await self._cli_write(world_cli_writes.scene_update(scene_id, data))
                if routed is not None:
                    return routed
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
            logger.info("Not found via direct search, trying get_actors...")
            actors = await self.get_actors(world_only=True)
            actor = next((a for a in actors if a.get("name", "").lower() == actor_name.lower()), None)
            if actor:
                actor_uuid = actor.get("uuid")
                logger.info(f"Found actor '{actor_name}' via world actors: {actor_uuid}")

        # Strategy 4: Try compendium/all actors if still not found
        all_actors = None
        if not actor_uuid:
            logger.info("Not found in world actors, searching all actors...")
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
        """Upload a file into Foundry's data directory.

        Returns the upload result, which includes the saved path that can be used
        as a scene ``background.src`` or actor ``img``.
        """
        routed = await self._cli_write(
            world_cli_writes.file_upload(file_bytes, path, filename, mime_type, source, overwrite), same_key_retry=True)
        if routed is not None:
            return routed
        data_url = f"data:{mime_type};base64,{base64.b64encode(file_bytes).decode('ascii')}"
        result = await self._send(
            "upload-file",
            path=path,
            filename=filename,
            source=source,
            mimeType=mime_type,
            overwrite=overwrite,
            fileData=data_url,
            _timeout=settings.relay_rpc_timeout_canvas,
        )
        return result.get("data", result)

    async def search(self, query: str) -> dict:
        return await self._send("search", query=query)

    async def get_actors(self, world_only: bool = False, strict: bool = False) -> list:
        """Return actors from Foundry's live game.actors collection via execute_js.

        This is more reliable than the search index, which only returns actors
        that happen to be indexed and visible to the search engine.  game.actors
        is the authoritative, permission-filtered collection for the logged-in
        headless client (GM), so all world actors are always returned.

        strict=True re-raises transport failures instead of returning []. The
        default stays lenient because 19 callers treat [] as "no actors", but a
        caller that must tell "the lookup failed" from "the world has no
        actors" cannot work with a swallowed error: it made
        _is_player_character's own except branch unreachable, so a relay blip
        read as "this name is not a PC".
        """
        try:
            routed = await self._cli_read(world_cli_reads.actors)
            if routed is not None:
                logger.info(f"get_actors found {len(routed)} actors via World CLI (world_only={world_only})")
                return routed
            js = (
                "return Array.from(game.actors || []).map(a => ({"
                "  id: a.id,"
                "  uuid: a.uuid,"
                "  name: a.name,"
                "  type: a.type,"
                "  hasPlayerOwner: a.hasPlayerOwner,"
                "  hp: a.system?.attributes?.hp?.value ?? null,"
                "  maxHp: a.system?.attributes?.hp?.max ?? null"
                "}));"
            )
            res = await self._send_with_retry("execute-js", max_retries=3, script=js)
            raw = res.get("result", []) if isinstance(res, dict) else []
            actors = []
            if isinstance(raw, list):
                for entry in raw:
                    actors.append({
                        "name": entry.get("name", "Unknown"),
                        "uuid": entry.get("uuid", entry.get("id", "")),
                        "type": entry.get("type", "unknown"),
                        "has_player_owner": entry.get("hasPlayerOwner", False),
                        "hp": entry.get("hp"),
                        "max_hp": entry.get("maxHp"),
                    })
            logger.info(f"get_actors found {len(actors)} actors (world_only={world_only}): {[a['name'] for a in actors]}")
            return actors
        except Exception as e:
            logger.error(f"Failed to get actors: {e}", exc_info=True)
            if strict:
                raise
            return []

    async def get_player_actor_mapping(self) -> dict:
        """Return a mapping of player-owned actor names/UUIDs to user IDs.

        Returns: {
            "actor_names": {"ActorName": "user_id", ...},
            "actor_uuids": {"actor.uuid": "user_id", ...}
        }
        """
        try:
            routed = await self._cli_read(world_cli_reads.player_actor_mapping)
            if routed is not None:
                return routed
            # Get all player characters and their owners
            js = (
                # v14: Actor#permission is the *current user's* level (a number), the
                # per-user map is `ownership`. GM users appear in it too (deploy
                # grants them OWNER), so skip them to land on the player.
                "return Array.from(game.actors || []).filter(a => a.hasPlayerOwner).map(a => {"
                "  const owners = Object.keys(a.ownership || {}).filter(uid => uid !== 'default'"
                "    && a.ownership[uid] >= 3 && !game.users.get(uid)?.isGM);"
                "  const ownerId = owners.length > 0 ? owners[0] : null;"
                "  return {"
                "    name: a.name,"
                "    uuid: a.uuid,"
                "    ownerId: ownerId"
                "  };"
                "}).filter(a => a.ownerId !== null);"
            )
            res = await self._send_with_retry("execute-js", max_retries=3, script=js)
            raw = res.get("result", []) if isinstance(res, dict) else []

            mapping = {"actor_names": {}, "actor_uuids": {}}
            if isinstance(raw, list):
                for entry in raw:
                    name = entry.get("name", "")
                    uuid = entry.get("uuid", "")
                    owner_id = entry.get("ownerId")
                    if name and owner_id:
                        mapping["actor_names"][name] = owner_id
                    if uuid and owner_id:
                        mapping["actor_uuids"][uuid] = owner_id

            logger.info(f"get_player_actor_mapping: {len(mapping['actor_names'])} player actors mapped")
            return mapping
        except Exception as e:
            logger.error(f"Failed to get player actor mapping: {e}", exc_info=True)
            return {"actor_names": {}, "actor_uuids": {}}

    async def _world_metadata(self) -> dict:
        """Name, version and system of the connected world.

        Read off game.world/game.system, the only place it is exposed. When
        execute_js is gated this degrades to the same empty shape the caller
        used to get unconditionally.
        """
        blank = {"name": "Unknown", "version": "", "systems": [],
                 "rooms": [], "totalActors": 0, "totalItems": 0}
        routed = await self._cli_read(world_cli_reads.world_metadata)
        if routed is not None:
            return routed
        try:
            res = await self.execute_js(
                "return {name: game.world.title, id: game.world.id, "
                "version: game.version, system: game.system.id, "
                "systemVersion: game.system.version, "
                "totalActors: game.actors.size, totalItems: game.items.size};"
            )
        except Exception as e:
            logger.debug(f"World metadata unavailable: {e}")
            return blank
        info = res.get("result") if isinstance(res, dict) else None
        if not isinstance(info, dict):
            return blank
        return {
            "name": info.get("name") or "Unknown",
            "id": info.get("id", ""),
            "version": info.get("version", ""),
            "systems": (
                [{"name": info["system"], "version": info.get("systemVersion", ""),
                  "enabled": True}]
                if info.get("system") else []
            ),
            "rooms": [],
            "totalActors": info.get("totalActors", 0),
            "totalItems": info.get("totalItems", 0),
        }

    async def _search_documents(self, document_type: str) -> list:
        """Every document of one type, via the relay's documentType filter.

        Not `query=`. That is a full-text match over all documents, so
        query="item" answers with rules pages whose text mentions items and
        query="journal" answers with nothing at all.
        """
        result = await self._send_with_retry(
            "search", max_retries=1, filter=f"documentType:{document_type}"
        )
        raw = result.get("data", result.get("results", []))
        if isinstance(raw, dict):
            # Wrapped one level deeper, under a key named for the type
            # ("scenes", "entries", "results"). Take the first list rather
            # than guessing at the name per document type.
            raw = next((v for v in raw.values() if isinstance(v, list)), [])
        return raw if isinstance(raw, list) else []

    async def get_scenes(self) -> list:
        """Every Scene in the world.

        This used to run a full-text search for the word "scene", which is not
        a document-type filter — it returns anything whose text happens to
        contain that word. Against a live dnd5e world it answered with class
        features:

            query="scene"                -> Ascendant Step, Bonus Proficiencies
            filter="documentType:Scene"  -> Foundry Virtual Tabletop, test

        The relay's search takes `filter` for exactly this, documented as
        `documentType:Item` and the like.
        """
        try:
            raw_data = await self._search_documents("Scene")
            scenes = []
            if True:
                for entry in raw_data:
                    scenes.append({
                        "name": entry.get("name", entry.get("title", "Unknown")),
                        "uuid": entry.get("uuid", entry.get("id", "")),
                        "token_count": entry.get("tokenCount", entry.get("tokens", {})),
                        "active": entry.get("active", False),
                    })
            return scenes
        except Exception as e:
            logger.error(f"Failed to get scenes: {e}", exc_info=True)
            return []

    async def get_scene_details(self, scene_name: str = None) -> dict:
        try:
            if not scene_name:
                # The relay's get-scene has NO default — omitting `name` returns
                # {"error": "Scene not found", "data": None}, it does not fall
                # back to the active/viewed scene. Every caller in this codebase
                # that omits scene_name means "the current scene", so resolve it
                # here rather than silently returning empty to over a dozen call
                # sites (combat loop, chat listener, place_token's dedup check,
                # reinforcement manager, ...). This was the root cause of
                # place_token's "already on scene" dedup never firing — it reads
                # get_scene_tokens() with no name and always saw [].
                scene_name = await self._get_active_scene_name()
                if not scene_name:
                    logger.warning("get_scene_details: no active/viewed scene to default to")
                    return {}
            routed = await self._cli_read(world_cli_reads.scene_details, scene_name)
            if routed is not None:
                return routed
            return await self._send("get-scene", name=scene_name)
        except Exception as e:
            logger.error(f"Failed to get scene details: {e}", exc_info=True)
            return {}

    async def _get_active_scene_name(self) -> Optional[str]:
        """Resolve the current scene's name for get-scene calls that omit one."""
        routed = await self._cli_read(world_cli_reads.active_scene)
        if routed is not None:
            return routed["name"]
        try:
            res = await self.execute_js(
                "return game.scenes.active?.name ?? game.scenes.viewed?.name ?? null;"
            )
            name = res.get("result") if isinstance(res, dict) else None
            return name if isinstance(name, str) and name else None
        except Exception as e:
            logger.debug(f"_get_active_scene_name failed: {e}")
            return None

    async def get_active_scene_id(self) -> Optional[str]:
        """The id of the scene canvas operations act on (the GM client's viewed scene), or None."""
        routed = await self._cli_read(world_cli_reads.active_scene)
        if routed is not None:
            return routed["id"]
        try:
            res = await self.execute_js(
                "return canvas?.scene?.id ?? game.scenes.viewed?.id ?? game.scenes.active?.id ?? null;"
            )
            scene_id = res.get("result") if isinstance(res, dict) else None
            return scene_id if isinstance(scene_id, str) and scene_id else None
        except Exception as e:
            logger.debug(f"get_active_scene_id failed: {e}")
            return None

    async def list_scene_names(self) -> list:
        """Return the names of all scenes in the world (the switch_scene menu)."""
        routed = await self._cli_read(world_cli_reads.scene_names)
        if routed is not None:
            return [n for n in routed if n]
        try:
            res = await self.execute_js("return game.scenes.map(s=>s.name);")
            names = res.get("result") if isinstance(res, dict) else None
            return [n for n in names if n] if isinstance(names, list) else []
        except Exception as e:
            logger.debug(f"list_scene_names failed: {e}")
            return []

    async def get_scene_tokens(self, scene_name: str = None) -> list:
        try:
            token_scene = scene_name or await self._get_active_scene_name()
            routed = await self._cli_read(world_cli_reads.scene_tokens, token_scene) if token_scene else None
            if routed is not None:
                return routed
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
                    # Foundry TokenDocuments key the actor as "actorId"; include
                    # it so token<->actor resolution (move_token, place_token
                    # dedup) actually matches instead of seeing an empty uuid.
                    "actorUuid": t.get("actorUuid") or t.get("actorId") or t.get("actor_id") or "",
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
            logger.error(f"Failed to get scene tokens: {e}", exc_info=True)
            return []

    async def set_active_scene(self, scene_name: str) -> dict:
        # Resolve the name inside Foundry (sees ALL scenes, tolerant of a
        # missing leading "The", case, and partial matches) then activate it.
        # The LLM often drops articles (e.g. "Summit Gatehouse" vs
        # "The Summit Gatehouse"), which the strict relay lookup rejects.
        # A scene switch triggers a full canvas redraw, so use the canvas timeout.
        if self._writes_via_cli():
            match = await self._cli_read(world_cli_reads.scene_match, scene_name)
            if match:
                tokens = await self._cli_read(world_cli_reads.pc_token_data, match["id"]) or []
                routed = await self._cli_write(
                    world_cli_writes.scene_activate(match["id"], match["name"], tokens), same_key_retry=True)
                if routed is not None:
                    if match["name"] != scene_name:
                        logger.info(f"set_active_scene: resolved '{scene_name}' -> '{match['name']}'")
                    return routed
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
        if uuid and not token_id:
            routed = await self._cli_write(world_cli_writes.entity_update(uuid, data))
            if routed is not None:
                return routed
        kwargs = {}
        if uuid:
            kwargs["uuid"] = uuid
        if data:
            kwargs["data"] = data
        if token_id:
            kwargs["token_id"] = token_id
        return await self._send("update", **kwargs)

    # The module checks `data.uuid` (falling back to the GM's selected token);
    # `actorUuid` is not read, so these answered "UUID or selected is required".
    async def decrease_attribute(self, attribute_path: str, amount: int, actor_uuid: str) -> dict:
        return await self._send("decrease", attribute=attribute_path, amount=amount, uuid=actor_uuid)

    async def increase_attribute(self, attribute_path: str, amount: int, actor_uuid: str) -> dict:
        return await self._send("increase", attribute=attribute_path, amount=amount, uuid=actor_uuid)

    async def play_sound(self, src: str, volume: float = 0.5) -> dict:
        # The relay's play-sound plays an audio file by `src` (a Foundry asset
        # path), NOT by a semantic name — passing `name` is silently ignored and
        # the handler throws "src is required". Callers must resolve a real path
        # (see execute_play_sound, which matches against get_playlists sounds).
        return await self._send("play-sound", src=src, volume=volume)

    async def get_playlists(self) -> list:
        """Return the world's playlists with their sounds (each sound has a real
        `path` usable as a play-sound `src`)."""
        try:
            routed = await self._cli_read(world_cli_reads.playlists)
            if routed is not None:
                return routed
            res = await self._send("get-playlists")
            data = res.get("data") if isinstance(res, dict) else None
            playlists = (data or {}).get("playlists") if isinstance(data, dict) else None
            return playlists if isinstance(playlists, list) else []
        except Exception as e:
            logger.error(f"Failed to get playlists: {e}", exc_info=True)
            return []

    async def play_playlist(self, playlist_name: str, volume: float = 0.5) -> dict:
        """Start a playlist at `volume`.

        The relay's type is "playlist-play" and its parameter is
        `playlistName` — "play-playlist" came back "Unknown message type", so
        every play_music failed. It takes no volume, so the level is applied
        separately, to the sounds that started: a Playlist document has no
        `volume` field, so a playlist-level "playlist-volume" is silently
        dropped by Foundry and a track would start at whatever level it was
        last left at. The sound's stored volume is overwritten with the level.
        """
        result = await self._send("playlist-play", playlistName=playlist_name)
        playlist = ((result or {}).get("data") or {}).get("playlist") or {}
        playing = [s for s in playlist.get("sounds") or [] if s.get("playing") and s.get("id")]
        if not playing:
            logger.warning(f"Playlist '{playlist_name}' started but no playing sound was reported; volume not applied")
        for sound in playing:
            try:
                await self._send("playlist-volume", playlistName=playlist_name, soundId=sound["id"], volume=volume)
            except Exception as e:
                logger.warning(f"Playlist '{playlist_name}' is playing but volume was not applied to '{sound.get('name')}': {e}")
        return result

    async def roll_initiative(self) -> dict:
        # The relay has no "roll-initiative" message type — initiative is rolled
        # via start-encounter's rollAll, or directly in Foundry. Use execute-js
        # so a standalone call still works instead of erroring "Unknown message
        # type".
        if self._writes_via_cli():
            combat_id = await self._cli_read(world_cli_reads.active_combat_id)
            if combat_id:
                routed = await self._cli_write(world_cli_writes.combat_roll_initiative(combat_id))
                if routed is not None:
                    return routed
        return await self.execute_js(
            "return (await game.combat?.rollAll?.()) ? 'ok' : 'no-combat';"
        )

    async def start_encounter(self, tokens: list = None, roll_all: bool = False, name: str = None) -> dict:
        # roll_all maps to the relay's rollAll param so initiative is rolled as
        # part of starting the encounter (the relay supports no separate
        # roll-initiative message). name is the relay's optional encounter name
        # (shown in Foundry's combat tracker); omitted entirely when not set.
        #
        # The relay's `tokens` param does NOT reliably add combatants — verified
        # empirically: passing bare token ids or full "Scene.x.Token.y" document
        # uuids both started an empty combat (0 combatants) regardless of any
        # delay after placement. The path that actually works is Foundry's own
        # combat-tracker behavior: select the tokens on canvas, then start with
        # startWithSelected. So when token ids are given, we select them here
        # first; callers keep passing plain token ids, this is an internal detail.
        if self._writes_via_cli():
            scene_id = await self.get_active_scene_id()
            rows = await self._cli_read(world_cli_reads.token_rows, scene_id) if scene_id and tokens else []
            if scene_id and rows is not None:
                known = {r.get("id") for r in rows}
                found = [t for t in tokens or [] if t in known]
                for t in tokens or []:
                    if t not in known:
                        logger.warning(f"start_encounter: token {t} is not on scene {scene_id}; skipping it")
                # Starting with combatants whose initiative is unrolled makes monks-combat-details open a dialog
                # that blocks a headless session, so only the roll-first sequence (or an empty combat) goes here.
                if roll_all or not found:
                    routed = await self._cli_write(world_cli_writes.combat_start(scene_id, found, roll_all, name))
                    if routed is not None:
                        return routed
        if tokens:
            select_js = (
                f"const ids={json.dumps(tokens)};"
                "let first=true, n=0;"
                "for (const id of ids) {"
                "  const t = canvas.tokens?.get(id);"
                "  if (t) { t.control({releaseOthers: first}); first = false; n++; }"
                "}"
                "return n;"
            )
            try:
                sel_res = await self.execute_js(select_js)
                n_selected = sel_res.get("result") if isinstance(sel_res, dict) else None
            except Exception as e:
                logger.warning(f"start_encounter: token selection failed: {e}")
                n_selected = None
            if not n_selected:
                logger.warning(
                    f"start_encounter: none of {len(tokens)} token id(s) resolved on "
                    f"canvas — combat will likely start with 0 combatants"
                )
            params = {"startWithSelected": True, "rollAll": roll_all}
        else:
            params = {"tokens": [], "rollAll": roll_all}
        if name:
            params["name"] = name
        return await self._send("start-encounter", **params)

    async def end_encounter(self) -> dict:
        if self._writes_via_cli():
            combat_id = await self._cli_read(world_cli_reads.active_combat_id)
            if combat_id:
                routed = await self._cli_write(world_cli_writes.combat_end(combat_id))
                if routed is not None:
                    return routed
        return await self._send("end-encounter")

    async def use_spell_slot(self, actor_uuid: str, spell_level) -> dict:
        """Consume one spell slot. Returns {ok, used, remaining}.

        There is no "use-spell-slot" relay message type — sending one got
        {"type":"error","error":"Unknown message type"} back, which _send
        raises on, so every non-ritual cast_spell failed. Slot writes go
        through execute-js, the same path scripts.get_spell_slots reads.
        """
        from foundry import scripts
        res = await self.execute_js(scripts.spend_spell_slot(actor_uuid, spell_level))
        return (res.get("result") or {}) if isinstance(res, dict) else {}

    # D&D 5e skill name -> the abbreviation the relay's skill-check expects.
    _SKILL_ABBR = {
        "acrobatics": "acr", "animal handling": "ani", "arcana": "arc",
        "athletics": "ath", "deception": "dec", "history": "his",
        "insight": "ins", "intimidation": "itm", "investigation": "inv",
        "medicine": "med", "nature": "nat", "perception": "prc",
        "performance": "prf", "persuasion": "per", "religion": "rel",
        "sleight of hand": "slt", "stealth": "ste", "survival": "sur",
    }

    async def request_skill_check(
        self, actor_uuid: str, skill: str, dc: int = None,
        reason: str = None, advantage: bool = None
    ) -> dict:
        # Relay message type is "skill-check" (not "request-skill-check"), keyed
        # by camelCase actorUuid + a skill abbreviation. dc is engine-side only
        # (the relay just rolls; the executor compares to the DC).
        # rules/database.py spells these with underscores and _SKILL_ABBR with
        # spaces, and the model has seen both. An unmapped name is passed to
        # the relay verbatim, so "animal_handling" reached it as a skill that
        # does not exist. Normalise before looking up.
        normalised = (skill or "").strip().lower().replace("_", " ").replace("-", " ")
        skill_code = self._SKILL_ABBR.get(normalised, skill)
        return await self._send(
            "skill-check",
            actorUuid=actor_uuid,
            skill=skill_code,
        )

    # D&D 5e ability name -> the abbreviation the relay's ability-save expects.
    _ABILITY_ABBR = {
        "strength": "str", "dexterity": "dex", "constitution": "con",
        "intelligence": "int", "wisdom": "wis", "charisma": "cha",
        "str": "str", "dex": "dex", "con": "con", "int": "int", "wis": "wis", "cha": "cha",
    }

    async def request_saving_throw(
        self, actor_uuid: str, ability: str, dc: int = None,
        advantage: bool = None
    ) -> dict:
        # Mirrors request_skill_check: relay message type is "ability-save",
        # keyed by camelCase actorUuid + an ability abbreviation. dc is
        # engine-side only (the relay just rolls; the executor compares to it).
        ability_code = self._ABILITY_ABBR.get((ability or "").strip().lower(), ability)
        params = {"actorUuid": actor_uuid, "ability": ability_code}
        if advantage is True:
            params["advantage"] = True
        elif advantage is False:
            params["disadvantage"] = True
        return await self._send("ability-save", **params)

    async def request_death_save(self, actor_uuid: str, advantage: bool = None) -> dict:
        # Relay message type "death-save" — the relay/dnd5e system owns the
        # actual DC-10 CON save, 3-success/failure tracking, nat-20 heal,
        # and nat-1 double-failure; this just triggers one roll of it.
        params = {"actorUuid": actor_uuid}
        if advantage is True:
            params["advantage"] = True
        elif advantage is False:
            params["disadvantage"] = True
        return await self._send("death-save", **params)

    async def request_short_rest(self, actor_uuid: str, auto_hd: bool = True) -> dict:
        # Relay's dnd5e system short-rest workflow (hit dice, class feature
        # resets — e.g. Warlock Pact Magic — handled by the real system, not
        # reimplemented here). autoHD avoids a hit-dice-spend dialog that
        # would otherwise hang a headless session waiting for a click.
        return await self._send("short-rest", actorUuid=actor_uuid, autoHD=auto_hd)

    async def request_long_rest(self, actor_uuid: str, new_day: bool = True) -> dict:
        # Full HP, spell slots, hit dice, and feature resets via the real
        # dnd5e system workflow.
        return await self._send("long-rest", actorUuid=actor_uuid, newDay=new_day)

    async def apply_solo_death_setback(self, actor_uuid: str) -> dict:
        """Recover a solo PC from death with HP 1 and exhaustion."""
        from foundry import scripts
        result = await self.execute_js(scripts.solo_death_setback(actor_uuid))
        payload = result.get("result") if isinstance(result, dict) else result
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            raise RuntimeError((payload or {}).get("error", "solo death setback failed"))
        await self.add_effect(actor_uuid, "exhaustion")
        return {**payload, "exhaustion": True}

    async def break_concentration(self, actor_uuid: str) -> dict:
        return await self._send("break-concentration", actorUuid=actor_uuid)

    async def apply_condition(
        self, actor_uuid: str, condition: str, duration: str = None
    ) -> dict:
        # add-effect with the condition as statusId (maps to the dnd5e system's
        # status effects); "apply-condition" is not in the relay's allowlist.
        # Delegated rather than duplicated — the copy of this payload here kept
        # its own spelling of the uuid parameter and went on failing after
        # add_effect was fixed.
        return await self.add_effect(actor_uuid, condition.lower(), duration)

    async def add_effect(
        self, actor_uuid: str, status_id: str, duration: str = None
    ) -> dict:
        """Add a status effect or condition to an actor via statusId.

        The REST module reads `data.uuid`. This sent `actor_uuid`, so every
        call came back "uuid is required" — verified against a live world,
        where add-effect, remove-effect and therefore apply_condition all
        failed, meaning the GM could not apply a condition at all.
        """
        payload = {
            "uuid": actor_uuid,
            "statusId": status_id,
        }
        if duration:
            payload["duration"] = duration
        return await self._send("add-effect", **payload)

    async def remove_effect(self, actor_uuid: str, status_id: str) -> dict:
        """Remove a status effect or condition from an actor."""
        parts = (actor_uuid or "").split(".")
        if len(parts) == 2 and parts[0] == "Actor" and parts[1] and self._writes_via_cli():
            effect_id = await self._cli_read(world_cli_reads.effect_for_status, parts[1], status_id)
            # No such effect: the relay raises the "Status ... not found" error, so behavior is unchanged.
            if effect_id:
                routed = await self._cli_write(world_cli_writes.effect_remove(parts[1], effect_id))
                if routed is not None:
                    return routed
        return await self._send(
            "remove-effect",
            uuid=actor_uuid,
            statusId=status_id,
        )

    async def get_passive_perception(self, actor_uuid: str) -> dict:
        """Get a creature's passive perception score."""
        from foundry import scripts
        try:
            res = await self.execute_js(scripts.get_passive_perception(actor_uuid))
            return res.get("result") if isinstance(res, dict) else {"passivePerception": 10}
        except Exception as e:
            logger.debug(f"[Passive Perception] Failed to calculate: {e}")
            return {"passivePerception": 10}

    async def contested_check(self, initiator_uuid: str, target_uuid: str,
                               initiator_ability: str = "str", target_ability: str = "str") -> dict:
        """Perform a contested ability check (grapple, shove, etc.)."""
        from foundry import scripts
        try:
            res = await self.execute_js(scripts.contested_check(initiator_uuid, target_uuid, initiator_ability, target_ability))
            return res.get("result") if isinstance(res, dict) else {"error": "No result"}
        except Exception as e:
            logger.debug(f"[Contested Check] Failed: {e}")
            return {"error": str(e)}

    async def check_spell_ritual(self, actor_uuid: str, spell_name: str) -> dict:
        """Check if a spell can be cast as a ritual (no slot consumption)."""
        from foundry import scripts
        try:
            res = await self.execute_js(scripts.check_spell_ritual(actor_uuid, spell_name))
            return res.get("result") if isinstance(res, dict) else {"isRitual": False}
        except Exception:
            return {"isRitual": False}

    async def get_spell_slots(self, actor_uuid: str) -> dict:
        """Read-only spell-slot snapshot off the actor's dnd5e sheet —
        used by RefereeAgent to check cast_spell legality before approving,
        not to consume a slot (that's use_spell_slot). Reuses the existing
        scripts.get_spell_slots (already used by combat/loop.py), which
        returns {"1": {value, max}, ..., "pact": {value, max, casterLevel}}
        — only levels with max > 0 are present; {} means no spellcasting
        (or actor not found)."""
        from foundry import scripts
        try:
            res = await self.execute_js(scripts.get_spell_slots(actor_uuid))
            return res.get("result") if isinstance(res, dict) else {}
        except Exception:
            return {}

    async def get_multiattack_count(self, actor_uuid: str) -> dict:
        """Get multiattack count for an NPC, if they have multiattack."""
        from foundry import scripts
        try:
            res = await self.execute_js(scripts.get_multiattack_count(actor_uuid))
            return res.get("result") if isinstance(res, dict) else {"count": 1, "description": ""}
        except Exception:
            return {"count": 1, "description": ""}

    async def get_legendary_resistance(self, actor_uuid: str) -> dict:
        """Get current/max legendary resistance uses for an actor."""
        from foundry import scripts
        try:
            res = await self.execute_js(scripts.get_legendary_resistance_resource(actor_uuid))
            return res.get("result") if isinstance(res, dict) else {"value": 0, "max": 0}
        except Exception:
            return {"value": 0, "max": 0}

    async def spend_legendary_resistance(self, actor_uuid: str) -> dict:
        """Spend one use of legendary resistance. Returns {ok, used, remaining}."""
        from foundry import scripts
        try:
            res = await self.execute_js(scripts.spend_legendary_resistance(actor_uuid))
            return res.get("result") if isinstance(res, dict) else {"ok": False, "used": False}
        except Exception:
            return {"ok": False, "used": False}

    async def get_users(self) -> list:
        try:
            routed = await self._cli_read(world_cli_reads.users)
            if routed is not None:
                return routed
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
            logger.error(f"Failed to get users: {e}", exc_info=True)
            return []

    async def get_rooms(self) -> list:
        """Get world structure (maps to relay 'structure' request)."""
        try:
            result = await self._send("structure")
            rooms = result.get("rooms", result.get("data", {}).get("rooms", []))
            return rooms if isinstance(rooms, list) else []
        except Exception as e:
            logger.error(f"Failed to get rooms: {e}", exc_info=True)
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
            # 1. World metadata. get_structure() returns the FOLDER tree —
            # {"data": {"folders": {...}}} — and never a "world" key, so
            # reading it here always produced name "Unknown", no version and
            # no systems against a real world.
            scan_result["world"] = await self._world_metadata()

            # 2. Scenes — via get_scenes(), which filters on documentType.
            # This used to read structure["scenes"], another key the folder
            # tree does not have, so the scan reported no scenes at all.
            scan_result["scenes"] = await self.get_scenes()

            # 3. Actors (NPCs, monsters)
            actors_result = await self.get_actors()
            scan_result["actors"] = actors_result

            # 4. Items
            try:
                items_raw = await self._search_documents("Item")
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
                journal_raw = await self._search_documents("JournalEntry")
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
                encounters_raw = await self._search_documents("Combat")
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
            logger.error(f"World scan failed: {e}", exc_info=True)
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

    def _truncate_context(self, label: str, context: str) -> str:
        limit = settings.context_max_chars
        if len(context) > limit:
            logger.warning(f"{label} context truncated from {len(context)} to {limit} chars")
            return context[:limit]
        return context

    async def set_npc_context(self, context: str):
        self._npc_context = self._truncate_context("NPC", context)
        logger.info(f"NPC context updated ({len(self._npc_context)} chars)")

    async def set_world_context(self, context: str):
        self._world_context = self._truncate_context("World", context)
        logger.info(f"World context updated ({len(self._world_context)} chars)")

    async def set_ai_tone(self, tone: str):
        self._ai_tone = tone
        logger.info(f"AI tone updated: {tone[:50]}...")

    async def get_world_info(self) -> ActiveModulesInfo:
        """Get active module list ({"modules": [{id, title, version, active}]}).

        Reads game.modules directly via execute_js rather than the relay's
        'world-info' RPC, which was found to always report zero modules
        regardless of how many are actually active — silently disabling
        every addon-integration check that reads this (deploy_to_foundry's
        scan, combat/loop's per-combat module detection). Both current
        callers only use the "modules" field, so that's all this returns.
        """
        return await self.get_active_modules_info()

    async def get_active_modules_info(self, include_world: bool = False) -> dict:
        """Get active modules, optionally along with current world identity.

        This centralizes the costly module scan so startup and campaign-loading
        paths can share one cached call instead of duplicating scan_world plus
        get_active_modules work.
        """
        try:
            routed = await self._cli_read(world_cli_reads.active_modules, include_world)
            if routed is not None:
                return routed
            from foundry import scripts
            res = await self.execute_js(scripts.get_active_modules())
            modules = res.get("result") if isinstance(res, dict) else None
            payload = {"modules": modules if isinstance(modules, list) else []}
            if include_world:
                world_res = await self.execute_js(
                    "return {title: game.world?.title ?? '', id: game.world?.id ?? ''};"
                )
                world = world_res.get("result") if isinstance(world_res, dict) else None
                payload["world"] = world if isinstance(world, dict) else {}
            return payload
        except Exception as e:
            logger.error(f"Failed to get active modules info: {e}", exc_info=True)
            return {"modules": []}

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
        "regions": "Region",
    }

    async def canvas_create(self, doc_type: str, data: Any) -> dict:
        """Create canvas embedded documents.

        doc_type: 'walls', 'lights', 'sounds', 'tokens', 'tiles',
                  'drawings', 'notes', 'regions'
        data: list of document dicts, or a single dict (auto-wrapped)
        """
        if isinstance(data, dict):
            data = [data]
        if self._writes_via_cli():
            scene_id = await self.get_active_scene_id()
            if scene_id:
                routed = await self._cli_write(world_cli_writes.canvas_create(scene_id, doc_type, data), same_key_retry=True)
                if routed is not None:
                    return routed
        class_name = self._CANVAS_DOC_CLASS.get(doc_type, doc_type)
        return await self._send(
            "create-canvas-document", documentType=doc_type, className=class_name, data=data,
            _timeout=settings.relay_rpc_timeout_canvas,
        )

    async def canvas_get(self, doc_type: str) -> list:
        """Get all canvas embedded documents of a given type on the active scene."""
        try:
            routed = await self._cli_read(world_cli_reads.canvas_documents, doc_type)
            if routed is not None:
                return routed
            result = await self._send("get-canvas-documents", documentType=doc_type)
            docs = result.get("data", result.get("documents", result.get("results", [])))
            return docs if isinstance(docs, list) else []
        except Exception as e:
            logger.error(f"Failed to get canvas documents ({doc_type}): {e}", exc_info=True)
            return []

    async def canvas_update(self, doc_type: str, updates: dict, uuid: str = None) -> dict:
        """Update a canvas embedded document."""
        parts = world_cli_writes.split_uuid(uuid, doc_type) if uuid and self._writes_via_cli() else None
        if parts:
            routed = await self._cli_write(world_cli_writes.canvas_update(parts[0], doc_type, parts[1], updates))
            if routed is not None:
                return routed
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
        if self._writes_via_cli():
            scene_id = await self.get_active_scene_id()
            delete_ids = ids or ([p[1] for p in [world_cli_writes.split_uuid(uuid, doc_type)] if p] if uuid else [])
            if scene_id and delete_ids:
                routed = await self._cli_write(world_cli_writes.canvas_delete(scene_id, doc_type, delete_ids))
                if routed is not None:
                    return routed
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
        """Execute JavaScript in the connected Foundry world.

        Requires the execute:js scope on the API key. Used for operations the
        relay exposes no structured endpoint for. _timeout overrides the default
        reply timeout (e.g. canvas ops pass a longer value).

        ALLOW_EXECUTE_JS is deliberately NOT checked here. It guards *untrusted*
        JavaScript, and it is enforced at the two boundaries where untrusted
        code can enter: the LLM-driven execute_js action (actions/executors.py,
        execute_execute_js) and POST /api/foundry/js (api/routes/session.py).
        Gating this transport instead of those inputs broke the 87 internal call
        sites that pass fixed, first-party snippets: combat turn sync,
        initiative, death saves, spell slots, legendary actions, scene setup,
        campaign teardown and TTS playback all raised ValueError under the
        documented default of false. See tests/test_execute_js_gate_layer.py.
        """
        return await self._send("execute-js", script=code, _timeout=_timeout)

    async def create_entity(self, entity_type: str, data: dict) -> dict:
        """Create a Foundry document (Scene, Actor, Item, JournalEntry, etc.)"""
        routed = await self._cli_write(world_cli_writes.entity_create(entity_type, data), same_key_retry=True)
        if routed is not None:
            return routed
        return await self._send("create", entityType=entity_type, data=data)

    async def create_player_character(self, character: dict, user_id: Optional[str] = None) -> dict:
        """Create a level-one dnd5e character and assign it to a player user.

        Creation happens inside Foundry so the installed dnd5e system owns
        defaults and derived values.  Matching compendium documents supplies
        class, background, race, equipment, and spells when the world has the
        standard dnd5e packs; missing optional documents do not make a legal
        base character fail.
        """
        payload = json.dumps({
            "name": str(character.get("name") or "Adventurer")[:60],
            "class": str(character.get("class") or "fighter").lower(),
            "race": str(character.get("race") or "human").lower(),
            "background": str(character.get("background") or "folk-hero").lower(),
            "concept": str(character.get("concept") or "")[:1000],
            "userId": user_id or "",
        })
        js = f"""
const spec = {payload};
const systemId = game.system?.id;
if (systemId !== 'dnd5e') return {{ok:false,error:'A dnd5e world is required',system:systemId||null}};
const users = game.users.filter(u => u.role < 4);
const assignedActor = u => u?.character || [...game.actors].find(a => a.type === 'character' && (a.ownership?.[u.id] ?? 0) >= 3);
const user = spec.userId ? game.users.get(spec.userId) : users.find(u => assignedActor(u)) || users.find(u => !u.character) || users[0];
if (!user) return {{ok:false,error:'No player user is available'}};
// Never replace a character supplied by another importer (for example the
// D&D Beyond importer).  User.character is the authoritative Foundry mapping;
// leave the imported actor and its permissions untouched.
const assigned = assignedActor(user);
if (assigned) return {{ok:true,created:false,imported:true,uuid:assigned.uuid,actorId:assigned.id,name:assigned.name,userId:user.id}};
const existing = [...game.actors].find(a => a.type === 'character' && a.name === spec.name && a.flags?.['ai-gm']?.player_character);
if (existing) {{
  await existing.update({{ownership: {{[user.id]: 3}}}});
  await user.update({{character: existing.id}});
  return {{ok:true,created:false,uuid:existing.uuid,actorId:existing.id,name:existing.name,userId:user.id}};
}}
const actor = await Actor.create({{
  name: spec.name, type: 'character',
  system: {{details: {{level: 1}}, abilities: {{}}}},
  flags: {{'ai-gm': {{player_character:true, concept:spec.concept, class:spec.class}}}},
  ownership: {{[user.id]: 3}}
}});
if (!actor) return {{ok:false,error:'Foundry did not create the character'}};
// Resolve standard dnd5e compendium documents by name.  Packs differ between
// dnd5e releases, so this is additive and the actor itself remains valid.
const equipment = spec.class === 'wizard' ? ['spellbook','dagger','component pouch','scholar']
  : spec.class === 'rogue' ? ['shortsword','shortbow','leather','thieves tools','explorer']
  : spec.class === 'cleric' ? ['mace','shield','chain mail','holy symbol']
  : ['longsword','shield','chain mail','explorer'];
const spells = ['wizard','sorcerer','warlock','cleric','druid','bard','paladin','ranger'].includes(spec.class)
  ? (spec.class === 'cleric' ? ['cure wounds','guiding bolt'] : ['fire bolt','magic missile']) : [];
const wanted = [spec.class, spec.race, spec.background, ...equipment, ...spells];
const docs = [];
for (const pack of game.packs) {{
  if (!['Item','Actor'].includes(pack.documentName) || !/dnd5e/i.test(pack.metadata?.id+' '+pack.metadata?.label)) continue;
  let index; try {{ index = await pack.getIndex(); }} catch (_) {{ continue; }}
  for (const term of wanted) {{
    const normalized = term.replaceAll('-',' ').toLowerCase();
    const row = index.find(i => String(i.name).toLowerCase() === normalized)
      || index.find(i => normalized.length > 5 && String(i.name).toLowerCase().includes(normalized));
    if (row && pack.documentName === 'Item' && !docs.some(d => d.name === row.name)) {{
      try {{ const doc=(await pack.getDocument(row._id)).toObject(); delete doc._id; docs.push(doc); }} catch (_) {{}}
    }}
  }}
}}
if (docs.length) await actor.createEmbeddedDocuments('Item', docs);
await user.update({{character: actor.id}});
return {{ok:true,created:true,uuid:actor.uuid,actorId:actor.id,name:actor.name,userId:user.id,items:docs.map(i=>i.name)}};
"""
        try:
            response = await self.execute_js(js)
            result = response.get("result") if isinstance(response, dict) else None
            return result if isinstance(result, dict) else {"ok": False, "error": "Invalid Foundry response"}
        except Exception as exc:
            logger.error("Failed to create player character: %s", exc, exc_info=True)
            return {"ok": False, "error": str(exc)}

    async def move_token(self, token_id: str, x: float, y: float) -> dict:
        """Move a token to absolute pixel coordinates on the active scene.

        Resolves the target INSIDE Foundry (authoritative) — the LLM passes a
        token id, an actor uuid ('Actor.xxx'), an actor id, or a display name.
        This avoids the relay's strict token-id lookup, which fails ('Entity not
        found') whenever the LLM hands us anything but the exact scene token id.
        """
        if self._writes_via_cli():
            scene_id = await self.get_active_scene_id()
            rows = await self._cli_read(world_cli_reads.token_rows, scene_id) if scene_id else None
            row = world_cli_writes.resolve_token(rows, token_id) if rows else None
            if row:
                routed = await self._cli_write(world_cli_writes.token_move(scene_id, row, x, y))
                if routed is not None:
                    return routed
        want = json.dumps(str(token_id))
        js = (
            f"const want={want};const wl=want.toLowerCase();const short=wl.split('.').pop();"
            "const s=canvas?.scene;if(!s)return{ok:false,error:'no active scene'};"
            "let tok=s.tokens.find(t=>t.id===want)"
            "||s.tokens.find(t=>t.actorId&&(t.actorId.toLowerCase()===short||('actor.'+t.actorId.toLowerCase())===wl))"
            "||s.tokens.find(t=>t.actor?.uuid&&t.actor.uuid.toLowerCase()===wl)"
            "||s.tokens.find(t=>t.name&&t.name.toLowerCase()===wl);"
            "if(!tok)return{ok:false,error:'token not found',tried:want};"
            "const upd={x:" + repr(float(x)) + ",y:" + repr(float(y)) + "};"
            # Levels module requires a valid 'level' reference on token update —
            # without it, tok.update() throws "level must exist" and the move
            # silently fails (caught below). Mirror the level the token is
            # already on if the scene has any, same as token creation does.
            "const lvls=s.levels;if(lvls?.length)upd.level=tok.level??lvls[0]._id;"
            # v14 vetoes a move it will not allow (scene bounds, walls) by leaving
            # the token where it was, and tok.update() still resolves. _source is
            # the committed position (the live x/y animates), so compare that —
            # otherwise the GM narrates a move that never happened.
            "const ox=tok._source.x,oy=tok._source.y;"
            "await tok.update(upd);"
            "const moved=tok._source.x!==ox||tok._source.y!==oy;"
            "return moved?{ok:true,id:tok.id,name:tok.name,x:tok._source.x,y:tok._source.y,fromX:ox,fromY:oy}"
            ":{ok:false,id:tok.id,name:tok.name,error:'Foundry did not move the token (blocked by scene bounds or walls)'};"
        )
        try:
            res = await self.execute_js(js)
            r = res.get("result") if isinstance(res, dict) else None
            if isinstance(r, dict) and r.get("ok"):
                return r
            # r=None means the JS itself errored (no "result" in the reply) —
            # log the raw reply so the actual Foundry-side error is visible.
            logger.warning(f"move_token: could not resolve {token_id!r} on scene ({r if r is not None else res})")
            return r if isinstance(r, dict) else {"ok": False, "error": "move failed"}
        except Exception as e:
            logger.warning(f"move_token via execute-js failed ({e}); falling back to relay move-token")
            return await self._send("move-token", tokenId=token_id, x=x, y=y)

    async def place_token(
        self,
        actor_name: str = None,
        x: float = 0,
        y: float = 0,
        disposition: int = 0,
        hidden: bool = False,
        uuid: str = None,
    ) -> dict:
        """Place an actor's token on the current scene at (x, y) pixels.

        Resolves the actor by uuid (preferred, the LLM usually has it) or by
        name, then creates a Token canvas document.
        disposition: -1=hostile, 0=neutral, 1=friendly
        """
        actors = await self.get_actors(world_only=True)
        actor = None
        ident = (uuid or "").strip()
        if ident:
            short = ident.split(".")[-1].lower()
            actor = next(
                (a for a in actors
                 if str(a.get("uuid", "")).lower() == ident.lower()
                 or str(a.get("uuid", "")).split(".")[-1].lower() == short),
                None,
            )
        if not actor and actor_name:
            actor = next(
                (a for a in actors if a.get("name", "").lower() == actor_name.lower()),
                None,
            )
        if not actor:
            target = actor_name or uuid
            logger.warning(f"place_token: actor '{target}' not found in world actors")
            return {"error": f"Actor '{target}' not found"}
        # Normalize the display name from the resolved actor (uuid path has none).
        actor_name = actor.get("name", actor_name or "Token")

        # Don't spawn a duplicate: if this actor already has a token on the active
        # scene, move that token to the requested spot instead of creating another
        # copy (the repeated "another copy of my character" seen in play).
        actor_uuid_full = actor.get("uuid", "")
        actor_short = actor_uuid_full.split(".")[-1]
        try:
            for t in await self.get_scene_tokens():
                au = str(t.get("actorUuid", ""))
                tid = t.get("id")
                if tid and au and (au == actor_uuid_full or au.split(".")[-1] == actor_short):
                    logger.info(
                        f"place_token: '{actor_name}' already on scene — moving "
                        f"existing token {tid} instead of duplicating"
                    )
                    move_result = await self.move_token(tid, x, y)
                    moved_ok = bool(move_result.get("ok") or move_result.get("success"))
                    if not moved_ok:
                        err = move_result.get("error", "unknown error")
                        logger.warning(
                            f"place_token: failed to move '{actor_name}' token {tid}: {err}"
                        )
                        return {"error": f"Failed to move '{actor_name}' token: {err}", "token_id": tid, "actor": actor_name}
                    return {"moved": True, "token_id": tid, "actor": actor_name}
        except Exception as e:
            logger.debug(f"place_token dedup check failed (creating anyway): {e}")

        # Resolve the active Levels module level so the token appears on the
        # correct layer. Without this, Levels assigns whatever level was last
        # active in the headless browser, which is usually wrong.
        level_id = None
        try:
            js = "const lvls=canvas?.scene?.levels; return lvls?.length ? lvls[0]._id : null"
            lres = await self.execute_js(js)
            level_id = lres.get("result") if isinstance(lres, dict) else None
        except Exception:
            logger.warning("[Scene] Level lookup failed; scene will be treated as single-level", exc_info=True)

        # Pull the prototype token's texture so the token shows the actor's image
        proto_img = actor.get("img") or "icons/svg/mystery-man.svg"
        routed_img = await self._cli_read(world_cli_reads.actor_image, actor.get("uuid", "").split(".")[-1])
        if routed_img:
            proto_img = routed_img
        else:
            try:
                js_proto = f"const a=game.actors.get({json.dumps(actor.get('uuid','').split('.')[-1])}); return a?.prototypeToken?.texture?.src || a?.img || null"
                pres = await self.execute_js(js_proto)
                proto_img = (pres.get("result") or proto_img) if isinstance(pres, dict) else proto_img
            except Exception:
                logger.warning("[Scene] Prototype image lookup failed; falling back to the default token art", exc_info=True)

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
            "texture": {"src": proto_img},
        }
        if level_id:
            token_data["level"] = level_id

        # Player-owned tokens need vision enabled so the player doesn't see
        # a black screen when tokenVision is on. Default to 60ft darkvision
        # range which the actor's own sheet will override if configured.
        if actor.get("has_player_owner"):
            token_data["sight"] = {
                "enabled": True,
                "range": 60,
                "visionMode": "basic",
            }

        return await self.canvas_create("tokens", token_data)

    async def get_actor_dispositions(self, names: list) -> dict:
        """Return {actorName: prototypeToken.disposition} for the given names.

        Used so auto-placed combatants get their real disposition (an ally NPC
        stays friendly, a monster stays hostile) instead of a guessed value.
        """
        if not names:
            return {}
        routed = await self._cli_read(world_cli_reads.actor_dispositions, names)
        if routed is not None:
            return routed
        js = (
            f"const want={json.dumps([str(n) for n in names])}.map(s=>s.toLowerCase());"
            "const out={};"
            "for(const a of game.actors){"
            "  if(a.name && want.some(w=>a.name.toLowerCase()===w||a.name.toLowerCase().includes(w)||w.includes(a.name.toLowerCase()))){"
            "    out[a.name]=a.prototypeToken?.disposition ?? -1;"
            "  }"
            "}"
            "return out;"
        )
        try:
            res = await self.execute_js(js)
            r = res.get("result") if isinstance(res, dict) else None
            return r if isinstance(r, dict) else {}
        except Exception:
            return {}

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
        }
        # Only known layers are addressable. The previous version fell back to
        # splicing an unmapped doc_type straight into the JS source, so any
        # caller that ever passed through an LLM- or API-supplied layer name
        # would have been executing that string inside Foundry.
        foundry_type = type_map.get(doc_type)
        if not foundry_type:
            return {"error": f"Unknown canvas layer: {doc_type}", "success": False}
        if self._writes_via_cli():
            scene_id = await self.get_active_scene_id()
            existing = [world_cli_writes.doc_id(d) for d in await self.canvas_get(doc_type)] if scene_id else []
            if scene_id and not [i for i in existing if i]:
                return {"result": 0, "success": True, "via": "world-cli"}      # nothing to clear
            if scene_id:
                routed = await self._cli_write(world_cli_writes.canvas_delete(scene_id, doc_type, [i for i in existing if i]))
                if routed is not None:
                    return routed
        code = (
            f"const scene = canvas.scene;"
            f"const ids = scene[{json.dumps(doc_type)}].map(d => d.id);"
            f"if (ids.length) await scene.deleteEmbeddedDocuments({json.dumps(foundry_type)}, ids);"
            f"return ids.length;"
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
        if self._writes_via_cli():
            scene_id = await self.get_active_scene_id()
            if scene_id:
                routed = await self._cli_write(world_cli_writes.scene_update(scene_id, updates))
                if routed is not None:
                    return routed
        # Update the currently active scene via execute-js
        code = f"await canvas.scene.update({json.dumps(updates)}); return true;"
        try:
            return await self.execute_js(code, _timeout=settings.relay_rpc_timeout_canvas)
        except Exception as e:
            logger.warning(f"configure_scene via execute-js failed: {e}")
            return {"error": str(e)}

