"""
Context Reinforcement Manager — the central coordinator that ensures
the LLM never drifts from established campaign facts.

This module runs periodic reinforcement passes:
1. Every N user/assistant turns, it injects fresh anchor facts
2. Every M turns, it summarizes old conversation into compact memory
3. On scene changes, it refreshes scene-specific context
4. On combat start/end, it updates game state anchors

Usage (in main.py lifespan):
    reinforcer_mgr = ContextReinforcementManager(...)
    await reinforcer_mgr.start()
    # ... during request handling ...
    await reinforcer_mgr.record_turn(user_msg, assistant_msg)
    # ... on shutdown ...
    await reinforcer_mgr.stop()
"""

import asyncio
import json
import logging
from collections import deque
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Memory limits for bounded collections
MAX_SESSION_HIGHLIGHTS = 20  # Keep last 20 events
MAX_ACTIVE_QUESTS = 10      # Keep last 10 quests
MAX_ACTIVE_PLAYERS = 20     # Keep last 20 players


class ContextReinforcementManager:
    """Central coordinator for context reinforcement and summarization."""

    def __init__(
        self,
        llm_manager,
        state_tracker,
        foundry_client,
        scene_awareness=None,
        campaign_loader=None,
        db=None,
        reinforce_interval=5,
        summarize_interval=10,
        summarize_timer=300,
    ):
        self.llm_manager = llm_manager
        self.state_tracker = state_tracker
        self.foundry_client = foundry_client
        self.scene_awareness = scene_awareness
        self.campaign_loader = campaign_loader
        self.db = db

        self.reinforce_interval = reinforce_interval
        self.summarize_interval = summarize_interval
        self.summarize_timer = summarize_timer

        # Internal state
        self._turn_count = 0
        self._message_count = 0
        self._last_reinforce_turn = 0
        self._last_summarize_turn = 0
        self._last_reinforcement_time: Optional[str] = None
        self._status = "idle"
        self._world_summary = ""
        self._running = False
        self._session_start = datetime.now(timezone.utc)
        # Bounded collections to prevent unbounded memory growth
        self._active_quests: deque = deque(maxlen=MAX_ACTIVE_QUESTS)
        self._active_players: deque = deque(maxlen=MAX_ACTIVE_PLAYERS)
        self._session_highlights: deque = deque(maxlen=MAX_SESSION_HIGHLIGHTS)

        # Periodic task handle
        self._summarize_task: Optional[asyncio.Task] = None

        logger.info("[Reinforcement] Manager initialized")

    async def start(self):
        """Start periodic summarization task."""
        self._running = True
        self._status = "running"
        self._summarize_task = asyncio.create_task(self._periodic_summarize())
        logger.info("[Reinforcement] Periodic summarization started")

    async def stop(self):
        """Stop periodic summarization task."""
        self._running = False
        self._status = "stopped"
        if self._summarize_task:
            self._summarize_task.cancel()
            try:
                await self._summarize_task
            except asyncio.CancelledError:
                pass
        logger.info("[Reinforcement] Manager stopped")

    async def record_turn(self, user_message: str, assistant_message: str):
        """Record a user/assistant turn pair and trigger reinforcement/summarization."""
        self._turn_count += 1
        self._message_count += 2

        # Extract key entities from messages for tracking
        await self._extract_entities(user_message, assistant_message)

        # Periodic reinforcement (every N turns)
        if self._turn_count - self._last_reinforce_turn >= self.reinforce_interval:
            await self._do_reinforcement()
            self._last_reinforce_turn = self._turn_count

        # Periodic summarization (every M turns)
        if self._turn_count - self._last_summarize_turn >= self.summarize_interval:
            await self._trigger_summarization()
            self._last_summarize_turn = self._turn_count

        # Log for observability
        logger.info(
            f"[Reinforcement] Turn #{self._turn_count} recorded"
            f" (last reinforce: {self._last_reinforce_turn}, "
            f"last summarize: {self._last_summarize_turn})"
        )

    async def on_scene_change(self, scene_name: str):
        """Handle scene changes by refreshing scene-specific context."""
        logger.info(f"[Reinforcement] Scene changed to '{scene_name}', refreshing context")

        # Get scene details from Foundry
        try:
            scene_tokens = await self.foundry_client.get_scene_tokens()
            if scene_tokens:
                token_info = []
                for token in scene_tokens[:20]:
                    name = token.get("name", "Unknown")
                    hp = token.get("hp", "?")
                    disposition = token.get("disposition", "?")
                    side = "Hostile" if disposition == -1 else "Friendly/Neutral"
                    token_info.append(f"{name} ({side}, HP: {hp})")
                self._session_highlights.append(
                    f"Scene: {scene_name} — Tokens: {', '.join(token_info[:10])}"
                )
        except Exception as e:
            logger.warning(f"[Reinforcement] Failed to get scene tokens: {e}")

        # Update the reinforcer's NPC/world context
        if self.campaign_loader:
            npc_context = self.campaign_loader.get_npc_context_sync()
            if npc_context:
                # Parse structured NPC entries (name|hp pairs or JSON).
                # Avoid splitting raw prose line-by-line.
                npc_list = self._parse_npc_context(npc_context)
                self.llm_manager._reinforcer.update_npc_summary(npc_list[:5])

    @staticmethod
    def _parse_npc_context(npc_context: str) -> List[Dict[str, Any]]:
        """Parse NPC context into structured entries.

        Handles two formats:
        1. Pipe-delimited: "Name|HP" per line
        2. JSON list of {name, hp} objects
        Strictly filters headers and prose to avoid injecting
        raw fragments into highlights.
        """
        lines = [l.strip() for l in npc_context.split("\n") if l.strip()]
        if not lines:
            return []

        # Try JSON first (highest-fidelity format)
        try:
            import json as _json
            data = _json.loads(npc_context)
            if isinstance(data, list):
                return [
                    {"name": str(entry.get("name", "Unknown")), "hp": str(entry.get("hp", "?"))}
                    for entry in data
                ]
        except (json.JSONDecodeError, ValueError):
            pass

        # Parse pipe-delimited: "Name|HP"
        # Only accept lines that look like structured data (contain |).
        # Skip known header lines and prose.
        parsed: List[Dict[str, Any]] = []
        known_headers = {
            "npc", "npcs", "name", "names", "creature", "creatures",
            "actor", "actors", "token", "tokens", "hp", "current hp",
            "health", "max hp", "current", "hit points", "status",
        }
        for line in lines:
            lower = line.lower()
            if lower in known_headers:
                continue
            if "|" in line:
                parts = line.split("|", 1)
                name = parts[0].strip()
                hp = parts[1].strip() if len(parts) > 1 else "?"
                # Require a reasonable-looking name (non-empty, not a header)
                if name and len(name) > 1 and len(name) < 60:
                    parsed.append({"name": name, "hp": hp})
            # Lines without | are treated as prose/headers and skipped
            # (this avoids injecting raw fragments into highlights)
        return parsed

    async def on_combat_start(self, tokens: List[Dict]):
        """Handle combat start by updating game state anchors."""
        logger.info(f"[Reinforcement] Combat started with {len(tokens)} tokens")

        # Record combat start
        self._session_highlights.append(
            f"⚔️ Combat started — {len(tokens)} participants"
        )

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
        """Handle combat end by recording highlights."""
        logger.info("[Reinforcement] Combat ended")
        self._session_highlights.append("⚔️ Combat ended")

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
                    pass

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

        # Update session highlights
        highlights_text = "\n".join(list(self._session_highlights)[-5:]) if self._session_highlights else "No highlights yet."
        if self.llm_manager._reinforcer:
            self.llm_manager._reinforcer.update_session_summary(
                f"Session highlights so far:\n{highlights_text}"
            )

        self._last_reinforcement_time = datetime.now(timezone.utc).isoformat()
        return reinforcement

    async def _trigger_summarization(self):
        """Trigger a summarization pass — compress old context."""
        logger.info(
            f"[Reinforcement] Triggering summarization pass "
            f"(turn #{self._turn_count})"
        )

        # Build a compact summary of session progress
        summary_parts = []

        # Session highlights
        if self._session_highlights:
            summary_parts.append(
                "Key events: " + "; ".join(list(self._session_highlights)[-3:])
            )

        # Active quests
        if self._active_quests:
            summary_parts.append(f"Active quests: {', '.join(list(self._active_quests)[:5])}")

        # Session duration
        elapsed = (datetime.now(timezone.utc) - self._session_start).total_seconds()
        minutes = int(elapsed / 60)
        summary_parts.append(f"Session duration: {minutes} minutes")

        # Build summary string
        summary_text = "\n".join(summary_parts) if summary_parts else "Session in progress."

        # Update reinforcer
        if self.llm_manager._reinforcer:
            self.llm_manager._reinforcer.update_session_summary(summary_text)

        # Persist summary to DB if available
        if self.db:
            try:
                session_id = await self.db.get_active_session()
                if session_id:
                    await self.db.save_conversation(
                        session_id,
                        "system",
                        f"## SESSION SUMMARY (auto-generated at turn {self._turn_count})\n{summary_text}",
                    )
                    logger.info("[Reinforcement] Summary persisted to DB")
            except Exception as e:
                logger.warning(f"[Reinforcement] Failed to persist summary: {e}")

        logger.info("[Reinforcement] Summarization complete")
        return summary_text

    # --- Admin API surface (used by main.py endpoints) ---

    async def reinforce_context(self) -> str:
        """Manually run a reinforcement pass; returns the injected payload."""
        return await self._do_reinforcement()

    async def summarize_context(self) -> str:
        """Manually run a summarization pass; returns the summary text."""
        return await self._trigger_summarization()

    def get_session_highlights(self) -> List[str]:
        """Return the session's recorded highlights, oldest first — the
        source material for end-of-session canon-proposal generation."""
        return list(self._session_highlights)

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

    async def _periodic_summarize(self):
        """Background task: periodically summarize conversation history."""
        while True:
            try:
                await asyncio.sleep(self.summarize_timer)
                await self._trigger_summarization()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"[Reinforcement] Summarization task error: {e}")

    async def _extract_entities(self, user_msg: str, assistant_msg: str):
        """Extract entities from messages for tracking."""
        # Extract quest-related keywords
        quest_keywords = ["quest", "objective", "goal", "prophecy", "curse", "blessing"]
        msg_lower = (user_msg + " " + assistant_msg).lower()
        for keyword in quest_keywords:
            if keyword in msg_lower:
                # Extract surrounding context
                idx = msg_lower.index(keyword)
                context = msg_lower[max(0, idx-100):idx+200]
                self._session_highlights.append(f"Quest-related: ...{context}...")
                break  # One per turn

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
            "last_summarize_turn": self._last_summarize_turn,
            "active_players": list(self._active_players),
            "active_quests": list(self._active_quests),
            "session_highlights": list(self._session_highlights)[-5:],
            "pending_reinforcement": (
                self._turn_count - self._last_reinforce_turn >= self.reinforce_interval
            ),
            "pending_summarization": (
                self._turn_count - self._last_summarize_turn >= self.summarize_interval
            ),
        }

    def force_reinforce(self):
        """Manually trigger a reinforcement pass (for admin panel)."""
        logger.info("[Reinforcement] Manual reinforcement triggered")
        asyncio.create_task(self._do_reinforcement())

    def force_summarize(self):
        """Manually trigger a summarization pass (for admin panel)."""
        logger.info("[Reinforcement] Manual summarization triggered")
        asyncio.create_task(self._trigger_summarization())
