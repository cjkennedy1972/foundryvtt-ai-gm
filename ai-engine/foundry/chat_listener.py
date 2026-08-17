"""
Chat Listener — subscribes to Foundry chat events and processes player messages.
Integrates with combat loop and scene awareness.
"""

import asyncio
import collections
import json
import logging
import random
import re
import time
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from foundry.client import FoundryClient
from llm.manager import LLMManager
from actions.dispatcher import ActionDispatcher
from actions.executors import _is_player_character
from referee.agent import RefereeAgent
from events.store import EventStore
from events.types import ACTION_RESOLVED, TIME_ADVANCED
from npc import persistence as npc_persistence
from npc.agent import NPCAgent
from npc.memory import NPCMemory
from llm.router import ModelRouter
from orchestrator.director import Candidate, SceneDirector
from worldclock.agent import WorldClockAgent
from campaign.prologue import describe_prologue, load_prologue_entry, present_prologue
from campaign import obsidian_sync
from context.canon import (
    approve_canon_proposal_with_vault_write,
    generate_canon_proposals,
    reject_canon_proposal_safely,
)
from state.tracker import GameStateTracker
from persistence.db import Database
from config import settings
from utils.tasks import spawn

logger = logging.getLogger(__name__)

_NUMBER_WORDS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "several": 3, "some": 3, "few": 3, "many": 5, "horde": 6, "group": 4,
    "pack": 4, "swarm": 6, "dozen": 6,
}


def _mention_count(text: str, name: str) -> int:
    """How many of `name` the narration describes; 0 if not present.

    Counts an explicit quantity word in a short window before the name
    ('two towering Revenants' -> 2, 'a horde of Skeletons' -> 6). With no
    number, a Capitalized mention counts (singular 1, plural 3) but a lowercase
    common-noun use does NOT — so 'retreats into the shadows' never spawns the
    'Shadow' monster, while 'two Shadows lunge' does.
    """
    if not name or not text:
        return 0
    for form in (name + "es", name + "s", name):  # longest first → plural wins
        for m in re.finditer(re.escape(form), text, re.IGNORECASE):
            matched = text[m.start():m.end()]
            pre = text[max(0, m.start() - 40):m.start()].split()
            for w in reversed(pre[-4:]):
                if w.lower().strip(".,!?") in _NUMBER_WORDS:
                    return _NUMBER_WORDS[w.lower().strip(".,!?")]
            if matched[:1].isupper():
                is_plural = form.lower().endswith("s") and form.lower() != name.lower()
                return 3 if is_plural else 1
        # lowercase, no number → fall through to next form / not counted
    return 0


class ChatListener:
    """Listens for player chat messages in Foundry and routes them to the AI."""

    def __init__(
        self,
        foundry: FoundryClient,
        llm: LLMManager,
        dispatcher: ActionDispatcher,
        state_tracker: GameStateTracker,
        db: Database,
        campaign_loader=None,
        combat_loop=None,
        scene_awareness=None,
        reinforcement_mgr=None,
        npc_registry=None,
        personality_engine=None,
        ambient_manager=None,
        effects_manager=None,
        vision_manager=None,
        referee=None,
        event_store=None,
        npc_llm=None,
        semantic_rag=None,
    ):
        self.foundry = foundry
        self.llm = llm
        self.dispatcher = dispatcher
        self._referee = referee or RefereeAgent(foundry=foundry)
        self._event_store = event_store or EventStore(db)
        self._world_clock = WorldClockAgent(self._event_store, npc_registry) if npc_registry else None
        self._model_router = ModelRouter(llm, npc=npc_llm)
        self._npc_memory = NPCMemory(self._event_store)
        self._scene_director = SceneDirector()
        self.state_tracker = state_tracker
        self.db = db
        self._campaign_loader = campaign_loader
        self._semantic_rag = semantic_rag
        # Maps the 1-indexed numbers shown by the last /gm canon review to
        # canon_proposals row ids, so /gm canon approve|reject <n> knows
        # which proposal <n> refers to.
        self._canon_review_ids: list = []
        self._combat_loop = combat_loop
        self._scene_awareness = scene_awareness
        self._reinforcement_mgr = reinforcement_mgr
        self._npc_registry = npc_registry
        self._personality_engine = personality_engine
        self._ambient_manager = ambient_manager
        self._effects_manager = effects_manager
        self._vision_manager = vision_manager
        self._running = False
        self._last_turn_token: Optional[str] = None
        self._running_lock = asyncio.Lock()  # Protects _running state for pause/resume
        self._ai_controlled_speakers: set = {
            settings.ai_name,
            self.foundry._ai_name if foundry and foundry._ai_name else settings.ai_name
        }
        # Recently sent message texts — used to suppress relay echoes of our own output.
        # Uses a timestamped list (not deque) for TTL-based expiry of old echoes.
        self._sent_messages_with_timestamp: list = []
        self._sent_messages_lock = asyncio.Lock()
        # Foundry users with a GM-tier role (role >= 3). Only these may issue
        # /gm commands; populated from Foundry at start and on scene change.
        self._gm_user_ids: set = set()
        self._gm_user_names: set = set()
        # Scene catalog (switch_scene menu) cache — scenes rarely change
        # mid-session, so refresh at most every 60s instead of an execute_js
        # round-trip per player turn.
        self._scene_catalog: list = []
        self._scene_catalog_at: float = 0.0
        # GM pacing state
        self._idle_timer_task: Optional[asyncio.Task] = None
        self._player_message_count: int = 0
        # Consecutive idle nudges fired with no player response since — backs
        # off the idle timeout on each one (30s, 48s, 77s, ... capped) so a
        # genuine lull gets a fast first nudge without turning into nagging if
        # the table has just stepped away. Reset to 0 the moment a player
        # message arrives.
        self._consecutive_idle_beats: int = 0
        # Monotonic time of the last proactive beat (idle OR pacing-interval),
        # so the two triggers can't fire back-to-back and double-nudge.
        self._last_proactive_beat_at: float = 0.0
        # Last few idle-nudge style choices, so consecutive nudges don't reuse
        # the same beat type (see _pick_idle_beat_style).
        self._recent_idle_styles: collections.deque = collections.deque(maxlen=2)
        # Serialises an entire narration turn (context build + LLM call + action
        # dispatch). A player turn always waits for the lock; self-initiated
        # beats (idle/pacing) skip when it is already held, so the model never
        # produces two overlapping narrations. Replaces the old _llm_in_flight
        # boolean, which could not express "a turn is already running".
        self._turn_lock: asyncio.Lock = asyncio.Lock()
        # Multi-player input batching: messages accumulate here while a
        # debounce window is open, and get flushed as one combined turn.
        self._pending_batch_inputs: list = []
        self._input_batch_task: Optional[asyncio.Task] = None
        # True once self._input_batch_task has popped its batch and
        # committed to running it — from that point on it must NEVER be
        # cancelled (a message arriving while it's merely waiting to
        # acquire self._turn_lock used to cancel it post-pop, silently
        # dropping the already-popped messages). A new message arriving
        # after commit instead gets its own fresh debounce task.
        self._batch_committed: bool = False
        # speaker -> last-message time, pruned to a rolling window — a proxy
        # for how many players are at the table right now (see
        # _track_active_speaker), without an extra Foundry round-trip.
        self._recent_speakers: dict = {}
        # Session-start prologue playback uses this to shorten dwell timing as
        # soon as a player chat arrives.
        self._prologue_interrupt_event: Optional[asyncio.Event] = None

    async def start(self):
        """Start listening for chat messages from Foundry."""
        self._running = True

        # Subscribe to chat events
        await self.foundry.subscribe_to_channel("chat-events")
        self.foundry.subscribe("chat-events", self._handle_chat_event)

        # Also subscribe to other relevant events
        await self.foundry.subscribe_to_channel("roll-events")
        self.foundry.subscribe("roll-events", self._handle_roll_event)

        await self.foundry.subscribe_to_channel("combat-events")
        self.foundry.subscribe("combat-events", self._handle_combat_event)

        await self.foundry.subscribe_to_channel("scene-events")
        self.foundry.subscribe("scene-events", self._handle_scene_event)

        # Sync Foundry's pause state back to the AI-GM so a human GM pressing
        # the pause button in FoundryVTT also halts AI processing.
        await self.foundry.subscribe_to_channel("hooks")
        self.foundry.subscribe("hooks", self._handle_hook_event)

        # Load player actor mapping so prompts can whisper to actual players
        await self._update_player_actors()
        # Load the GM-role user list so /gm commands can be authorized
        await self._update_gm_users()
        # Load settlements and register with world clock
        await self._load_campaign_settlements()

        self._reset_idle_timer()
        logger.info("Chat listener started — listening for player messages")

    async def stop(self):
        """Stop listening."""
        self._running = False
        self._cancel_idle_timer()
        if self._combat_loop:
            await self._combat_loop.stop()
        logger.info("Chat listener stopped")

    async def pause(self) -> None:
        """Pause the chat listener and AI narration.
        
        Called when admin panel requests pause via /api/ws pause message.
        Uses lock to synchronize with active narration.
        """
        async with self._running_lock:
            self._running = False
        logger.info("Chat listener paused")

    async def resume(self) -> None:
        """Resume the chat listener and AI narration.
        
        Called when admin panel requests resume via /api/ws resume message.
        Uses lock to synchronize with active narration.
        """
        async with self._running_lock:
            self._running = True
        logger.info("Chat listener resumed")


    async def _update_player_actors(self):
        """Update the game state with player actor mapping so LLM can whisper to players."""
        try:
            mapping = await self.foundry.get_player_actor_mapping()
            if mapping and mapping.get("actor_names"):
                await self.state_tracker.state.set_player_actors(mapping["actor_names"])
                logger.info(f"[Players] Updated mapping: {list(mapping['actor_names'].keys())}")
        except Exception as e:
            logger.error(f"Failed to update player actors: {e}")

    async def _update_gm_users(self):
        """Cache the set of Foundry users with a GM-tier role (role >= 3).

        Used to authorize /gm chat commands. Players (role <= 2) can never be in
        this set, so they cannot drive session/combat/pause control or
        impersonate the GM via /gm narrate.
        """
        try:
            res = await self.foundry.execute_js(
                "return Array.from(game.users).filter(u=>u.role>=3).map(u=>({id:u.id,name:u.name}));"
            )
            users = res.get("result") if isinstance(res, dict) else None
            if isinstance(users, list):
                self._gm_user_ids = {u.get("id") for u in users if u.get("id")}
                self._gm_user_names = {
                    (u.get("name") or "").lower() for u in users if u.get("name")
                }
                logger.info(f"[GM] Authorized GM users: {sorted(self._gm_user_names)}")
        except Exception as e:
            logger.warning(f"[GM] Could not load GM user list: {e}")

    async def _load_campaign_settlements(self):
        """Load settlements from campaign vault and register with world clock.

        Settlements are stored in campaign.json (generated during build).
        Deserialize and register them with the world clock so they're
        queryable via /gm settlement commands.
        """
        try:
            if not self._world_clock:
                return
            # Get active campaign
            active_campaign = await self.db.get_active_campaign()
            if not active_campaign:
                logger.info("No active campaign — skipping settlement load")
                return
            # Load campaign data from vault
            from campaign.vault import CampaignStore
            store = CampaignStore(active_campaign)
            if not store.exists:
                logger.warning(f"Campaign '{active_campaign}' not found in vault")
                return
            campaign_data = await store.load()
            # Deserialize and register settlements
            settlements_data = campaign_data.get("settlements", {})
            if isinstance(settlements_data, dict) and settlements_data:
                from campaign.settlement_integration import deserialize_settlements
                settlements = deserialize_settlements(settlements_data)
                for settlement in settlements.values():
                    self._world_clock.register_settlement(settlement)
                logger.info(f"Registered {len(settlements)} settlement(s) from campaign")
            else:
                logger.info("No settlements in campaign")
        except Exception as e:
            logger.warning(f"Failed to load campaign settlements: {e}")

    def _is_gm_author(self, inner: dict) -> bool:
        """True if a chat message was authored by a GM-tier Foundry user.

        Matches the author's user id/name against the cached GM-role set. The
        author is the *User* document — players cannot create or rename users,
        so a player account can't spoof a GM name. Foundry's default GM display
        name and the configured foundry_username are accepted as fallbacks so
        commands still work before the GM-user list has loaded.
        """
        author = inner.get("author") or inner.get("user") or {}
        if not isinstance(author, dict):
            author = {}
        aid = author.get("id") or author.get("_id")
        aname = (author.get("name") or "").lower()
        if aid and aid in self._gm_user_ids:
            return True
        if aname and aname in self._gm_user_names:
            return True
        if aname in ("gm", "gamemaster"):
            return True
        fu = (getattr(settings, "foundry_username", "") or "").lower()
        return bool(fu and aname == fu)

    async def _is_player_message(self, inner: dict) -> bool:
        """Determine if a chat message (pre-unwrapped inner data) is from a player."""
        content = inner.get("content", inner.get("message", ""))
        # Exclude empty messages
        if not content:
            return False

        # Exclude system messages
        if inner.get("type") == "system":
            return False

        # Exclude whispered messages (includes REST API Module echoes, which whisper to GM)
        whisper = inner.get("whisper", [])
        if whisper:
            return False

        # speaker is a Foundry object {alias, actor, token, scene}; extract alias
        raw_speaker = inner.get("speaker", {})
        speaker_alias = raw_speaker.get("alias", "") if isinstance(raw_speaker, dict) else str(raw_speaker)

        # PRIMARY echo guard: every message the AI posts via the relay REST API
        # comes back as a PUBLIC chat echo with an EMPTY speaker.alias — Foundry
        # only populates alias for user-typed messages; the relay puts our name
        # in `author` instead. Treating those echoes as player input is what
        # produced the staggered re-narration loop (each AI narrate/speak line
        # triggered another LLM turn ~one round-trip later). Content-snippet
        # matching alone could not catch them: the deque evicts under load, the
        # relay re-delivers old echoes on reconnect, and setup_scene narrate
        # fields don't byte-match. A genuine player or human-GM message ALWAYS
        # carries a speaker alias, so an empty alias ⟹ programmatically posted.
        if not speaker_alias.strip():
            return False

        # Exclude messages from AI-controlled speakers or module/system aliases
        if speaker_alias in self._ai_controlled_speakers or speaker_alias in ("GM", "REST API Module"):
            return False

        # Belt-and-suspenders: also drop anything authored by the GM/AI user
        # (covers Foundry builds that DO echo a non-empty alias on API posts).
        author = inner.get("author") or inner.get("user") or {}
        author_name = author.get("name", "") if isinstance(author, dict) else str(author)
        if author_name and (
            author_name in self._ai_controlled_speakers
            or author_name in ("GM", "Gamemaster", "REST API Module")
        ):
            return False

        # Suppress relay echoes of messages we just sent
        snippet = content[:120]
        async with self._sent_messages_lock:
            if any(msg == snippet for msg, _ in self._sent_messages_with_timestamp):
                logger.warning(f"[Actions] Dropping relay echo of our message")
                return False
        return True

    async def register_ai_speaker(self, speaker_name: str):
        """Register a speaker as AI-controlled (NPC, narration, etc) to prevent self-triggering.

        Refuses player-owned names: registering a PC here would make the echo
        guard (_is_player_message) silently drop that player's own future chat
        messages, so every call site funnels through this one check rather than
        each needing its own player-ownership guard.
        """
        if not speaker_name:
            return
        if await _is_player_character(speaker_name, self.foundry):
            return
        self._ai_controlled_speakers.add(speaker_name)

    async def _record_sent(self, text: str):
        """Track a message we're about to send so its echo can be suppressed."""
        now = time.monotonic()
        async with self._sent_messages_lock:
            # Append timestamped entry
            self._sent_messages_with_timestamp.append((text[:120], now))
            
            # Expire entries older than 5 minutes
            cutoff = now - 300
            self._sent_messages_with_timestamp = [
                (msg, ts) for msg, ts in self._sent_messages_with_timestamp
                if ts >= cutoff
            ]

    async def _record_actions(self, actions: list):
        """Record all outgoing text from an action list before dispatch.

        Covers standalone narrate/speak and the narrate field embedded in
        setup_scene/switch_scene, which also posts a chat message to Foundry.
        """
        for action in actions:
            if action.get("type") in ("narrate", "speak") and action.get("text"):
                await self._record_sent(action["text"])
            if action.get("narrate"):
                await self._record_sent(action["narrate"])
            if action.get("type") == "speak" and action.get("npc_name"):
                await self.register_ai_speaker(action["npc_name"])

    async def _handle_chat_event(self, envelope: dict):
        """Process incoming chat events from Foundry.

        The relay wraps Foundry's ChatMessage as:
          {"type": "chat-event", "event": "chat-create", "data": {<foundry ChatMessage>}}
        Foundry's ChatMessage has `content` (not `message`) and `speaker` as an object.
        """
        try:
            # Relay envelope: {type, event, data:{type, eventType, data:{type, eventType, data:{ChatMessage}}}}
            # Unwrap until we find a dict with "content"
            inner = envelope
            for _ in range(5):
                if isinstance(inner, dict) and "content" in inner:
                    break
                nxt = inner.get("data") if isinstance(inner, dict) else None
                if nxt is None or nxt is inner:
                    break
                inner = nxt

            content = inner.get("content", inner.get("message", ""))
            raw_speaker = inner.get("speaker", {})
            speaker = raw_speaker.get("alias", "") if isinstance(raw_speaker, dict) else str(raw_speaker)

            # Enforce maximum message length to prevent context exhaustion
            # and DoS via extremely long player messages.
            max_len = settings.chat_message_max_length
            if len(content) > max_len:
                logger.warning(
                    f"[Chat] Message from '{speaker}' exceeds max length "
                    f"({len(content)} > {max_len}), truncating"
                )
                content = content[:max_len] + " ... (truncated)"

            # GM control commands are an out-of-band channel: authorized by the
            # sender's Foundry role and handled BEFORE the session/pause/player
            # filters below. This lets the human GM (whose messages
            # _is_player_message intentionally drops) start a session or resume
            # the AI, while preventing players from impersonating the GM or
            # driving control via /gm. Only GM-tier senders are honored.
            is_gm_command = (
                content.startswith("/gm ")
                or content.startswith("/ask ")
                or content.strip() == "/ask"
            )
            if is_gm_command:
                if self._is_gm_author(inner):
                    await self._handle_gm_command(speaker, content)
                else:
                    logger.warning(f"Ignoring /gm command from non-GM sender '{speaker}'")
                return

            # Don't respond to anything if no session is active — prevents the AI
            # from narrating during campaign setup, deploy, or while idle.
            session_id = await self.db.get_active_session()
            if not session_id:
                return

            # Skip non-player messages
            if not await self._is_player_message(inner):
                return

            prologue_interrupt = getattr(self.foundry, "_prologue_interrupt_event", None)
            if isinstance(prologue_interrupt, asyncio.Event) and not prologue_interrupt.is_set():
                prologue_interrupt.set()

            logger.info(f"Chat message from {speaker}: {content[:100]}")

            # Respect the pause flag for normal player messages
            if not self._running:
                return

            # Player is active — reset the idle countdown and block pacing
            # immediately so idle doesn't fire while we're building context
            # or waiting on the LLM. Holding the turn lock for the whole turn
            # serialises against any in-flight pacing/idle beat.
            self._reset_idle_timer()
            self._player_message_count += 1

            # Multi-player input batching: debounce simultaneous messages into
            # one combined turn instead of one turn per message. Skipped
            # during combat (turn order would conflict with coalescing) and
            # when only one player is currently active (pure added latency
            # with no benefit for solo play).
            in_combat = (
                self.state_tracker.state.mode == "combat"
                and self._combat_loop and self._combat_loop.is_running
            )
            debounce_seconds = settings.input_batch_debounce_seconds
            active_players = self._track_active_speaker(speaker)

            if debounce_seconds > 0 and not in_combat and active_players > 1:
                self._pending_batch_inputs.append((speaker, content))
                # Only cancel a pending flush that hasn't committed yet — a
                # committed one already popped its own batch and is safe to
                # let keep running; this message gets a fresh task instead.
                if (
                    self._input_batch_task
                    and not self._input_batch_task.done()
                    and not self._batch_committed
                ):
                    self._input_batch_task.cancel()
                self._batch_committed = False
                self._input_batch_task = asyncio.create_task(self._flush_input_batch(debounce_seconds))
                return

            async with self._turn_lock:
                await self._run_turn(content, speaker)

        except Exception as e:
            logger.error(f"Error handling chat event: {e}", exc_info=True)
            await self.foundry.chat_message(
                "*The GM takes a moment to collect their thoughts…*",
                speaker="GM"
            )

    # Rolling window used to estimate how many players are currently active
    # at the table, from chat traffic alone (no extra Foundry round-trip).
    _ACTIVE_SPEAKER_WINDOW_SECONDS = 600  # 10 minutes

    def _track_active_speaker(self, speaker: str) -> int:
        """Record this speaker as recently active and return the number of
        distinct speakers seen within the last _ACTIVE_SPEAKER_WINDOW_SECONDS."""
        now = time.time()
        self._recent_speakers[speaker] = now
        self._recent_speakers = {
            s: t for s, t in self._recent_speakers.items()
            if now - t < self._ACTIVE_SPEAKER_WINDOW_SECONDS
        }
        return len(self._recent_speakers)

    async def _run_turn(self, content: str, speaker: str):
        """Body of a single AI GM turn: build context, then dispatch to
        combat or normal processing. Caller must hold self._turn_lock.

        Shared by the immediate path and the batched-flush path so context
        is always built fresh at the moment a turn actually runs, not at
        message-arrival time (which would be stale/wasted for a message that
        ends up merged into a later batch).
        """
        game_state = self.state_tracker.get_snapshot()

        extra_context = await self._get_npc_context()
        location = await self._build_location_context()
        if location:
            extra_context += f"\n\n## CURRENT LOCATION\n{location}"

        if self.state_tracker.state.mode == "combat" and self._combat_loop and self._combat_loop.is_running:
            await self._process_combat_input(content, speaker)
        else:
            await self._process_normal_input(content, speaker, game_state, extra_context)

    async def _flush_input_batch(self, delay: float):
        """Wait out the debounce window, then run one combined turn for
        everything that arrived. Cancelled (not run) if a newer message
        rescheduled the batch — that newer task is the one that flushes."""
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return

        pending, self._pending_batch_inputs = self._pending_batch_inputs, []
        if not pending:
            return

        # Past this point we're committed to running this batch — a message
        # arriving now must not cancel us (see _handle_chat_event), since
        # cancellation past the pop used to silently drop `pending`.
        self._batch_committed = True

        if len(pending) == 1:
            speaker, content = pending[0]
        else:
            speaker = "Table"
            content = "\n".join(f"{s}: {c}" for s, c in pending)

        async with self._turn_lock:
            await self._run_turn(content, speaker)

    async def _process_player_input(
        self, content: str, speaker: str, game_state: str, extra_context: str, advance_turn: bool = False
    ):
        """Process a player message: generate actions via LLM and execute them.

        Shared by _process_normal_input and _process_combat_input. The
        *extra_context* parameter is already fully built up by the caller
        (including scene awareness), so this method only needs the final
        game state summary to pass to the LLM.

        Args:
            content: The raw player message text.
            speaker: The speaker name (player or GM).
            game_state: Serialized game state string for the LLM.
            extra_context: Additional context appended before the LLM call.
            advance_turn: If True, signal the combat loop to advance to the
                          next turn after actions complete (used during combat).

        Returns:
            Tuple of (actions, results) where actions are the LLM-generated
            action dicts and results are the execution output from the dispatcher.
        """
        # Inject semantic lore from vault if available (P2b)
        if self._semantic_rag:
            try:
                lore_results = await self._semantic_rag.inject_lore(content + "\n" + game_state, top_k=3)
                if lore_results:
                    lore_text = "\n\n## VAULT LORE (Semantic Search)\n"
                    for result in lore_results:
                        lore_text += f"- {result.text} (source: {result.source})\n"
                    extra_context += lore_text
            except Exception as e:
                logger.warning(f"Semantic RAG injection failed: {e}")

        result = await self.llm.generate(
            user_message=f"[{speaker}]: {content}",
            game_state_summary=game_state,
            extra_context=extra_context,
        )
        actions = result.get("actions", [])
        results = []

        # Referee gate: rules-consistency adjudication happens here, before
        # dispatch. Approved actions may be rules-adjusted (e.g. a clamped
        # DC); rejected ones are converted into dispatcher-shaped failure
        # results so the rest of the pipeline (retry-notify, admin panel)
        # handles them exactly like an execution failure.
        if actions:
            rulings = await self._referee.adjudicate_batch(actions)
            actions = []
            for ruling in rulings:
                if ruling.approved:
                    actions.append(ruling.action)
                else:
                    results.append({
                        "type": ruling.action.get("type"),
                        "error": ruling.reason or "Action rejected by referee",
                        "success": False,
                    })

        dispatch_results = []
        if actions:
            await self._record_actions(actions)
            dispatch_results = await self.dispatcher.execute_batch(actions)

        # Retry-notify sees every failure this turn — referee rejections
        # (added to `results` above, before dispatch) and genuine dispatch
        # failures alike — so a referee-rejected action gets the same
        # same-turn self-correction chance as a bad dispatch. This must run
        # even when `actions` ended up empty (every proposal was rejected
        # by the referee), which the old `if actions:`-gated call skipped
        # entirely.
        dispatch_results += await self._notify_llm_of_failures(results + dispatch_results)
        results += dispatch_results

        if actions:
            await self._place_referenced_combatants(actions)
        if dispatch_results:
            await self._handle_generated_npcs(dispatch_results)
            await self._update_immersion_state(dispatch_results)
            await self._record_action_resolved_events(dispatch_results)
        if actions:
            logger.info(f"[Actions] Executed {len(actions)} actions for {speaker}")

        if advance_turn and self._combat_loop and self._combat_loop.is_running:
            self._combat_loop.advance_pc_turn()

        return actions, results

    async def _record_action_resolved_events(self, dispatch_results: list, trigger_npcs: bool = True) -> None:
        """Append one ACTION_RESOLVED event per dispatched action, so the
        event log (events/store.py) has a full record to replay later —
        this is what Phase 4/5 agents read to know what already happened.

        trigger_npcs=False is used when recording an NPC's OWN dispatched
        action (see _maybe_trigger_npc_agents below) — otherwise an NPC
        acting would immediately wake the very candidates SceneDirector
        just deferred, defeating "one actor per tick" the moment the chosen
        actor's own action resolves."""
        session_id = await self.db.get_active_session()
        if not session_id:
            return
        for result in dispatch_results:
            payload = {
                "action_type": result.get("type"),
                "success": result.get("success", True),
                "error": result.get("error"),
            }
            await self._event_store.append(session_id, ACTION_RESOLVED, payload=payload)
            if trigger_npcs:
                await self._maybe_trigger_npc_agents(session_id, {"type": ACTION_RESOLVED, "payload": payload})

    async def _maybe_trigger_npc_agents(self, session_id: str, event: dict) -> None:
        """Wake any NPC whose pending/active goal matches *event* — the
        reactive path for Phase 5's autonomous NPC turns. Independent of
        WorldClockAgent's own activation of time-based goals; this fires on
        any event type, not just TIME_ADVANCED.

        Only ONE NPC acts per event, chosen by SceneDirector — when several
        NPCs match the same event, only the chosen one's matched goals flip
        to 'active'; the rest are left untouched (still 'pending', not
        activated) so a runner-up doesn't carry a stale 'active' goal into
        some later, unrelated turn where it happens to be chosen instead —
        it's reconsidered fresh, from its actual current status, next time
        an event matches it. Non-mutating candidate collection also means
        this fires on any event type, not just TIME_ADVANCED, independent
        of WorldClockAgent's own activation of time-based goals (or
        reacting to each other within the same tick — see the
        trigger_npcs=False on the recording of the chosen NPC's own action
        below)."""
        if not self._npc_registry:
            return
        candidates = []
        for npc in self._npc_registry.list_npcs():
            matched = [g for g in npc.goals if g.status in ("pending", "active") and g.matches(event)]
            if matched:
                candidates.append(Candidate(npc=npc, matched_goals=matched))

        chosen = self._scene_director.next_turn(candidates)
        if not chosen:
            return
        for goal in chosen.matched_goals:
            goal.status = "active"

        agent = NPCAgent(chosen.npc, self._model_router, self._referee, self._npc_memory)
        rulings = await agent.act(session_id, event)
        if not rulings:
            return
        for goal in chosen.matched_goals:
            goal.status = "done"

        actions = [r.action for r in rulings]
        dispatch_results = await self.dispatcher.execute_batch(actions)
        logger.info(f"[NPCAgent] {chosen.npc.npc_name} self-initiated {len(actions)} action(s)")
        await self._record_action_resolved_events(dispatch_results, trigger_npcs=False)

    async def _process_normal_input(self, content: str, speaker: str, game_state: str, extra_context: str):
        """Process a normal (non-combat) player message."""
        try:
            actions, results = await self._process_player_input(
                content, speaker, game_state, extra_context, advance_turn=False
            )

            # Record in DB
            session_id = await self.db.get_active_session()
            if session_id:
                await self.db.save_conversation(session_id, "user", content)
                for action in actions:
                    await self.db.save_conversation(
                        session_id, "assistant", json.dumps(action)
                    )

            # Record turn for context reinforcement
            if hasattr(self, '_reinforcement_mgr') and self._reinforcement_mgr:
                await self._reinforcement_mgr.record_turn(
                    f"[{speaker}]: {content}",
                    json.dumps(results) if results else "No actions executed"
                )

            # Pacing check: after every N player exchanges, have the GM evaluate
            # whether the scene needs a push (NPC entrance, ticking clock, etc.)
            pace_interval = getattr(settings, "gm_pace_interval", 10)
            if pace_interval > 0 and self._player_message_count % pace_interval == 0:
                spawn(self._process_proactive_action(reason="pacing"))

            # Notify admin panel
            if self._on_results_callback:
                await self._on_results_callback(results)

        except Exception as e:
            logger.error(f"Error processing message: {e}", exc_info=True)
            # Don't leave the table hanging if the LLM/transport fails outright.
            try:
                await self.foundry.chat_message(
                    "*The GM pauses, the scene holding its breath for a moment…*",
                    speaker="GM"
                )
            except Exception:
                pass

    async def _process_combat_input(self, content: str, speaker: str):
        """Process player input during combat.

        Routes the player's input through the LLM for action generation,
        executes the resulting actions, then signals the combat loop to
        advance to the next turn.
        """
        try:
            game_state = self.state_tracker.get_snapshot()
            extra_context = await self._get_npc_context()
            location = await self._build_location_context()
            if location:
                extra_context += f"\n\n## CURRENT LOCATION\n{location}"

            actions, results = await self._process_player_input(
                content, speaker, game_state, extra_context, advance_turn=True
            )

            # Notify admin panel
            if self._on_results_callback:
                await self._on_results_callback(results)

        except Exception as e:
            logger.error(f"Error processing combat input: {e}", exc_info=True)
            await self.foundry.chat_message(
                "*The GM pauses, then waves the action through.*",
                speaker="GM"
            )
            # Still advance to avoid deadlock
            if self._combat_loop and self._combat_loop.is_running:
                self._combat_loop.advance_pc_turn()

    async def _handle_gm_command(self, speaker: str, content: str):
        """Handle a /gm command from a player (for the human GM)."""
        # Strip "/gm " or "/ask" prefix
        if content.startswith("/gm "):
            command = content[4:].strip()
        else:
            # /ask prefix — 4 chars, no trailing space required
            command = content[4:].strip()

        if command.startswith("start session"):
            campaign_name = command[len("start session"):].strip() or settings.default_campaign or "Adventure"
            await self._cmd_start_session(campaign_name)
        elif command.startswith("narrate "):
            await self.foundry.chat_message(command[8:], speaker="GM")
        elif command.startswith("roll "):
            roll_part = command[5:].strip()
            await self.foundry.roll(roll_part, speaker="GM")
        elif command == "help":
            await self.foundry.chat_message(
                "GM Commands:\n"
                "/gm start session [name] — start a new session (activates the AI)\n"
                "/gm narrate <text> — send narrative text\n"
                "/gm roll <formula> — roll dice\n"
                "/gm rule <fact> — assert a canonical fact (auto-injects into LLM context)\n"
                "/gm canonize <fact> — alias for /gm rule\n"
                "/gm canon review — list pending AI-proposed canon facts\n"
                "/gm canon approve <n> — approve proposal <n> from the last review\n"
                "/gm canon reject <n> — reject proposal <n> from the last review\n"
                "/gm start combat — start combat loop\n"
                "/gm stop combat — stop combat loop\n"
                "/gm pause ai — pause AI processing\n"
                "/gm resume ai — resume AI processing\n"
                "/gm session replay [limit] — show last N events (default 20)\n"
                "/gm session events <type> — show all events of a type (e.g., 'action_resolved')\n"
                "/gm settlement query <id> [time] — show NPCs at locations in a settlement\n"
                "/gm settlement list — list all settlements in the campaign\n"
                "/gm end session — end the session, export a recap to Foundry + vault",
                speaker="GM"
            )
        elif command == "start combat":
            await self._start_combat()
        elif command == "stop combat":
            if self._combat_loop:
                await self._combat_loop.stop()
            await self.foundry.chat_message("Combat loop stopped.", speaker="GM")
        elif command == "pause ai":
            self._running = False
            await self.foundry.chat_message("GM: AI is paused.", speaker="GM")
        elif command == "resume ai":
            self._running = True
            await self.foundry.chat_message("GM: AI is now active.", speaker="GM")
        elif command.startswith("rule ") or command.startswith("canonize "):
            prefix = "rule " if command.startswith("rule ") else "canonize "
            fact_text = command[len(prefix):].strip()

            # Get active session info
            session_info = await self.db.get_active_session_info()
            if not session_info:
                await self.foundry.chat_message(
                    "An active session is required to add to the canon. "
                    "Use /gm start session first.",
                    speaker="GM"
                )
                return

            try:
                campaign_name = session_info.get("campaign")
                vault_path = obsidian_sync.resolve_vault_path(settings.campaign_vault_path)
                campaign_folder = obsidian_sync.get_campaign_folder(vault_path, campaign_name)
                await obsidian_sync.push_canon_fact_live(campaign_folder, fact_text, self.llm)

                await self.foundry.chat_message(
                    f"📜 Canon updated: {fact_text}",
                    speaker="GM"
                )
                logger.info(f"Canon fact added: {fact_text}")

            except Exception as e:
                logger.error(f"Failed to update canon: {e}", exc_info=True)
                await self.foundry.chat_message(
                    f"❌ Failed to update canon: {str(e)}",
                    speaker="GM"
                )
        elif command.startswith("session replay"):
            # /gm session replay [limit]
            session_id = await self.db.get_active_session()
            if not session_id:
                await self.foundry.chat_message(
                    "No active session. Use /gm start session first.",
                    speaker="GM"
                )
                return

            try:
                from events.replay import SessionReplay
                limit_str = command[len("session replay"):].strip()
                limit = int(limit_str) if limit_str else 20

                replay = SessionReplay(self._event_store)
                events = await replay.get_session_transcript(session_id, limit=limit)
                text = replay.format_transcript_for_chat(events)

                await self.foundry.chat_message(text, speaker="GM")
                logger.info(f"[Session] Replayed {len(events)} events")
            except Exception as e:
                logger.error(f"[Session] Replay failed: {e}", exc_info=True)
                await self.foundry.chat_message(
                    f"❌ Replay failed: {str(e)}",
                    speaker="GM"
                )

        elif command.startswith("session events "):
            # /gm session events <type>
            session_id = await self.db.get_active_session()
            if not session_id:
                await self.foundry.chat_message(
                    "No active session. Use /gm start session first.",
                    speaker="GM"
                )
                return

            try:
                from events.replay import SessionReplay
                event_type = command[len("session events "):].strip()

                replay = SessionReplay(self._event_store)
                events = await replay.find_events_by_type(session_id, event_type)

                if not events:
                    await self.foundry.chat_message(
                        f"No events of type '{event_type}' found.",
                        speaker="GM"
                    )
                else:
                    text = replay.format_transcript_for_chat(events)
                    await self.foundry.chat_message(text, speaker="GM")
                    logger.info(f"[Session] Found {len(events)} {event_type} events")
            except Exception as e:
                logger.error(f"[Session] Events lookup failed: {e}", exc_info=True)
                await self.foundry.chat_message(
                    f"❌ Lookup failed: {str(e)}",
                    speaker="GM"
                )

        elif command == "settlement list":
            # List all registered settlements
            try:
                if not getattr(self, "_world_clock", None):
                    await self.foundry.chat_message(
                        "Settlement system not initialized.",
                        speaker="GM"
                    )
                    return

                settlements = self._world_clock.list_settlements()
                if not settlements:
                    await self.foundry.chat_message(
                        "📍 No settlements registered in this campaign.",
                        speaker="GM"
                    )
                else:
                    lines = ["📍 **Settlements in Campaign:**\n"]
                    for settlement in settlements:
                        npcs_count = len(settlement.npcs)
                        buildings_count = len(settlement.buildings)
                        lines.append(
                            f"• **{settlement.name}** ({settlement.region}): "
                            f"{settlement.population} pop, {npcs_count} NPCs, "
                            f"{buildings_count} buildings"
                        )
                    await self.foundry.chat_message("\n".join(lines), speaker="GM")
                    logger.info(f"[Session] Listed {len(settlements)} settlements")
            except Exception as e:
                logger.error(f"[Settlement] List failed: {e}", exc_info=True)
                await self.foundry.chat_message(
                    f"❌ Settlement list failed: {str(e)}",
                    speaker="GM"
                )

        elif command.startswith("settlement query "):
            # /gm settlement query <settlement_id> [time_of_day]
            try:
                if not getattr(self, "_world_clock", None):
                    await self.foundry.chat_message(
                        "Settlement system not initialized.",
                        speaker="GM"
                    )
                    return

                parts = command[len("settlement query "):].strip().split()
                if not parts:
                    await self.foundry.chat_message(
                        "Usage: /gm settlement query <settlement_id> [time]",
                        speaker="GM"
                    )
                    return

                settlement_id = parts[0]
                time_of_day = parts[1] if len(parts) > 1 else None

                locations = await self._world_clock.query_location_at_time(settlement_id, time_of_day)

                if not locations:
                    current_time = time_of_day or self._world_clock.get_current_time()
                    await self.foundry.chat_message(
                        f"📍 No NPCs found in {settlement_id} at {current_time}",
                        speaker="GM"
                    )
                else:
                    current_time = time_of_day or self._world_clock.get_current_time()
                    lines = [f"📍 **{settlement_id}** at **{current_time}:**\n"]
                    for location, npcs in sorted(locations.items()):
                        npc_names = ", ".join(npcs)
                        lines.append(f"• **{location}**: {npc_names}")
                    await self.foundry.chat_message("\n".join(lines), speaker="GM")
                    logger.info(
                        f"[Settlement] Queried {settlement_id} at {current_time}: "
                        f"{sum(len(n) for n in locations.values())} NPCs found"
                    )
            except Exception as e:
                logger.error(f"[Settlement] Query failed: {e}", exc_info=True)
                await self.foundry.chat_message(
                    f"❌ Settlement query failed: {str(e)}",
                    speaker="GM"
                )

        elif command == "end session":
            session_info = await self.db.get_active_session_info()
            if not session_info:
                await self.foundry.chat_message("No active session to end.", speaker="GM")
                return
            session_id = session_info["session_id"]
            campaign_name = session_info.get("campaign") or ""

            summary_text = ""
            try:
                if getattr(self, "_reinforcement_mgr", None):
                    summary_text = await self._reinforcement_mgr.summarize_context()
            except Exception as e:
                logger.warning(f"[Session] Failed to summarize session {session_id}: {e}")
            summary_text = summary_text or "No session highlights recorded."

            campaign_folder = None
            if campaign_name:
                vault_path = obsidian_sync.resolve_vault_path(settings.campaign_vault_path)
                campaign_folder = obsidian_sync.get_campaign_folder(vault_path, campaign_name)

            # Recap export and canon-proposal generation are independent —
            # the latter only needs session highlights and existing Canon.md
            # content, nothing the recap export produces — so they run
            # concurrently rather than paying the sum of both durations
            # (the canon LLM call alone can take up to ~2 minutes).
            await asyncio.gather(
                self._export_session_recap(session_id, campaign_folder, summary_text),
                self._generate_and_store_canon_proposals(session_id, campaign_name, campaign_folder),
                return_exceptions=True,
            )

            # Advance the world clock, give any newly-activated NPC goal a
            # chance to actually act (WorldClockAgent only activates
            # matching goals — it has no LLM access itself to act on them;
            # without this call those goals would sit 'active' forever,
            # since nothing else is ever invoked with a time_advanced
            # event), then persist any NPC goal changes — best-effort, must
            # not block ending the session.
            if self._world_clock:
                try:
                    duration = settings.world_clock_session_end_advance_seconds
                    await self._world_clock.advance(session_id, duration)
                    await self._maybe_trigger_npc_agents(
                        session_id, {"type": TIME_ADVANCED, "payload": {"duration_seconds": duration}}
                    )
                    await npc_persistence.save(self.db, campaign_name, self._npc_registry)
                except Exception as e:
                    logger.warning(f"[Session] World clock advance failed: {e}")

            # Ending the session must not be blocked by either task above.
            await self.db.close_session(session_id)
            await self.foundry.chat_message("🛑 Session ended.", speaker="GM")
            logger.info(f"[Session] Ended session {session_id}")
        elif command == "canon review":
            proposals = await self.db.get_pending_canon_proposals()
            if not proposals:
                await self.foundry.chat_message("No pending canon proposals.", speaker="GM")
                return
            shown = proposals[:5]
            self._canon_review_ids = [p["id"] for p in shown]
            lines = ["📜 **Pending canon proposals:**"]
            for i, p in enumerate(shown, 1):
                note = f" ⚠️ conflicts with: {p['contradiction_note']}" if p.get("contradiction_note") else ""
                lines.append(f"{i}. [{p['confidence'].upper()}] {p['fact']} — {p['rationale']}{note}")
            lines.append("\nUse /gm canon approve <n> or /gm canon reject <n>.")
            await self.foundry.chat_message("\n".join(lines), speaker="GM")
        elif command.startswith("canon approve ") or command.startswith("canon reject "):
            is_approve = command.startswith("canon approve ")
            idx_str = command[len("canon approve "):].strip() if is_approve else command[len("canon reject "):].strip()
            if not self._canon_review_ids:
                await self.foundry.chat_message("Run /gm canon review first.", speaker="GM")
                return
            try:
                idx = int(idx_str)
                if idx < 1 or idx > len(self._canon_review_ids):
                    raise IndexError
                proposal_id = self._canon_review_ids[idx - 1]
            except (ValueError, IndexError):
                await self.foundry.chat_message(f"Invalid proposal number: {idx_str}", speaker="GM")
                return

            if is_approve:
                success, message = await approve_canon_proposal_with_vault_write(
                    self.db, self.llm, proposal_id, settings.campaign_vault_path,
                )
                if success:
                    await self.foundry.chat_message(f"✅ Canon proposal #{idx} approved.", speaker="GM")
                else:
                    await self.foundry.chat_message(f"⚠️ Proposal #{idx} not approved: {message}", speaker="GM")
            else:
                success, message = await reject_canon_proposal_safely(self.db, proposal_id)
                if success:
                    await self.foundry.chat_message(f"❌ Canon proposal #{idx} rejected.", speaker="GM")
                else:
                    await self.foundry.chat_message(f"⚠️ Proposal #{idx} not rejected: {message}", speaker="GM")
        else:
            await self.foundry.chat_message(
                f"Unknown command: {command}. Use /gm help.",
                speaker="GM"
            )

    async def _export_session_recap(self, session_id: str, campaign_folder, summary_text: str):
        """Write a session-end recap to Foundry + the vault. Self-contained
        try/except so a failure here never blocks session close or the
        (independent) canon-proposal generation running alongside it."""
        try:
            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            journal_name = f"Session Recap — {date_str}"
            await self.foundry.create_entity("JournalEntry", {
                "name": journal_name,
                "pages": [{
                    "name": journal_name,
                    "type": "text",
                    "text": {"content": summary_text.replace("\n", "<br>"), "format": 1},
                }],
                "flags": {"ai-gm": {"type": "session_recap", "session_id": session_id}},
            })

            if campaign_folder:
                await obsidian_sync.save_session_recap(campaign_folder, session_id, summary_text)

            await self.foundry.chat_message(
                f"📖 Session recap saved — *{journal_name}*.", speaker="GM"
            )
            logger.info(f"[Session] Recap exported for session {session_id}")
        except Exception as e:
            logger.error(f"[Session] Recap export failed for session {session_id}: {e}", exc_info=True)
            await self.foundry.chat_message(
                f"⚠️ Session ending, but recap export failed: {e}", speaker="GM"
            )

    async def _generate_and_store_canon_proposals(self, session_id: str, campaign_name: str, campaign_folder):
        """Generate canon proposals from this session's highlights and store
        them in the review queue — never auto-approved. Self-contained
        try/except so a failure here never blocks session close or the
        (independent) recap export running alongside it."""
        try:
            if not (getattr(self, "_reinforcement_mgr", None) and campaign_folder):
                return
            highlights = self._reinforcement_mgr.get_session_highlights()
            existing_canon_text = ""
            canon_file = campaign_folder / "Canon.md"
            if canon_file.exists():
                existing_canon_text = await asyncio.to_thread(canon_file.read_text, encoding="utf-8")

            endpoint = settings.llm_base_url.rstrip("/") + "/chat/completions?thinking=false"
            headers = {
                "Authorization": f"Bearer {settings.llm_api_key}",
                "Content-Type": "application/json",
            }
            proposals = await generate_canon_proposals(
                self.llm._http, endpoint, headers, settings.model,
                highlights, existing_canon_text,
            )
            for p in proposals:
                await self.db.create_canon_proposal(
                    session_id=session_id,
                    campaign=campaign_name,
                    fact=p["fact"],
                    confidence=p["confidence"],
                    rationale=p["rationale"],
                    contradiction_note=p["contradiction_note"],
                )
            if proposals:
                await self.foundry.chat_message(
                    f"📜 {len(proposals)} canon proposal(s) awaiting review — /gm canon review",
                    speaker="GM"
                )
        except Exception as e:
            logger.warning(f"[Session] Canon proposal generation failed for session {session_id}: {e}")

    async def _start_combat(self):
        """Start a combat encounter."""
        if not self._combat_loop:
            await self.foundry.chat_message("Combat loop not available.", speaker="GM")
            return

        # Get tokens from scene
        scene_tokens = await self.foundry.get_scene_tokens()
        if not scene_tokens:
            await self.foundry.chat_message(
                "No tokens found on current scene to start combat.",
                speaker="GM"
            )
            return

        await self.foundry.chat_message(
            f"⚔️ **Combat started!** {len(scene_tokens)} tokens engaged.",
            speaker="GM"
        )
        # Launch combat loop as a background task to avoid blocking the reader
        spawn(self._combat_loop.start_combat_loop(scene_tokens))

    async def _handle_roll_event(self, data: dict):
        """Handle dice roll events — update state if in combat."""
        try:
            roll_data = data.get("data", data)
            roll_result = roll_data.get("roll", roll_data.get("total", 0))
            speaker = roll_data.get("speaker", "Unknown")

            # If in combat, track roll results
            if self.state_tracker.state.mode == "combat":
                # Could update NPC/PC HP based on roll results
                # For now, just log
                logger.info(f"Roll by {speaker}: {roll_result}")
        except Exception as e:
            logger.error(f"Error handling roll event: {e}", exc_info=True)

    async def _handle_combat_event(self, data: dict):
        """Handle combat events from Foundry."""
        try:
            combat_data = data.get("data", data)
            event_type = combat_data.get("type", data.get("type", ""))

            if event_type == "start" or data.get("type") == "encounter-started":
                await self.state_tracker.set_combat_mode(in_combat=True)

                # Start auto-combat loop if configured
                if self._combat_loop:
                    scene_tokens = await self.foundry.get_scene_tokens()
                    if scene_tokens:
                        # Launch combat loop as a background task to avoid blocking the reader
                        spawn(self._combat_loop.start_combat_loop(scene_tokens))
                        await self.foundry.chat_message(
                            "⚔️ AI combat loop started.",
                            speaker="GM"
                        )

                logger.info("[State] Combat started")

            elif event_type == "end" or data.get("type") == "encounter-ended":
                await self.state_tracker.set_combat_mode(in_combat=False)
                if self._combat_loop:
                    await self._combat_loop.stop()
                logger.info("[State] Combat ended")

            elif event_type == "turn" or data.get("type") == "encounter-turn":
                turn_data = combat_data.get("turn", {})
                current_actor = turn_data.get("actorId", turn_data.get("speaker", ""))
                # Update the turn counter but do NOT replace the full turn_order.
                # Replacing turn_order with [current_actor] loses the rest of the
                # initiative list, causing the combat loop to treat every subsequent
                # tick as a brand-new round.
                existing_order = self.state_tracker.state.combat.turn_order if self.state_tracker.state.combat.turn_order else []
                await self.state_tracker.update_combat(
                    in_combat=True,
                    turn=self.state_tracker.state.combat.turn + 1,
                    turn_order=existing_order
                )
                logger.info(f"[State] Combat turn: {current_actor}")

        except Exception as e:
            logger.error(f"Error handling combat event: {e}", exc_info=True)

    async def _handle_scene_event(self, data: dict):
        """Handle scene change events."""
        try:
            # The relay fans out scene-events with the payload under "data" and
            # the scene keyed by sceneId/name (not "sceneName"), so probe several
            # shapes. AI-driven switches are handled deterministically by the
            # scene-switch executors; this path mainly catches the human GM
            # changing scenes manually in Foundry.
            inner = data.get("data", data) if isinstance(data, dict) else {}
            inner = inner if isinstance(inner, dict) else {}
            scene_name = (
                data.get("sceneName")
                or inner.get("sceneName")
                or inner.get("name")
                or data.get("name")
                or ""
            )
            if scene_name:
                await self.state_tracker.set_scene(scene_name)

                # Clear stale encounter and scene data from the previous scene
                # so the LLM doesn't reference NPCs, tokens, or context from
                # the old scene when a new session begins.
                cleared = await self.state_tracker.clear_stale_scene_data()
                if cleared:
                    logger.info(f"[State] Cleared stale scene data for scene: {scene_name}")

                logger.info(f"[State] Scene changed to: {scene_name}")

                # Update scene awareness
                if self._scene_awareness:
                    await self._scene_awareness.on_scene_change(scene_name)

                # Refresh player actor mapping in case party composition changed
                await self._update_player_actors()
                await self._update_gm_users()
        except Exception as e:
            logger.error(f"Error handling scene event: {e}", exc_info=True)

    async def _handle_hook_event(self, data: dict):
        """Handle generic Foundry hook events.

        We only care about pauseGame / resumeGame here so that a human GM
        pressing the pause button in Foundry automatically suspends AI-GM
        processing (and re-enables it on unpause).
        """
        hook = data.get("hook", "")
        try:
            if hook == "pauseGame":
                paused = data.get("data", {}).get("paused", True)
                if paused and self._running:
                    self._running = False
                    logger.info("[Hook] Foundry paused → AI-GM suspended")
                elif not paused and not self._running:
                    self._running = True
                    self._reset_idle_timer()
                    logger.info("[Hook] Foundry unpaused → AI-GM resumed")
        except Exception as e:
            logger.error(f"Error handling hook event ({hook}): {e}", exc_info=True)

    async def _build_location_context(self) -> str:
        """Build the per-turn CURRENT LOCATION block that anchors the GM to the
        map it's actually displaying.

        The session-opening beat hands the model the scene's authored
        atmosphere, the live token IDs, and the scene catalog — but every turn
        after got only a scene name + entity names, so the GM lost the ability
        to (a) narrate the right place, (b) move_token (no IDs), and (c)
        switch_scene (no catalog). This rebuilds all three every turn.
        """
        scene_name = self.state_tracker.state.current_scene
        if not scene_name:
            return ""
        parts = [f"**Active scene (this map is on the players' screens):** {scene_name}"]

        # (a) Authored atmosphere so narration matches the displayed map.
        try:
            briefing = self._campaign_loader.get_scene_briefing(scene_name) if self._campaign_loader else ""
        except Exception:
            briefing = ""
        if briefing:
            parts.append(
                "Ground ALL narration in this location's authored description — "
                "do NOT invent a different setting:\n" + briefing
            )

        # (b) Live token IDs/positions so move_token is actually usable.
        try:
            tokens = await self.foundry.get_scene_tokens()
        except Exception:
            tokens = []
        if tokens:
            lines = []
            for t in tokens:
                disp = t.get("disposition", 1)
                side = "ally/PC" if (disp or 0) >= 0 else "hostile"
                lines.append(
                    f"- {t.get('name', '?')} ({side}) token_id={t.get('id', '?')} "
                    f"at ({t.get('x', '?')}, {t.get('y', '?')})"
                )
            parts.append(
                "Tokens on this map (use these exact token_id values for move_token "
                "when someone moves):\n" + "\n".join(lines)
            )

        # (c) Scene catalog so switch_scene has real targets, cached ~60s.
        now = asyncio.get_event_loop().time()
        if not self._scene_catalog or now - self._scene_catalog_at > 60:
            try:
                names = await self.foundry.list_scene_names()
            except Exception:
                names = []
            if names:
                self._scene_catalog = names
                self._scene_catalog_at = now
        others = [n for n in self._scene_catalog if n != scene_name]
        if others:
            parts.append(
                "Other available maps — call switch_scene with the EXACT name when the "
                "story moves to one (do NOT generate new maps, these already exist): "
                + ", ".join(others)
            )

        return "\n\n".join(parts)

    async def _get_npc_context(self) -> str:
        """Get current NPC context from loaded files + Foundry actors + Personality Registry."""
        parts = []
        if self._campaign_loader:
            npc = self._campaign_loader.get_npc_context_sync()
            if npc:
                parts.append(npc)

        try:
            actors = await self.foundry.get_actors(world_only=True)
            if actors:
                actor_lines = []
                for a in actors:
                    actor_name = a.get('name', 'Unknown')
                    # Include the real uuid so actions like update_hp target a
                    # valid actor instead of a hallucinated id.
                    uuid_part = f" [uuid: {a['uuid']}]" if a.get('uuid') else ""
                    actor_lines.append(
                        f"- {actor_name}{uuid_part} "
                        f"(HP: {a.get('hp', '?')}/{a.get('max_hp', '?')})"
                    )

                    # Inject personality traits and relationships from registry (Tier 3)
                    if self._npc_registry:
                        try:
                            npc_context = self._npc_registry.get_context(actor_name)
                            if npc_context:
                                actor_lines.append(f"  {npc_context[:200]}...")
                        except Exception as e:
                            logger.debug(f"Failed to get personality for {actor_name}: {e}")

                parts.append("Active NPCs/Characters:\n" + "\n".join(actor_lines))
        except Exception as e:
            logger.warning(f"Failed to get actor context: {e}")

        # Live token state on the current map. move_token needs a token_id +
        # pixel x,y, and place_token is how new creatures/objects appear — without
        # this block the LLM only knows actor names and never touches the board,
        # so the map and tokens end up purely decorative. Queried live each turn
        # because scene-event driven cache population is unreliable for
        # programmatic scene.activate() switches.
        try:
            scene_tokens = await self.foundry.get_scene_tokens()
            tok_lines = []
            for t in scene_tokens or []:
                tid = t.get("id", "")
                if not tid:
                    continue
                disp = t.get("disposition")
                side = "hostile" if (disp is not None and disp < 0) else "friendly/neutral"
                tok_lines.append(
                    f"- {t.get('name', '?')} [token_id: {tid}] {side} "
                    f"at ({int(t.get('x', 0))}, {int(t.get('y', 0))})"
                )
            if tok_lines:
                parts.append(
                    "## TOKENS ON THE CURRENT MAP (grid 100px = 5ft)\n"
                    + "\n".join(tok_lines)
                    + "\n\nWhen a creature moves, emit move_token with its token_id and the "
                    "new pixel x,y. If you narrate a creature, enemy, or interactable object "
                    "that is NOT listed above, FIRST place_token for it (disposition -1 for "
                    "enemies) so it appears on the map and can be targeted."
                )
        except Exception as e:
            logger.debug(f"Failed to get scene tokens for context: {e}")

        # Encounter briefs for the current scene
        enc_context = self.state_tracker.get_encounter_context()
        if not enc_context and self._campaign_loader:
            # Fallback: query loader directly using current scene name
            current_scene = self.state_tracker.state.current_scene
            enc_context = self._campaign_loader.get_encounter_context_for_scene(current_scene)
        if enc_context:
            parts.append(enc_context)

        # Scene-authored mood, written by the campaign generator into
        # flags["ai-gm"].atmosphere (e.g. "dusty, silent, heavy with history"
        # for a dungeon room). Nothing previously read this back — the LLM
        # only ever saw the generic weather/time-of-day simulation below,
        # which defaults to "Full daylight... noon, clear" regardless of
        # scene. Confirmed live: an indoor vault scene got narrated with
        # "the light of the high sun cuts harsh shadows" because both lines
        # were injected and the LLM blended them. The authored mood is
        # scene-specific ground truth; the weather/time sim is a global
        # default nobody has set for this scene, so it takes priority and
        # the generic line is suppressed when it's present to avoid the
        # contradiction rather than feed the LLM both.
        authored_atmosphere = None
        try:
            scene_details = await self.foundry.get_scene_details()
            ai_gm_flags = ((scene_details or {}).get("data", {}) or {}).get("flags", {}).get("ai-gm", {})
            authored_atmosphere = ai_gm_flags.get("atmosphere")
            if authored_atmosphere:
                parts.append(f"Scene mood: {authored_atmosphere}")
        except Exception as e:
            logger.debug(f"Failed to get scene-authored atmosphere: {e}")

        # Add current immersion state (Tier 6) — generic weather/time simulation.
        # Skipped when the scene has its own authored mood (see above).
        if self._ambient_manager and not authored_atmosphere:
            try:
                atmosphere = self._ambient_manager.get_atmosphere_description()
                if atmosphere:
                    parts.append(f"Atmosphere: {atmosphere}")
            except Exception as e:
                logger.debug(f"Failed to get atmosphere: {e}")

        return "\n\n".join(parts) if parts else "No NPC context available."

    async def _notify_llm_of_failures(self, results: list) -> list:
        """If any actions failed, send a corrective message to the LLM and return retry results."""
        failed = [
            {"type": r.get("type"), "error": r.get("error")}
            for r in results
            if not r.get("success") and r.get("error")
        ]
        if not failed:
            return []
        logger.warning(f"[Actions] {len(failed)} failed — notifying LLM for retry")

        # If start_encounter failed, suppress retry entirely — combat cannot
        # proceed without tokens on the scene. A retry would just repeat the
        # same broken combat narration. Let the failure surface to the player
        # and wait for the next player turn.
        encounter_failed = any(f.get("type") == "start_encounter" for f in failed)
        place_failed = any(f.get("type") == "place_token" for f in failed)
        if encounter_failed:
            logger.warning("[Actions] start_encounter failed — suppressing combat retry to prevent phantom combat")
            # Inject a hard stop into LLM history so next turn knows combat did not start
            try:
                self.llm._conversation_history.append({
                    "role": "user",
                    "content": (
                        "[SYSTEM] COMBAT DID NOT START. start_encounter failed because there are no tokens on the scene. "
                        "Do NOT narrate any combat actions, attacks, or turns. "
                        + (
                            "The place_token actions also failed — the actor names you used do not exist in the world. "
                            "You must ONLY use actor names from the 'Active NPCs/Characters' list. "
                            "To start combat with improvised enemies, call generate_encounter instead. "
                            if place_failed else ""
                        )
                        + "Wait for the player's next message before taking any action."
                    )
                })
                self.llm._conversation_history.append({
                    "role": "assistant",
                    "content": '{"actions": [{"type": "narrate", "text": "The moment hangs suspended — something in the shadows stirs, but fate has not yet committed to violence."}]}'
                })
            except Exception as e:
                logger.warning(f"[Actions] Failed to inject combat-stop into history: {e}")
            return []

        try:
            retry_result = await self.llm.generate(
                user_message=(
                    "[SYSTEM] These actions failed and were NOT applied to the game: "
                    f"{json.dumps(failed)}. "
                    "Re-issue ONLY corrected versions of these specific actions using ONLY actor names "
                    "from the 'Active NPCs/Characters' list in your context. "
                    "Do NOT repeat any narration or dialogue you already gave this turn — "
                    "that text has already been shown to the players. "
                    "Skip any action that cannot be fixed."
                ),
                game_state_summary=self.state_tracker.get_snapshot(),
            )
            # The model often re-emits the whole previous turn, including
            # narration/dialogue that already played. Re-dispatching it makes
            # the same beat speak again (the "staggered" repeat heard in play),
            # so drop any narrate/speak whose text was already delivered.
            retry_actions = await self._drop_redelivered(retry_result.get("actions", []))
            if retry_actions:
                await self._record_actions(retry_actions)
                dispatch_results = await self.dispatcher.execute_batch(retry_actions)
                await self._record_action_resolved_events(dispatch_results, trigger_npcs=False)
                return dispatch_results
        except Exception as e:
            logger.warning(f"[Actions] LLM failure feedback errored: {e}")
        return []

    async def _drop_redelivered(self, actions: list) -> list:
        """Strip narration the players already heard this turn.

        A pure narrate/speak whose text already played is dropped entirely. An
        action that merely *carries* a narrate field (setup_scene/switch_scene)
        still needs retrying for its side effect, so only the already-played
        narrate sub-field is removed and the action itself is kept.
        """
        async with self._sent_messages_lock:
            seen = {msg for msg, _ in self._sent_messages_with_timestamp}
        kept = []
        for action in actions:
            if action.get("type") in ("narrate", "speak"):
                text = action.get("text") or ""
                if text and text[:120] in seen:
                    logger.info(f"[Actions] Dropping re-delivered {action.get('type')} from retry")
                    continue
            else:
                narrate = action.get("narrate") or ""
                if narrate and narrate[:120] in seen:
                    # This action is in the retry set because its side effect
                    # FAILED (setup_scene/switch_scene, etc) — the played narrate
                    # only proves the narrate sub-call landed, not the scene op.
                    # Strip the stale narrate but KEEP the action so the side
                    # effect re-runs; dropping it would leave players on a black
                    # screen / wrong map with the failure never corrected.
                    logger.info(f"[Actions] Stripping re-delivered narration from retry {action.get('type', '')}")
                    action = {k: v for k, v in action.items() if k != "narrate"}
            kept.append(action)
        return kept

    async def _place_referenced_combatants(self, actions: list) -> None:
        """Put enemies the GM rolls for OR narrates onto the map.

        The local model narrates foes appearing ('two towering Revenants', 'a
        horde of skeletons') and rolls for them without ever calling place_token,
        so nothing shows up (the repeated 'no tokens for the attackers' report).
        This reconciler:
          - rolls: every roll speaker matching a world actor is placed (any
            disposition — it is actively acting);
          - narration: HOSTILE world actors named in narrate/speak text are
            placed up to the quantity described (numbers, plurals), so 'six
            skeletons' yields six tokens.
        Existing tokens count toward the target, so it tops up rather than
        duplicating. Foes spread in a ring around the party.
        """
        # Roll speakers (combatants that are acting this turn).
        refs = [str(a["speaker"]).strip() for a in actions
                if a.get("type") == "roll" and a.get("speaker")]
        # Narration / dialogue text describing who is present (original case —
        # _mention_count relies on capitalization to tell a monster name from a
        # common noun).
        text = " ".join(
            str(a.get("text") or a.get("narrate") or "")
            for a in actions
            if a.get("type") in ("narrate", "speak", "setup_scene", "switch_scene")
        )
        if not refs and not text.strip():
            return
        try:
            actors = await self.foundry.get_actors(world_only=True)
            scene_tokens = await self.foundry.get_scene_tokens()
        except Exception as e:
            logger.debug(f"[Tokens] combatant reconcile skipped: {e}")
            return

        on_scene = [str(t.get("name", "")).lower() for t in scene_tokens]

        def existing_count(name: str) -> int:
            nl = name.lower()
            return sum(1 for s in on_scene if s and (nl in s or s in nl))

        # Desired count per world actor: max of roll-reference (1) and the
        # quantity its name is mentioned with in narration.
        desired: dict = {}
        for a in actors:
            nm = a.get("name")
            if not nm:
                continue
            nl = nm.lower()
            want = 0
            if any(nl in r.lower() or r.lower() in nl for r in refs):
                want = 1
            want = max(want, _mention_count(text, nm))
            if want:
                desired[nm] = want
        if not desired:
            return

        disp = await self.foundry.get_actor_dispositions(list(desired.keys()))
        rolled = {r.lower() for r in refs}

        # Anchor near the party so foes appear around them.
        pcs = [t for t in scene_tokens
               if (t.get("disposition") or 0) >= 0 and t.get("x") is not None]
        if pcs:
            ax = int(sum(t.get("x", 0) for t in pcs) / len(pcs))
            ay = int(sum(t.get("y", 0) for t in pcs) / len(pcs))
        else:
            ax, ay = 1200, 1000

        import math
        placed = 0
        for actor_name, want in desired.items():
            d = int(disp.get(actor_name, -1))
            is_rolled = any(actor_name.lower() in r or r in actor_name.lower() for r in rolled)
            # Narration-only mentions: place hostiles only, so an ally named in
            # passing dialogue isn't dropped onto the battlefield.
            if not is_rolled and d >= 0:
                continue
            need = min(want, 6) - existing_count(actor_name)
            for _ in range(max(0, need)):
                if placed >= 10:  # global safety cap per turn
                    break
                ang = placed * (math.pi / 3)
                ring = 240 + 90 * (placed // 6)
                x = max(0, ax + int(ring * math.cos(ang)) + 200)
                y = max(0, ay + int(ring * math.sin(ang)))
                try:
                    await self.foundry.place_token(actor_name, x, y, disposition=d)
                    placed += 1
                except Exception as e:
                    logger.debug(f"[Tokens] auto-place of '{actor_name}' failed: {e}")
            if existing_count(actor_name) or need > 0:
                await self.register_ai_speaker(actor_name)
        if placed:
            logger.info(f"[Tokens] Auto-placed {placed} narrated/rolled combatant token(s) near the party")

    async def _handle_generated_npcs(self, results: list) -> None:
        """Register newly generated NPCs in the personality registry (Tier 5 integration)."""
        if not self._npc_registry or not self._personality_engine:
            return

        for result in results:
            if not isinstance(result, dict):
                continue

            # Handle generated NPCs from procedural generation
            if result.get("type") == "generate_npc" and result.get("npc"):
                npc_data = result["npc"]
                npc_name = npc_data.get("name", "Unknown NPC")
                try:
                    # Register the generated NPC in the personality system
                    description = f"{npc_data.get('description', '')} ({npc_data.get('class', 'Commoner')} {npc_data.get('race', 'Human')})"
                    personality_result = self._personality_engine.extract_traits(description)

                    self._npc_registry.register_npc(
                        npc_id=npc_name,
                        name=npc_name,
                        npc_class=npc_data.get("class", "Commoner"),
                        level=npc_data.get("level", 1),
                        alignment=npc_data.get("alignment", "Neutral"),
                    )
                    logger.info(f"[Tier 5] Registered generated NPC '{npc_name}' in personality system")
                except Exception as e:
                    logger.warning(f"Failed to register generated NPC '{npc_name}': {e}")

            # Handle generated encounters
            elif result.get("type") == "generate_encounter" and result.get("encounter"):
                encounter = result["encounter"]
                logger.info(f"[Tier 5] Generated encounter: {encounter.get('name', 'Unknown')} ({encounter.get('difficulty', 'unknown')})")

            # Handle generated treasure
            elif result.get("type") == "generate_treasure" and result.get("treasure"):
                treasure = result["treasure"]
                logger.info(f"[Tier 5] Generated treasure worth {treasure.get('total_value_gp', 0)}gp")

            # Handle generated quests
            elif result.get("type") == "generate_quest" and result.get("quest"):
                quest = result["quest"]
                logger.info(f"[Tier 5] Generated quest: {quest.get('title', 'Unknown')} ({quest.get('difficulty', 'unknown')})")

    async def _update_immersion_state(self, results: list) -> None:
        """Update immersion state based on executed actions (Tier 6 integration)."""
        if not self._ambient_manager and not self._effects_manager and not self._vision_manager:
            return

        for result in results:
            if not isinstance(result, dict) or not result.get("success"):
                continue

            # Handle weather changes
            if result.get("type") == "set_weather" and self._ambient_manager:
                logger.info("[Tier 6] Weather updated via set_weather action")

            # Handle time changes
            elif result.get("type") == "set_time" and self._ambient_manager:
                logger.info("[Tier 6] Time updated via set_time action")

            # Handle token effects
            elif result.get("type") == "apply_token_effect" and self._effects_manager:
                logger.info(f"[Tier 6] Token effect applied: {result.get('effect_name', 'unknown')}")

            # Handle vision updates
            elif result.get("type") == "update_vision" and self._vision_manager:
                logger.info(f"[Tier 6] Vision updated for token: {result.get('token_id', 'unknown')}")

    # --- GM pacing helpers ---

    # Idle-nudge content variety — rotated so consecutive nudges don't all
    # reach for the same beat (the "generic content" complaint: every idle
    # nudge defaulting to the same vague "an NPC reacts" instruction).
    _IDLE_BEAT_STYLES = [
        ("npc_reaction", "Have a specific NPC already in the scene speak, react, or interrupt."),
        ("sensory_detail", "Introduce a new sensory detail unique to the current location — a sound, smell, or sight not yet mentioned."),
        ("time_pressure", "Create a ticking-clock moment tied to the current situation — something that demands a decision soon."),
        ("foreshadow", "Drop a hint of approaching danger or a hook connected to the campaign's stakes."),
    ]

    def _pick_idle_beat_style(self) -> str:
        """Pick an idle-nudge instruction, avoiding the last two styles used."""
        choices = [s for s in self._IDLE_BEAT_STYLES if s[0] not in self._recent_idle_styles]
        if not choices:
            choices = self._IDLE_BEAT_STYLES
        name, instruction = random.choice(choices)
        self._recent_idle_styles.append(name)
        return instruction

    def _reset_idle_timer(self, extra_delay: float = 0.0, _escalate: bool = False):
        """Cancel any existing idle countdown and start a fresh one.

        extra_delay: additional seconds to add (e.g. TTS audio duration) so
        pacing nudges don't fire while narration is still playing.

        _escalate: internal — set by _idle_countdown when re-arming after a
        nudge that got no player response. Every other caller means genuine
        activity (a player message, session start, unpause), which resets
        the backoff to the fast baseline.
        """
        if not _escalate:
            self._consecutive_idle_beats = 0
        self._cancel_idle_timer()
        base = getattr(settings, "gm_idle_timeout", 30)
        # Back off on consecutive unanswered nudges (base, 1.6x, 2.56x, ...)
        # capped at 4x base — fast to speak up on a genuine lull, but stops
        # nagging a table that's stepped away without going silent forever.
        timeout = min(base * (1.6 ** self._consecutive_idle_beats), base * 4) + extra_delay
        if timeout > 0:
            self._idle_timer_task = asyncio.create_task(self._idle_countdown(timeout))

    def _cancel_idle_timer(self):
        if self._idle_timer_task and not self._idle_timer_task.done():
            self._idle_timer_task.cancel()
        self._idle_timer_task = None

    async def _idle_countdown(self, timeout: float):
        """Sleep then fire a proactive GM action if no player message arrived."""
        try:
            await asyncio.sleep(timeout)
            session_id = await self.db.get_active_session()
            if not (session_id and self._running):
                return
            # Don't fire pacing nudges during active combat — the combat loop
            # handles its own pacing and an idle nudge would break turn order.
            in_combat = (
                hasattr(self.state_tracker, "state") and
                str(getattr(self.state_tracker.state, "mode", "")).lower() == "combat"
            )
            if not in_combat:
                # _process_proactive_action drops the idle beat itself if a turn
                # is already in flight, so we don't re-check the lock here.
                logger.info(f"[Pacing] {timeout:.0f}s idle — evaluating proactive GM action")
                await self._process_proactive_action(reason="idle")
            # Reaching here means the full timeout elapsed without a player
            # message cancelling this task (that path calls _reset_idle_timer
            # itself, which cancels and replaces this task) — genuine silence
            # just occurred, whether or not the beat above actually fired
            # (it may have been skipped for combat or an in-flight turn).
            # Count it toward the backoff either way.
            self._consecutive_idle_beats += 1
            self._reset_idle_timer(_escalate=True)
        except asyncio.CancelledError:
            pass

    # Minimum gap between any two proactive beats (idle or pacing-interval),
    # so a pacing-check landing right after an idle nudge doesn't double-nudge.
    _MIN_PROACTIVE_GAP = 15.0

    async def _process_proactive_action(self, reason: str = "idle"):
        """Ask the LLM to advance the scene without waiting for a player message."""
        # Self-initiated beats must never overlap a turn that is already running
        # (a player response or another beat) — that is what produced duplicate
        # narrations. An idle nudge is pointless if a turn is already underway,
        # so it is dropped. A pacing check and session_start are deliberate and
        # wait for the lock so they fire once the current turn finishes. The
        # locked() check and the early return are not separated by an await, so
        # no idle beat can slip through after the check.
        if reason == "idle" and self._turn_lock.locked():
            logger.debug("[Pacing] Skipping idle beat — a turn is already in flight")
            return
        # session_start is a one-time critical beat and is never subject to
        # the anti-stacking gap.
        if reason in ("idle", "pacing"):
            since_last = time.monotonic() - self._last_proactive_beat_at
            if since_last < self._MIN_PROACTIVE_GAP:
                logger.debug(f"[Pacing] Skipping {reason} beat — another fired {since_last:.0f}s ago")
                return
        async with self._turn_lock:
            await self._run_proactive_action(reason)
            self._last_proactive_beat_at = time.monotonic()

    async def _run_proactive_action(self, reason: str, _retried: bool = False):
        """Body of a proactive beat; the caller holds self._turn_lock."""
        try:
            game_state = self.state_tracker.get_snapshot()
            extra_context = await self._get_npc_context()
            if self._scene_awareness:
                scene_summary = self._scene_awareness.get_context_summary()
                if scene_summary:
                    extra_context += f"\n\n## SCENE\n{scene_summary}"

            _live_scenes = ""
            prologue_summary = ""
            if reason == "session_start":
                prologue_entry = None
                try:
                    prologue_entry = await load_prologue_entry(self.foundry)
                except Exception as e:
                    logger.warning(f"[Pacing] Could not load prologue journal: {e}")
                if prologue_entry:
                    prologue_summary = describe_prologue(prologue_entry)
                    interrupt_event = asyncio.Event()
                    setattr(self.foundry, "_prologue_interrupt_event", interrupt_event)
                    try:
                        await present_prologue(
                            self.foundry,
                            lambda text: self.foundry.chat_message(text, speaker="GM"),
                            prologue_entry["uuid"],
                            interrupt_event=interrupt_event,
                            entry=prologue_entry,
                        )
                    finally:
                        if getattr(self.foundry, "_prologue_interrupt_event", None) is interrupt_event:
                            delattr(self.foundry, "_prologue_interrupt_event")

                # Pull live world data so the LLM knows the active scene and
                # which player actors to place. Failures are non-fatal.
                _live_scene = ""
                _live_actors = ""
                _slist = []  # must exist even if the scenes query below fails
                try:
                    _sjs = (
                        "const s=canvas?.scene;"
                        "return s ? {name:s.name,bg:s.background?.src||s.img||''} : null;"
                    )
                    _sres = await self.foundry.execute_js(_sjs)
                    _sd = (_sres.get("result") or {}) if isinstance(_sres, dict) else {}
                    if _sd.get("name"):
                        _bg = _sd.get("bg", "")
                        if _bg:
                            _live_scene = f"Active scene: {_sd['name']}. Background image: {_bg}"
                        else:
                            _live_scene = (
                                f"Active scene: {_sd['name']}. "
                                "Background image: NONE — the players see a black screen. "
                                "You MUST call setup_scene with background_src set to a Foundry asset path "
                                "(e.g. 'worlds/valenthal/maps/gatehouse.webp') or call generate_map."
                            )
                except Exception:
                    pass
                _live_scenes = ""
                try:
                    _scenes_js = (
                        "return game.scenes.map(s=>({name:s.name,active:s.active}));"
                    )
                    _slist_res = await self.foundry.execute_js(_scenes_js)
                    _slist = (_slist_res.get("result") or []) if isinstance(_slist_res, dict) else []
                    if _slist:
                        _scene_names = ", ".join(
                            f"\"{s['name']}\"{' (ACTIVE)' if s.get('active') else ''}"
                            for s in _slist if s.get("name")
                        )
                        _live_scenes = f"Available Foundry scenes (all have maps): {_scene_names}"
                except Exception:
                    pass
                try:
                    actors = await self.foundry.get_actors()
                    pcs = [a for a in actors if a.get("has_player_owner")]
                    if pcs:
                        _live_actors = "Player characters to place on the map: " + ", ".join(
                            f"{a['name']} (uuid={a['uuid']})" for a in pcs
                        )
                except Exception:
                    pass

                _live_info = "\n".join(filter(None, [_live_scene, _live_scenes, _live_actors]))
                # Identify the Act 1 starting scene from the available scenes list
                # (first scene alphabetically that contains "Monastery" or "Act 1" or the
                # very first scene if none match — avoids jumping to later acts)
                _act1_hint = ""
                if _slist:
                    _act1_candidates = [
                        s["name"] for s in _slist
                        if any(kw in s.get("name", "") for kw in ("Monastery", "Act 1", "Courtyard", "Entrance", "Start"))
                    ]
                    if _act1_candidates:
                        _act1_hint = (
                            f"\n\nSTARTING LOCATION: This is the beginning of the campaign — Act 1. "
                            f"Switch to and narrate from \"{_act1_candidates[0]}\" as the opening scene. "
                            "Do NOT skip ahead to later acts or locations."
                        )
                    else:
                        # Fall back to whichever scene is currently active, or the first listed
                        _first = next((s["name"] for s in _slist if s.get("active")), None) or _slist[0]["name"]
                        _act1_hint = (
                            f"\n\nSTARTING LOCATION: This is the beginning of the campaign — Act 1. "
                            f"Begin at \"{_first}\" and narrate the opening from there. "
                            "Do NOT skip ahead to later acts or locations."
                        )
                _prologue_prompt = ""
                if prologue_summary:
                    _prologue_prompt = (
                        "The players have just witnessed the prologue (summary below). "
                        "Open the first scene flowing from its final panel — do NOT re-summarize the history.\n"
                        f"{prologue_summary}\n\n"
                    )
                prompt = (
                    "[SESSION OPENING]\n"
                    + _prologue_prompt
                    + (_live_info + "\n\n" if _live_info else "")
                    + "A new session has just started. Your REQUIRED opening sequence:\n"
                    "1. Call `setup_scene` to configure the current map (set darkness, fog_exploration=false, "
                    "tokenVision=false, global_illumination=true for outdoors/well-lit; "
                    "global_illumination=false + darkness=0.6-0.8 for dungeons/night). "
                    "IMPORTANT: tokenVision must always be false — the Levels module handles vision. "
                    "Include a vivid `narrate` field in setup_scene to describe the location aloud.\n"
                    "2. Call `place_token` for EACH player character listed above so they appear on the map.\n"
                    "3. Optionally have a key NPC `speak` to draw the players into the scene.\n"
                    "Do all of this in a single JSON response. Do NOT skip setup_scene or place_token.\n"
                    "The scene displayed MUST match the story location. Use switch_scene to change to any "
                    "available scene as the story moves between locations — do NOT generate new maps, "
                    "all locations already have scenes."
                    + _act1_hint
                )
            elif reason == "idle":
                style_instruction = self._pick_idle_beat_style()
                prompt = (
                    "[GM PACING — NO PLAYER INPUT RECEIVED] "
                    "The players have been silent. Add a NEW beat — do NOT re-describe the opening scene or repeat narration already given. "
                    f"{style_instruction} "
                    "Ground it in something specific from the scene/NPC context below — a named NPC, an object, "
                    "a detail already established — not a generic aside. Keep it SHORT (1-2 sentences). "
                    "Do NOT call setup_scene or switch_scene unless the story has explicitly moved to a new location. "
                    "Do NOT wait for a player response — drive the narrative forward with a single narrate or speak action."
                )
            else:
                prompt = (
                    "[GM PACING CHECK] "
                    f"After {self._player_message_count} player exchanges, evaluate whether "
                    "the scene is stalling. If players are circling the same topic or "
                    "not making progress, escalate: an NPC interrupts, a complication "
                    "arrives, or the environment changes. If the scene is progressing "
                    "well, issue a brief atmospheric beat to maintain immersion."
                )

            result = await self.llm.generate(
                user_message=prompt,
                game_state_summary=game_state,
                extra_context=extra_context
            )

            actions = result.get("actions", [])
            if actions:
                await self._record_actions(actions)
                dispatch_results = await self.dispatcher.execute_batch(actions)
                await self._record_action_resolved_events(dispatch_results, trigger_npcs=False)
                logger.info(f"[Pacing] Proactive GM ({reason}): {len(actions)} actions executed")

                if self._on_results_callback:
                    await self._on_results_callback(dispatch_results)

        except Exception as e:
            logger.error(f"[Pacing] Error in proactive GM action ({reason}): {e}", exc_info=True)
            # A lost idle nudge is harmless, but a lost session opening leaves
            # the table with no scene, no tokens, and no narration. Retry once.
            if reason == "session_start" and not _retried:
                logger.info("[Pacing] Retrying session opening once…")
                await asyncio.sleep(5)
                await self._run_proactive_action(reason, _retried=True)

    async def sync_active_scene(self):
        """Resolve Foundry's actual active scene and push it into state_tracker/scene_awareness.

        Foundry's canvas doesn't emit a scene-change event just because a session
        started — it only fires when someone actually switches scenes. If the
        world is already sitting on a scene (the normal case for every session
        start except the very first), current_scene would stay "" until a human
        GM manually switches away and back, silently dropping encounter-brief
        lookup (get_encounter_context_for_scene) and scene awareness for the
        scene players are already standing in. Call this at every session-start
        path instead of waiting for an event that may never come.
        """
        if not self.state_tracker:
            return
        active_scene = await self.foundry._get_active_scene_name()
        if active_scene:
            await self.state_tracker.set_scene(active_scene)
            if self._scene_awareness:
                await self._scene_awareness.on_scene_change(active_scene)
            logger.info(f"[Session] Synced active scene on start: {active_scene}")

    async def _cmd_start_session(self, campaign_name: str):
        """Handle '/gm start session [name]' — activate the AI GM for this session."""
        import uuid
        existing = await self.db.get_active_session()
        if existing:
            await self.foundry.chat_message(
                f"A session is already active (ID: {existing[:8]}…). "
                "Use /gm pause ai or /gm stop combat to reset if needed.",
                speaker="GM"
            )
            return

        session_id = str(uuid.uuid4())
        await self.db.create_session(session_id, campaign_name)
        self._player_message_count = 0
        self._reset_idle_timer()

        # Rehydrate NPC goals/relationships persisted at the end of this
        # campaign's last session (npc_persistence.save() in "/gm end
        # session" below) — without this, npc/persistence.py's whole
        # save/load round trip has no effect, since the registry is
        # otherwise always created fresh at boot (main.py) with nothing
        # ever reading these rows back. Update the existing registry
        # in place (not a new instance) since other components hold the
        # same reference (main.py's app.state, personality engine, TTS).
        if self._npc_registry:
            try:
                loaded = await npc_persistence.load(self.db, campaign_name)
                if loaded.npcs:
                    self._npc_registry.npcs = loaded.npcs
                    self._npc_registry.relationships = loaded.relationships
            except Exception as e:
                logger.warning(f"[Session] Failed to load persisted NPC records for '{campaign_name}': {e}")

        # Reset scene state so the GM starts from Act 1, not a previous session's location
        if self.state_tracker:
            self.state_tracker.state.scene_data = {}
            self.state_tracker.state.current_scene = ""
            self.state_tracker.state.npc_context = ""
            self.state_tracker.state.encounter_context = ""
            await self.state_tracker.save()

            await self.sync_active_scene()

        await self.foundry.chat_message(
            f"🎲 **Session started** — *{campaign_name}*. The AI GM is now active.",
            speaker="GM"
        )
        logger.info(f"[Session] Started session {session_id} for campaign '{campaign_name}'")

        # Opening narration
        await self._process_proactive_action(reason="session_start")

    # --- Callbacks ---
    _on_results_callback: Optional[Callable] = None
    _on_scene_change_callback: Optional[Callable] = None

    def set_results_callback(self, callback: Callable):
        self._on_results_callback = callback

    def set_scene_change_callback(self, callback: Callable):
        self._on_scene_change_callback = callback
