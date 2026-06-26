"""
Chat Listener — subscribes to Foundry chat events and processes player messages.
Integrates with combat loop and scene awareness.
"""

import asyncio
import json
import logging
from typing import Any, Callable, Optional

from foundry.client import FoundryClient
from llm.manager import LLMManager
from actions.dispatcher import ActionDispatcher
from state.tracker import GameStateTracker
from persistence.db import Database
from config import settings

logger = logging.getLogger(__name__)


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
    ):
        self.foundry = foundry
        self.llm = llm
        self.dispatcher = dispatcher
        self.state_tracker = state_tracker
        self.db = db
        self._campaign_loader = campaign_loader
        self._combat_loop = combat_loop
        self._scene_awareness = scene_awareness
        self._reinforcement_mgr = reinforcement_mgr
        self._npc_registry = npc_registry
        self._personality_engine = personality_engine
        self._ambient_manager = ambient_manager
        self._effects_manager = effects_manager
        self._vision_manager = vision_manager
        self._running = False
        self._pending_ai_message: Optional[asyncio.Future] = None
        self._last_turn_token: Optional[str] = None
        self._ai_controlled_speakers: set = {
            settings.ai_name,
            self.foundry._ai_name if foundry and foundry._ai_name else settings.ai_name
        }
        # Recently sent message texts — used to suppress relay echoes of our own output.
        # Stores the first 120 chars of each sent message; cleared after 10 entries.
        self._sent_messages: list = []
        # GM pacing state
        self._idle_timer_task: Optional[asyncio.Task] = None
        self._player_message_count: int = 0

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

        self._reset_idle_timer()
        logger.info("Chat listener started — listening for player messages")

    async def stop(self):
        """Stop listening."""
        self._running = False
        self._cancel_idle_timer()
        if self._combat_loop:
            await self._combat_loop.stop()
        logger.info("Chat listener stopped")

    def _is_player_message(self, msg: dict) -> bool:
        """Determine if a chat message is from a player (not from GM/AI)."""
        speaker = msg.get("speaker", "")

        # Exclude empty/blank messages (no content and no speaker)
        content = msg.get("message", msg.get("content", ""))
        if not content and not speaker:
            return False

        # Exclude system messages
        if msg.get("type") == "system":
            return False

        # Exclude whispered messages (check for both "whisper" and "whisper_to")
        if msg.get("whisper") or msg.get("whisper_to"):
            return False

        # Exclude messages from AI-controlled speakers (including NPCs we've created)
        if speaker in self._ai_controlled_speakers or speaker == "GM":
            return False

        # Suppress relay echoes of messages we just sent.
        # The relay broadcasts every chat-send back to all subscribers including us.
        content = msg.get("message", msg.get("content", ""))
        if content:
            snippet = content[:120]
            if snippet in self._sent_messages:
                return False

        return True

    def register_ai_speaker(self, speaker_name: str):
        """Register a speaker as AI-controlled (NPC, narration, etc) to prevent self-triggering."""
        if speaker_name:
            self._ai_controlled_speakers.add(speaker_name)

    def _record_sent(self, text: str):
        """Track a message we're about to send so its echo can be suppressed."""
        self._sent_messages.append(text[:120])
        if len(self._sent_messages) > 20:
            self._sent_messages.pop(0)

    async def _handle_chat_event(self, data: dict):
        """Process incoming chat events from Foundry."""
        try:
            content = data.get("message", data.get("content", ""))
            if not content:
                content = data.get("data", {}).get("message", "")

            speaker = data.get("speaker", data.get("data", {}).get("speaker", ""))
            whisper_to = data.get("whisper_to", data.get("data", {}).get("whisper_to", []))

            # Don't respond to anything if no session is active — prevents the AI
            # from narrating during campaign setup, deploy, or while idle.
            session_id = await self.db.get_active_session()
            if not session_id:
                return

            # Skip non-player messages
            if not self._is_player_message(data):
                return

            logger.info(f"Chat message from {speaker}: {content[:100]}")

            # Check for GM commands (handled even while paused so the
            # AI can be resumed via "/gm resume ai")
            if content.startswith("/gm ") or content.startswith("/ask ") or content.strip() == "/ask":
                await self._handle_gm_command(speaker, content)
                return

            # Respect the pause flag for normal player messages
            if not self._running:
                return

            # Player is active — reset the idle countdown
            self._reset_idle_timer()
            self._player_message_count += 1

            # Get game state snapshot
            game_state = self.state_tracker.get_snapshot()

            # Build context
            extra_context = await self._get_npc_context()
            if self._scene_awareness:
                scene_summary = self._scene_awareness.get_context_summary()
                if scene_summary:
                    extra_context += f"\n\n## SCENE\n{scene_summary}"

            # If in combat, route through combat loop
            if self.state_tracker.state.mode == "combat" and self._combat_loop and self._combat_loop.is_running:
                # During combat, let the combat loop handle NPC turns
                # But we still process player input
                await self._process_combat_input(content, speaker)
            else:
                await self._process_normal_input(content, speaker, game_state, extra_context)

        except Exception as e:
            logger.error(f"Error handling chat event: {e}", exc_info=True)
            await self.foundry.chat_message(
                "*The GM takes a moment to collect their thoughts…*",
                speaker="GM"
            )

    async def _process_normal_input(self, content: str, speaker: str, game_state: str, extra_context: str):
        """Process a normal (non-combat) player message."""
        try:
            result = await self.llm.generate(
                user_message=f"[{speaker}]: {content}",
                game_state_summary=game_state,
                extra_context=extra_context
            )

            actions = result.get("actions", [])
            results = []
            if actions:
                # Record outgoing text BEFORE dispatch so echoes are suppressed
                # when the relay bounces them back through chat-events.
                for action in actions:
                    if action.get("type") == "narrate" and action.get("text"):
                        self._record_sent(action["text"])
                    elif action.get("type") == "speak" and action.get("text"):
                        self._record_sent(action["text"])
                        if action.get("npc_name"):
                            self.register_ai_speaker(action["npc_name"])

                results = await self.dispatcher.execute_batch(actions)

                # Register NPC speakers to prevent self-triggering
                for action in actions:
                    if action.get("type") == "speak" and action.get("npc_name"):
                        self.register_ai_speaker(action.get("npc_name"))

                # Handle generated NPCs (Tier 5 integration)
                await self._handle_generated_npcs(results)

                # Update immersion state after actions (Tier 6 integration)
                await self._update_immersion_state(results)

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
                asyncio.create_task(self._process_proactive_action(reason="pacing"))

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
            if self._scene_awareness:
                scene_summary = self._scene_awareness.get_context_summary()
                if scene_summary:
                    extra_context += f"\n\n## SCENE\n{scene_summary}"

            result = await self.llm.generate(
                user_message=f"[{speaker}]: {content}",
                game_state_summary=game_state,
                extra_context=extra_context
            )

            actions = result.get("actions", [])
            results = []
            if actions:
                for action in actions:
                    if action.get("type") == "narrate" and action.get("text"):
                        self._record_sent(action["text"])
                    elif action.get("type") == "speak" and action.get("text"):
                        self._record_sent(action["text"])
                        if action.get("npc_name"):
                            self.register_ai_speaker(action["npc_name"])

                results = await self.dispatcher.execute_batch(actions)

                # Register NPC speakers to prevent self-triggering
                for action in actions:
                    if action.get("type") == "speak" and action.get("npc_name"):
                        self.register_ai_speaker(action.get("npc_name"))

                # Handle generated NPCs (Tier 5 integration)
                await self._handle_generated_npcs(results)

                # Update immersion state after actions (Tier 6 integration)
                await self._update_immersion_state(results)

                logger.info(f"[Combat] Executed {len(actions)} actions for {speaker}")

            # Signal the combat loop to advance to the next turn
            if self._combat_loop and self._combat_loop.is_running:
                self._combat_loop.advance_pc_turn()

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
                "/gm start combat — start combat loop\n"
                "/gm stop combat — stop combat loop\n"
                "/gm pause ai — pause AI processing\n"
                "/gm resume ai — resume AI processing",
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
        else:
            await self.foundry.chat_message(
                f"Unknown command: {command}. Use /gm help.",
                speaker="GM"
            )

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
        asyncio.create_task(self._combat_loop.start_combat_loop(scene_tokens))

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
            logger.error(f"Error handling roll event: {e}")

    async def _handle_combat_event(self, data: dict):
        """Handle combat events from Foundry."""
        try:
            combat_data = data.get("data", data)
            event_type = combat_data.get("type", data.get("type", ""))

            if event_type == "start" or data.get("type") == "encounter-started":
                await self.state_tracker.set_mode("combat")
                await self.state_tracker.update_combat(in_combat=True)

                # Start auto-combat loop if configured
                if self._combat_loop:
                    scene_tokens = await self.foundry.get_scene_tokens()
                    if scene_tokens:
                        # Launch combat loop as a background task to avoid blocking the reader
                        asyncio.create_task(self._combat_loop.start_combat_loop(scene_tokens))
                        await self.foundry.chat_message(
                            "⚔️ AI combat loop started.",
                            speaker="GM"
                        )

                logger.info("[State] Combat started")

            elif event_type == "end" or data.get("type") == "encounter-ended":
                await self.state_tracker.set_mode("exploration")
                await self.state_tracker.update_combat(in_combat=False)
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
            logger.error(f"Error handling combat event: {e}")

    async def _handle_scene_event(self, data: dict):
        """Handle scene change events."""
        try:
            scene_name = data.get("sceneName", data.get("data", {}).get("sceneName", ""))
            if scene_name:
                await self.state_tracker.set_scene(scene_name)
                logger.info(f"[State] Scene changed to: {scene_name}")

                # Update scene awareness
                if self._scene_awareness:
                    await self._scene_awareness.on_scene_change(scene_name)
        except Exception as e:
            logger.error(f"Error handling scene event: {e}")

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
            logger.error(f"Error handling hook event ({hook}): {e}")

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
                    actor_lines.append(
                        f"- {actor_name} "
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

        # Encounter briefs for the current scene
        enc_context = self.state_tracker.get_encounter_context()
        if not enc_context and self._campaign_loader:
            # Fallback: query loader directly using current scene name
            current_scene = self.state_tracker.state.current_scene
            enc_context = self._campaign_loader.get_encounter_context_for_scene(current_scene)
        if enc_context:
            parts.append(enc_context)

        # Add current immersion state (Tier 6)
        if self._ambient_manager:
            try:
                atmosphere = self._ambient_manager.get_atmosphere_description()
                if atmosphere:
                    parts.append(f"Atmosphere: {atmosphere}")
            except Exception as e:
                logger.debug(f"Failed to get atmosphere: {e}")

        return "\n\n".join(parts) if parts else "No NPC context available."

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

    def _reset_idle_timer(self):
        """Cancel any existing idle countdown and start a fresh one."""
        self._cancel_idle_timer()
        timeout = getattr(settings, "gm_idle_timeout", 120)
        if timeout > 0:
            self._idle_timer_task = asyncio.create_task(self._idle_countdown(timeout))

    def _cancel_idle_timer(self):
        if self._idle_timer_task and not self._idle_timer_task.done():
            self._idle_timer_task.cancel()
        self._idle_timer_task = None

    async def _idle_countdown(self, timeout: int):
        """Sleep then fire a proactive GM action if no player message arrived."""
        try:
            await asyncio.sleep(timeout)
            session_id = await self.db.get_active_session()
            if session_id and self._running:
                logger.info(f"[Pacing] {timeout}s idle — triggering proactive GM action")
                await self._process_proactive_action(reason="idle")
                # Re-arm the timer so the GM keeps nudging during extended silence
                self._reset_idle_timer()
        except asyncio.CancelledError:
            pass

    async def _process_proactive_action(self, reason: str = "idle"):
        """Ask the LLM to advance the scene without waiting for a player message."""
        try:
            game_state = self.state_tracker.get_snapshot()
            extra_context = await self._get_npc_context()
            if self._scene_awareness:
                scene_summary = self._scene_awareness.get_context_summary()
                if scene_summary:
                    extra_context += f"\n\n## SCENE\n{scene_summary}"

            if reason == "session_start":
                # Pull live world data so the LLM knows the active scene and
                # which player actors to place. Failures are non-fatal.
                _live_scene = ""
                _live_actors = ""
                try:
                    _sjs = (
                        "const s=canvas?.scene;"
                        "return s ? {name:s.name,hasBackground:!!(s.background?.src||s.img)} : null;"
                    )
                    _sres = await self.foundry.execute_js(_sjs)
                    _sd = (_sres.get("result") or {}) if isinstance(_sres, dict) else {}
                    if _sd.get("name"):
                        _live_scene = (
                            f"Active scene: {_sd['name']}. "
                            f"Background image: {'present' if _sd.get('hasBackground') else 'none — you must call generate_map or setup_scene to give it a visual'}."
                        )
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

                _live_info = "\n".join(filter(None, [_live_scene, _live_actors]))
                prompt = (
                    "[SESSION OPENING]\n"
                    + (_live_info + "\n\n" if _live_info else "")
                    + "A new session has just started. Your REQUIRED opening sequence:\n"
                    "1. Call `setup_scene` to configure the current map (set darkness, fog_exploration=true, "
                    "tokenVision=true, global_illumination=false for anything indoors or night; "
                    "add walls/lights if the scene has no background image, generate one with `generate_map`). "
                    "Include a vivid `narrate` field in setup_scene to describe the location aloud.\n"
                    "2. Call `place_token` for EACH player character listed above so they appear on the map.\n"
                    "3. Optionally have a key NPC `speak` to draw the players into the scene.\n"
                    "Do all of this in a single JSON response. Do NOT skip setup_scene or place_token — "
                    "players are currently looking at a black screen."
                )
            elif reason == "idle":
                prompt = (
                    "[GM PACING — NO PLAYER INPUT RECEIVED] "
                    "The players have been silent. Advance the scene: add atmosphere, "
                    "have an NPC speak or act, introduce a sensory detail, hint at "
                    "approaching danger, or create a time-pressure moment. "
                    "Do NOT wait for a player response — drive the narrative forward."
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
                for action in actions:
                    if action.get("type") == "narrate" and action.get("text"):
                        self._record_sent(action["text"])
                    elif action.get("type") == "speak" and action.get("text"):
                        self._record_sent(action["text"])
                        if action.get("npc_name"):
                            self.register_ai_speaker(action["npc_name"])

                results = await self.dispatcher.execute_batch(actions)
                logger.info(f"[Pacing] Proactive GM ({reason}): {len(actions)} actions executed")

                if self._on_results_callback:
                    await self._on_results_callback(results)

        except Exception as e:
            logger.error(f"[Pacing] Error in proactive GM action: {e}", exc_info=True)

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
