"""
Context Reinforcement Manager — the central coordinator that ensures
the LLM never drifts from established campaign facts.

This module runs periodic reinforcement passes:
1. Every N user/assistant turns, it injects fresh anchor facts
2. On combat start/end, it updates game state anchors

Summarising what happened in play is context/campaign_memory.py's job.

Usage (in main.py lifespan):
    reinforcer_mgr = ContextReinforcementManager(...)
    await reinforcer_mgr.start()
    # ... during request handling ...
    await reinforcer_mgr.record_turn(user_msg, assistant_msg)
    # ... on shutdown ...
    await reinforcer_mgr.stop()
"""

import logging
from collections import deque
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from utils.tasks import spawn

logger = logging.getLogger(__name__)

# Memory limits for bounded collections
MAX_ACTIVE_PLAYERS = 20     # Keep last 20 players


class ContextReinforcementManager:
    """Central coordinator for context reinforcement."""

    def __init__(
        self,
        llm_manager,
        state_tracker,
        foundry_client,
        scene_awareness=None,
        campaign_loader=None,
        reinforce_interval=5,
    ):
        self.llm_manager = llm_manager
        self.state_tracker = state_tracker
        self.foundry_client = foundry_client
        self.scene_awareness = scene_awareness
        self.campaign_loader = campaign_loader

        self.reinforce_interval = reinforce_interval

        # Internal state
        self._turn_count = 0
        self._message_count = 0
        self._last_reinforce_turn = 0
        self._last_reinforcement_time: Optional[str] = None
        self._status = "idle"
        self._world_summary = ""
        self._running = False
        self._session_start = datetime.now(timezone.utc)
        # Bounded to prevent unbounded memory growth
        self._active_players: deque = deque(maxlen=MAX_ACTIVE_PLAYERS)

        logger.info("[Reinforcement] Manager initialized")

    async def start(self):
        self._running = True
        self._status = "running"

    async def stop(self):
        self._running = False
        self._status = "stopped"
        logger.info("[Reinforcement] Manager stopped")

    async def record_turn(self, user_message: str, assistant_message: str):
        """Record a user/assistant turn pair and trigger reinforcement."""
        self._turn_count += 1
        self._message_count += 2

        # Extract key entities from messages for tracking
        await self._extract_entities(user_message, assistant_message)

        # Periodic reinforcement (every N turns)
        if self._turn_count - self._last_reinforce_turn >= self.reinforce_interval:
            await self._do_reinforcement()
            self._last_reinforce_turn = self._turn_count

        # Log for observability
        logger.info(
            f"[Reinforcement] Turn #{self._turn_count} recorded"
            f" (last reinforce: {self._last_reinforce_turn})"
        )

    async def on_combat_start(self, tokens: List[Dict]):
        """Handle combat start by updating game state anchors."""
        logger.info(f"[Reinforcement] Combat started with {len(tokens)} tokens")

        # Update NPC context for combatants
        if self.campaign_loader:
            npc_data = []
            for token in tokens[:30]:
                npc_data.append({
                    "name": token.get("name", "Unknown"),
                    "hp": token.get("hp", "?"),
                    "max_hp": token.get("max_hp", "?"),
                    "class": token.get("class_type", "Creature"),
                })
            self.llm_manager._reinforcer.update_npc_summary(npc_data)

    async def on_combat_end(self):
        logger.info("[Reinforcement] Combat ended")

    async def _do_reinforcement(self):
        """Perform a reinforcement pass — inject fresh context."""
        logger.info(f"[Reinforcement] Performing reinforcement pass (turn #{self._turn_count})")

        # Get current game state
        game_state_dict = {}
        if self.state_tracker:
            state = self.state_tracker.state
            # Normalise mode: tolerate a plain string from deserialization.
            mode_value = state.mode.value if hasattr(state.mode, "value") else str(state.mode)
            in_combat = mode_value == "combat"
            game_state_dict = {
                "mode": mode_value,
                "scene": {"name": state.current_scene} if state.current_scene else {},
                "in_combat": in_combat,
                "combat_round": state.combat.round if in_combat else None,
                "nearby_npcs": [],
            }

            # Get nearby NPCs if in exploration mode
            if not in_combat:
                try:
                    actors = await self.foundry_client.get_actors(world_only=True)
                    if actors:
                        game_state_dict["nearby_npcs"] = [
                            {"name": a.get("name"), "hp": a.get("hp")}
                            for a in actors[:10]
                        ]
                except Exception:
                    logger.debug("Nearby-NPC context unavailable for this reinforcement pass", exc_info=True)

        # Update the LLMManager's reinforcer
        reinforcement = ""
        if self.llm_manager and self.llm_manager._reinforcer:
            reinforcement = self.llm_manager._reinforcer.get_reinforcement(
                active_state=game_state_dict,
            )
            if reinforcement:
                logger.info(
                    f"[Reinforcement] Reinforcement payload: "
                    f"~{len(reinforcement)} chars"
                )

        self._last_reinforcement_time = datetime.now(timezone.utc).isoformat()
        return reinforcement

    async def reinforce_context(self) -> str:
        """Manually run a reinforcement pass; returns the injected payload."""
        return await self._do_reinforcement()

    async def update_world_summary(self, state_dict: Dict[str, Any], scene_data: str = ""):
        """Update the world summary from the current game state."""
        if self.llm_manager and self.llm_manager._reinforcer:
            self.llm_manager._reinforcer.update_world_summary(state_dict, scene_data)
            self._world_summary = self.llm_manager._reinforcer.world_summary

    def _get_anchor_facts(self) -> List[str]:
        """Return the reinforcer's anchor facts for display."""
        if self.llm_manager and self.llm_manager._reinforcer:
            return self.llm_manager._reinforcer.get_anchor_facts()
        return []

    async def _extract_entities(self, user_msg: str, assistant_msg: str):
        """Extract entities from messages for tracking."""
        # Extract player names (simplified heuristic)
        if "PLAYERS" in user_msg.upper() or "[" in user_msg:
            # Extract name from bracket pattern
            import re
            match = re.search(r'\[([^\]]+)\]:', user_msg)
            if match:
                name = match.group(1)
                if name not in self._active_players:
                    self._active_players.append(name)
                    logger.info(f"[Reinforcement] New active player detected: {name}")

    def get_session_status(self) -> Dict[str, Any]:
        """Return current reinforcement status for admin panel."""
        elapsed = (datetime.now(timezone.utc) - self._session_start).total_seconds()
        return {
            "turn_count": self._turn_count,
            "session_start": self._session_start.isoformat(),
            "session_duration_minutes": round(elapsed / 60, 1),
            "last_reinforce_turn": self._last_reinforce_turn,
            "active_players": list(self._active_players),
            "pending_reinforcement": (
                self._turn_count - self._last_reinforce_turn >= self.reinforce_interval
            ),
        }

    def force_reinforce(self):
        """Manually trigger a reinforcement pass (for admin panel).

        Via spawn(), not create_task: the loop holds only a weak reference to a
        bare task, so one suspended on its first LLM await could be collected
        mid-flight, losing the pass and swallowing any exception with it.
        """
        logger.info("[Reinforcement] Manual reinforcement triggered")
        return spawn(self._do_reinforcement())
