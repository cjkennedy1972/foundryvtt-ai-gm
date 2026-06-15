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
        self._running = False
        self._pending_ai_message: Optional[asyncio.Future] = None
        self._last_turn_token: Optional[str] = None
        self._ai_controlled_speakers: set = {
            settings.ai_name,
            self.foundry._ai_name if foundry and foundry._ai_name else settings.ai_name
        }

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

        logger.info("Chat listener started — listening for player messages")

    async def stop(self):
        """Stop listening."""
        self._running = False
        if self._combat_loop:
            await self._combat_loop.stop()
        logger.info("Chat listener stopped")

    def _is_player_message(self, msg: dict) -> bool:
        """Determine if a chat message is from a player (not from GM/AI)."""
        speaker = msg.get("speaker", "")

        # Exclude system messages
        if msg.get("type") == "system":
            return False

        # Exclude whispered messages (check for both "whisper" and "whisper_to")
        if msg.get("whisper") or msg.get("whisper_to"):
            return False

        # Exclude messages from AI-controlled speakers (including NPCs we've created)
        if speaker in self._ai_controlled_speakers or speaker == "GM":
            return False

        return True

    def register_ai_speaker(self, speaker_name: str):
        """Register a speaker as AI-controlled (NPC, narration, etc) to prevent self-triggering."""
        if speaker_name:
            self._ai_controlled_speakers.add(speaker_name)

    async def _handle_chat_event(self, data: dict):
        """Process incoming chat events from Foundry."""
        try:
            content = data.get("message", data.get("content", ""))
            if not content:
                content = data.get("data", {}).get("message", "")

            speaker = data.get("speaker", data.get("data", {}).get("speaker", ""))
            whisper_to = data.get("whisper_to", data.get("data", {}).get("whisper_to", []))

            # Skip non-player messages
            if not self._is_player_message(data):
                return

            logger.info(f"Chat message from {speaker}: {content[:100]}")

            # Check for GM commands (handled even while paused so the
            # AI can be resumed via "/gm resume ai")
            if content.startswith("/gm ") or content.startswith("/ask"):
                await self._handle_gm_command(speaker, content)
                return

            # Respect the pause flag for normal player messages
            if not self._running:
                return

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
                f"[GM Error] Something went wrong: {str(e)}",
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
                results = await self.dispatcher.execute_batch(actions)

                # Register NPC speakers to prevent self-triggering
                for action in actions:
                    if action.get("type") == "speak" and action.get("npc_name"):
                        self.register_ai_speaker(action.get("npc_name"))

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

            # Notify admin panel
            if self._on_results_callback:
                await self._on_results_callback(results)

        except Exception as e:
            logger.error(f"Error processing message: {e}", exc_info=True)

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
                results = await self.dispatcher.execute_batch(actions)

                # Register NPC speakers to prevent self-triggering
                for action in actions:
                    if action.get("type") == "speak" and action.get("npc_name"):
                        self.register_ai_speaker(action.get("npc_name"))

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
                f"[GM Error] Combat input error: {str(e)}",
                speaker="GM"
            )
            # Still advance to avoid deadlock
            if self._combat_loop and self._combat_loop.is_running:
                self._combat_loop.advance_pc_turn()

    async def _handle_gm_command(self, speaker: str, content: str):
        """Handle a /gm command from a player (for the human GM)."""
        command = content[4:].strip()

        if command.startswith("narrate "):
            await self.foundry.chat_message(command[8:], speaker="GM")
        elif command.startswith("roll "):
            roll_part = command[5:].strip()
            await self.foundry.roll(roll_part, speaker="GM")
        elif command == "help":
            await self.foundry.chat_message(
                "GM Commands:\n"
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

    async def _get_npc_context(self) -> str:
        """Get current NPC context from loaded files + Foundry actors."""
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
                    actor_lines.append(
                        f"- {a.get('name', 'Unknown')} "
                        f"(HP: {a.get('hp', '?')}/{a.get('max_hp', '?')})"
                    )
                parts.append("Active NPCs/Characters:\n" + "\n".join(actor_lines))
        except Exception as e:
            logger.warning(f"Failed to get actor context: {e}")

        return "\n\n".join(parts) if parts else "No NPC context available."

    # --- Callbacks ---
    _on_results_callback: Optional[Callable] = None
    _on_scene_change_callback: Optional[Callable] = None

    def set_results_callback(self, callback: Callable):
        self._on_results_callback = callback

    def set_scene_change_callback(self, callback: Callable):
        self._on_scene_change_callback = callback
