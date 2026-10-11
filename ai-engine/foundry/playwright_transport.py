"""Playwright transport for FoundryClient: a headless GM browser run from this process.

Replaces the removed Go relay. Only the message types in HANDLERS are
implemented; anything else raises NotImplementedError so a missing port fails
loudly. Event channels cover what chat_listener consumes: chat, roll, combat,
scene, and pauseGame.
"""

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Optional

from config import settings

logger = logging.getLogger(__name__)

# Page-side ops for the message types ported from the module (user.ts, playlist.ts, scene.ts,
# canvas.ts, entity.ts, dnd5e.ts, encounter.ts, effects.ts). Each entry returns the module's payload; errors throw.
_OPS_JS = (Path(__file__).with_name("playwright_ops.js")).read_text()
_OPS_INDEX_JS = (Path(__file__).with_name("playwright_ops_index.js")).read_text()  # search, structure

# Runs a script body as an async function, the same way the relay's execute-js does,
# so callers can use top-level `return` and `await`.
_EXECUTE_JS = """async (code) => {
  const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
  return await new AsyncFunction(code)();
}"""

_CHAT_SEND = """async ({content, whisper}) => {
  const ids = whisper.map(name => {
    const user = game.users.find(u => u.name === name);
    if (!user) throw new Error('unknown whisper target: ' + name);
    return user.id;
  });
  const message = await ChatMessage.create({content, whisper: ids});
  return {id: message.id};
}"""

# Installs Foundry hooks for one event channel and emits the messages through
# window.__aigmEmit. Payload shapes follow what foundry/chat_listener.py reads and
# the module's eventChannels.ts; the combat turn is a dict because the engine reads turn.actorId.
_INSTALL_CHANNEL = """(channel) => {
  const emit = (msg) => window.__aigmEmit(JSON.stringify(msg));
  window.__aigmChannels ??= new Set();
  const channels = window.__aigmChannels;
  if (channels.has(channel)) return true;
  channels.add(channel);

  const serializeMessage = (m) => ({
    id: m.id, uuid: m.uuid, content: m.content, speaker: m.speaker, timestamp: m.timestamp,
    whisper: m.whisper || [], type: m.type,
    author: m.author ? {id: m.author.id, name: m.author.name} : null,
    flavor: m.flavor || "", isRoll: m.isRoll || false,
    rolls: (m.rolls || []).map(r => ({
      formula: r.formula, total: r.total, isCritical: !!r.isCritical, isFumble: !!r.isFumble,
      dice: (r.dice || []).map(d => ({faces: d.faces,
        results: (d.results || []).map(x => ({result: x.result, active: x.active}))})),
    })),
    flags: m.flags || {},
  });
  const turnOf = (c) => ({index: c.turn, actorId: c.combatant?.id ?? c.turns?.[c.turn]?.id ?? ""});
  const combatantsOf = (c) => (c.turns || []).map(x => ({
    id: x.id, name: x.name, initiative: x.initiative, defeated: x.defeated, uuid: x.uuid}));
  const combatEvent = (c, type) => emit({type: "combat-event", data: {
    type, eventType: type, encounterId: c.id, round: c.round, turn: turnOf(c),
    started: c.started, combatants: combatantsOf(c)}});

  if (channel === "chat-events" || channel === "roll-events") {
    if (!window.__aigmChatHooks) {
      window.__aigmChatHooks = true;
      Hooks.on("createChatMessage", (m) => {
        if (channels.has("roll-events") && m.isRoll && m.rolls?.length) {
          const r = m.rolls[0];
          const roll = {id: m.id, messageId: m.id, user: {id: m.author?.id, name: m.author?.name},
            speaker: m.speaker?.alias ?? m.author?.name ?? "Unknown", flavor: m.flavor || "",
            formula: r.formula, rollTotal: r.total, roll: r.total, total: r.total,
            isCritical: !!r.isCritical, isFumble: !!r.isFumble,
            dice: (r.dice || []).map(d => ({faces: d.faces,
              results: (d.results || []).map(x => ({result: x.result, active: x.active}))})),
            timestamp: Date.now()};
          emit({type: "roll-event", roll, data: roll});
        }
        if (channels.has("chat-events")) {
          emit({type: "chat-event", data: {eventType: "create", data: serializeMessage(m)}});
        }
      });
      Hooks.on("deleteChatMessage", (m) => {
        if (channels.has("chat-events")) {
          emit({type: "chat-event", data: {eventType: "delete", data: {id: m.id}}});
        }
      });
    }
  } else if (channel === "combat-events") {
    Hooks.on("combatStart", (c) => combatEvent(c, "start"));
    Hooks.on("updateCombat", (c, changed) => {
      if (!("turn" in changed) && !("round" in changed)) return;
      if ("started" in changed) return;
      combatEvent(c, "turn");
    });
    Hooks.on("deleteCombat", (c) => emit({type: "combat-event", data: {
      type: "end", eventType: "end", encounterId: c.id, round: c.round}}));
  } else if (channel === "scene-events") {
    const sceneEvent = (scene, eventType, extra = {}) => emit({type: "scene-event", event: eventType,
      sceneId: scene.id, data: {sceneId: scene.id, eventType, ...extra}});
    Hooks.on("createToken", (t) => sceneEvent(t.parent, "token-create"));
    Hooks.on("updateToken", (t) => sceneEvent(t.parent, "token-update"));
    Hooks.on("deleteToken", (t) => sceneEvent(t.parent, "token-delete"));
    Hooks.on("activateScene", (s) => sceneEvent(s, "scene-activate", {name: s.name}));
  } else if (channel === "hooks") {
    Hooks.on("pauseGame", (paused) => emit({type: "hook", hook: "pauseGame", data: {paused: !!paused}}));
  } else {
    channels.delete(channel);
    return false;
  }
  return true;
}"""


class PlaywrightTransport:
    # Background reconnect delays after the browser goes away, in seconds.
    _RECONNECT_DELAYS = (2, 5, 10, 30, 60)

    def __init__(self):
        self._playwright = None
        self._browser = None
        self._page = None
        self._lock = asyncio.Lock()  # serialises connect, close, and reconnects
        self._channels: set = set()  # re-installed after every reconnect
        self._closing = False
        self._reconnect_task: Optional[asyncio.Task] = None
        # Receives each event message as a dict; the client routes it like a relay push.
        self.on_event: Optional[Callable[[dict], Awaitable[None]]] = None
        # Called with True/False as the browser comes and goes, so the client's connected flag stays right.
        self.on_state: Optional[Callable[[bool], None]] = None

    @property
    def connected(self) -> bool:
        return (self._page is not None and not self._page.is_closed()
                and self._browser is not None and self._browser.is_connected())

    async def connect(self) -> None:
        """Launch Chromium and log the GM user in. No-op when already connected."""
        async with self._lock:
            if self.connected:
                return
            await self._teardown()
            if not settings.foundry_password:
                raise RuntimeError("FOUNDRY_PASSWORD is not set; the playwright backend needs the GM password in .env")
            if not settings.foundry_username:
                raise RuntimeError("FOUNDRY_USERNAME is not set; the playwright backend needs the GM username")

            try:
                from playwright.async_api import async_playwright  # lazy: relay-mode installs don't need it

                self._playwright = await async_playwright().start()
                # The full Chromium build, not chrome-headless-shell: the shell exits with code 0 about
                # 30 s after launch, even on a blank page. The SwiftShader flags stay, since the GPU
                # path also stalls canvas renders during encounters.
                self._browser = await self._playwright.chromium.launch(
                    channel="chromium", headless=True, args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
                browser = self._browser
                browser.on("disconnected", lambda *_: self._on_browser_lost(browser))
                context = await browser.new_context(viewport={"width": 1600, "height": 900})
                self._page = await context.new_page()
                # A renderer crash leaves the browser connected and the page "open", so without this
                # every later evaluate fails and nothing reconnects.
                self._page.on("crash", lambda *_: self._on_browser_lost(browser))
                await self._page.expose_function("__aigmEmit", self._on_page_event)

                base = settings.foundry_url.rstrip("/")
                await self._page.goto(f"{base}/join", wait_until="domcontentloaded")
                await self._page.wait_for_selector("input[name=username]", timeout=30000)
                await self._page.fill("input[name=username]", settings.foundry_username)
                await self._page.fill("input[name=password]", settings.foundry_password)
                await self._page.click("button[name=join]")
                await self._page.wait_for_function("window.game && game.ready === true", timeout=120000)
                await self._page.evaluate(_OPS_JS)
                await self._page.evaluate(_OPS_INDEX_JS)
                for channel in sorted(self._channels):
                    await self._page.evaluate(_INSTALL_CHANNEL, channel)
            except BaseException:
                # A login timeout or a cancelled connect (send() wraps this in wait_for) must not
                # leave Chromium running with no page attached.
                await self._teardown()
                raise
            logger.info(f"Playwright transport logged in as {settings.foundry_username}")
            self._notify(True)

    async def close(self) -> None:
        self._closing = True
        if self._reconnect_task is not None and not self._reconnect_task.done():
            self._reconnect_task.cancel()
        async with self._lock:
            await self._teardown()
        self._notify(False)

    async def _teardown(self) -> None:
        browser, playwright = self._browser, self._playwright
        self._page = self._browser = self._playwright = None
        # The old browser's disconnect event fires while we tear it down; _on_browser_lost ignores it.
        # Closing a browser that already died raises; that is expected here, so log it at debug.
        if browser is not None:
            try:
                await browser.close()
            except Exception as e:
                logger.debug(f"browser close during teardown: {e}")
        if playwright is not None:
            try:
                await playwright.stop()
            except Exception as e:
                logger.debug(f"playwright stop during teardown: {e}")

    def _on_browser_lost(self, browser) -> None:
        if browser is not self._browser or self._closing:
            return  # our own teardown, or a browser that was already replaced
        logger.warning("Playwright browser disconnected; scheduling a reconnect")
        self._page = None
        self._notify(False)
        if self._reconnect_task is None or self._reconnect_task.done():
            self._reconnect_task = asyncio.ensure_future(self._reconnect_loop())

    async def _reconnect_loop(self) -> None:
        for delay in self._RECONNECT_DELAYS:
            await asyncio.sleep(delay)
            try:
                await self.connect()
                logger.info("Playwright transport reconnected")
                return
            except Exception as e:
                logger.warning(f"Playwright reconnect failed: {e}")
        logger.error("Playwright transport gave up reconnecting; the next call will try again")

    def _notify(self, up: bool) -> None:
        if self.on_state is not None:
            self.on_state(up)

    async def enable_channel(self, channel: str) -> bool:
        """Install the Foundry hooks for an event channel, now and after any reconnect."""
        self._channels.add(channel)
        if not self.connected:
            await self.connect()
        return await self._page.evaluate(_INSTALL_CHANNEL, channel)

    async def _on_page_event(self, raw: str) -> None:
        if self.on_event is not None:
            await self.on_event(json.loads(raw))

    async def send(self, msg_type: str, **params) -> dict:
        handler: Optional[Callable[["PlaywrightTransport", dict], Awaitable[dict]]] = HANDLERS.get(msg_type)
        if handler is None:
            raise NotImplementedError(f"playwright backend has no handler for '{msg_type}' yet")
        if not self.connected:
            # Reconnect lazily. A write that was in flight when the browser died is never replayed:
            # it may already have applied, so the caller sees its error and decides.
            try:
                await self.connect()
            except Exception as e:
                raise ConnectionError(f"playwright backend could not reconnect: {e}") from e
        return await handler(self, params)

    async def _execute_js(self, params: Dict[str, Any]) -> dict:
        try:
            value = await self._page.evaluate(_EXECUTE_JS, params.get("script", ""))
        except Exception as e:
            raise RuntimeError(f"Foundry error [execute-js]: {e}") from e
        return {"result": value}

    async def _chat_send(self, params: Dict[str, Any]) -> dict:
        # Speaker is ignored, as on the relay: an empty alias marks the AI's own posts.
        try:
            return await self._page.evaluate(
                _CHAT_SEND,
                {"content": params.get("content", ""), "whisper": params.get("whisper") or []},
            )
        except Exception as e:
            raise RuntimeError(f"Foundry error [chat-send]: {e}") from e


def _page_op(msg_type: str, op: str, flat: bool = False) -> Callable[[PlaywrightTransport, dict], Awaitable[dict]]:
    """Handler for a message type implemented by an entry in window.__aigmOps.

    The module puts most results under `data`. Some replies carry their fields at the
    top level instead (`flat=True`: create, update, increase, decrease).
    """

    async def handler(self: PlaywrightTransport, params: Dict[str, Any]) -> dict:
        try:
            value = await self._page.evaluate("([op, p]) => window.__aigmOps[op](p)", [op, params])
        except Exception as e:
            raise RuntimeError(f"Foundry error [{msg_type}]: {e}") from e
        if flat:
            return {"type": f"{msg_type}-result", **value}
        return {"type": f"{msg_type}-result", "data": value}

    return handler


HANDLERS: Dict[str, Callable[[PlaywrightTransport, dict], Awaitable[dict]]] = {
    "execute-js": PlaywrightTransport._execute_js,
    "chat-send": PlaywrightTransport._chat_send,
    "get-users": _page_op("get-users", "getUsers"),
    "get-playlists": _page_op("get-playlists", "getPlaylists"),
    "playlist-play": _page_op("playlist-play", "playlistPlay"),
    "playlist-volume": _page_op("playlist-volume", "playlistVolume"),
    "play-sound": _page_op("play-sound", "playSound"),
    "roll": _page_op("roll", "roll", flat=True),
    "skill-check": _page_op("skill-check", "skillCheck", flat=True),
    "switch-scene": _page_op("switch-scene", "switchScene", flat=True),
    "create-canvas-document": _page_op("create-canvas-document", "createCanvasDocument"),
    "get-scene": _page_op("get-scene", "getScene"),
    "get-canvas-documents": _page_op("get-canvas-documents", "getCanvasDocuments"),
    "update-canvas-document": _page_op("update-canvas-document", "updateCanvasDocument"),
    "delete-canvas-document": _page_op("delete-canvas-document", "deleteCanvasDocument"),
    "move-token": _page_op("move-token", "moveToken"),
    "update-scene": _page_op("update-scene", "updateScene"),
    "create": _page_op("create", "createEntity", flat=True),
    "update": _page_op("update", "updateEntity", flat=True),
    "increase": _page_op("increase", "increaseAttribute", flat=True),
    "decrease": _page_op("decrease", "decreaseAttribute", flat=True),
    "short-rest": _page_op("short-rest", "shortRest"),
    "long-rest": _page_op("long-rest", "longRest"),
    "ability-save": _page_op("ability-save", "abilitySave"),
    "death-save": _page_op("death-save", "deathSave"),
    "break-concentration": _page_op("break-concentration", "breakConcentration"),
    "add-effect": _page_op("add-effect", "addEffect"),
    "start-encounter": _page_op("start-encounter", "startEncounter", flat=True),
    "end-encounter": _page_op("end-encounter", "endEncounter", flat=True),
    "search": _page_op("search", "search", flat=True),
    "structure": _page_op("structure", "structure"),
    "remove-effect": _page_op("remove-effect", "removeEffect"),
    "upload-file": _page_op("upload-file", "uploadFile"),
}
