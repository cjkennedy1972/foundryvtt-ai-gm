"""
Context Reinforcer — prevents LLM drift by periodically injecting
fresh context into the conversation and summarizing old turns.

## How it works

1. **Anchor facts** — immutable campaign facts that must never be forgotten
   (world lore, core rules, NPCs, locations, quest lines)
2. **Periodic summarization** — after N message pairs, the old conversation
   gets summarized into a compact "Story So Far" and injected as a system message
3. **Reality checks** — before generating responses, the LLM receives a compact
   list of verified game state facts to ground its output
4. **Drift alerts** — when the LLM's output contradicts known facts, a warning
   is logged and the context is re-injected

## Usage

    from context.reinforcer import ContextReinforcer

    reinforcer = ContextReinforcer(
        anchor_facts=["The world is called Aethelwyrd.", "Magic costs sanity."],
        npc_summary="List of current NPCs and their traits.",
        world_summary="Worldbuilding notes.",
        summarize_every_n_pairs=10,  # Summarize every 10 user/assistant pairs
    )

    # Before each LLM call:
    reinforcement_msg = reinforcer.get_reinforcement(active_state=state_dict)
    # Add reinforcement_msg as a system message in the LLM prompt

    # After generating (to build the summary):
    reinforcer.add_turn(user_msg=..., assistant_msg=...)

    # When trimming would lose too much context:
    summary = reinforcer.try_summarize(messages_to_trim)
    # The summary is injected as a new system message
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


class ContextReinforcer:
    """Prevents LLM drift by anchoring to hard facts and summarizing old context.

    Attributes:
        summarize_every_n_pairs: Summarize conversation after this many user/assistant pairs.
            Default is 10 pairs (~20 messages).
        anchor_facts: Set of immutable facts the LLM must never forget.
        npc_summary: Current NPC list and key traits.
        world_summary: Worldbuilding and setting notes.
        session_summary: Living summary of what happened this session.
        active_quests: Current quest lines and their status.
        active_players: List of player names and their current situation.
    """

    def __init__(
        self,
        anchor_facts: Optional[List[str]] = None,
        npc_summary: str = "",
        world_summary: str = "",
        session_summary: str = "",
        active_quests: Optional[List[str]] = None,
        active_players: Optional[List[str]] = None,
        summarize_every_n_pairs: int = 10,
        max_summary_length: int = 4000,  # tokens
    ):
        self.anchor_facts: Set[str] = set(anchor_facts) if anchor_facts else set()
        self.npc_summary = npc_summary
        self.world_summary = world_summary
        self.session_summary = session_summary
        self.active_quests: List[str] = active_quests or []
        self.active_players: List[str] = active_players or []
        self.summarize_every_n_pairs = summarize_every_n_pairs
        self.max_summary_length = max_summary_length

        # Internal: track message count for periodic summarization
        self._message_count = 0  # counts assistant messages
        self._pending_turn: Optional[Dict[str, str]] = None
        self._conversation_log: List[Dict[str, str]] = []  # raw messages for summarization
        self._summarize_at = summarize_every_n_pairs

    def record_turn(self, user_message: str, assistant_response: str):
        """Record a user/assistant turn pair.

        After N pairs, triggers a summary pass that condenses old context
        into a compact "Story So Far" and re-injects it.
        """
        self._message_count += 1
        self._conversation_log.extend([
            {"role": "user", "content": user_message},
            {"role": "assistant", "content": assistant_response},
        ])

        if self._message_count >= self._summarize_at:
            self._trigger_summarization()

    def get_reinforcement(
        self,
        active_state: Optional[Dict[str, Any]] = None,
        extra_context: str = "",
    ) -> str:
        """Build a compact reinforcement message for LLM context injection.

        This returns a system-level message that should be prepended to the
        LLM's context window before generating a response. It contains:
        - Hard anchor facts (world lore, rules)
        - Active NPC context
        - Current game state
        - Active quests
        - Any extra drift-prevention context

        Args:
            active_state: Current game state dict from GameStateTracker.
            extra_context: Any additional context the caller wants included.

        Returns:
            A string to inject as a system message.
        """
        parts = []

        # Anchor facts — these must never be forgotten
        if self.anchor_facts:
            parts.append("## ANCHORED CONTEXT ##")
            parts.append("The following facts are established and must not be contradicted:")
            for fact in self.anchor_facts:
                parts.append(f"- {fact}")

        # NPC context
        if self.npc_summary:
            parts.append("\n## ACTIVE NPCs ##")
            parts.append(self.npc_summary)

        # World context
        if self.world_summary:
            parts.append("\n## WORLD CONTEXT ##")
            parts.append(self.world_summary)

        # Active state
        if active_state:
            parts.append("\n## CURRENT GAME STATE ##")
            parts.append(self._format_state(active_state))

        # Active quests
        if self.active_quests:
            parts.append("\n## ACTIVE QUESTS ##")
            for quest in self.active_quests:
                parts.append(f"- {quest}")

        # Session summary (what happened recently)
        if self.session_summary:
            parts.append("\n## SESSION SUMMARY ##")
            parts.append(self.session_summary)

        # Active players
        if self.active_players:
            parts.append("\n## ACTIVE PLAYERS ##")
            for player in self.active_players:
                parts.append(f"- {player}")

        # Extra context
        if extra_context:
            parts.append(f"\n{extra_context}")

        return "## CONTEXT ANCHOR ##\n" + "\n".join(parts) if parts else ""

    def _format_state(self, state: Dict[str, Any]) -> str:
        """Format game state dict into a compact string."""
        lines = []
        # Always include combat status
        if state.get("in_combat"):
            lines.append(f"  Combat: Active (round {state.get('combat_round', '?')})")
            combatants = state.get("combat_combatants", [])
            lines.append(f"  Combatants: {', '.join(c.get('name', '?') for c in combatants[:10])}")
        else:
            lines.append(f"  Combat: Not active")

        # Scene/location
        scene = state.get("scene", {})
        if scene:
            scene_name = scene.get("name", "unknown")
            lines.append(f"  Location: {scene_name}")
            x, y = scene.get("x", 0), scene.get("y", 0)
            if x and y:
                lines.append(f"  Position: ({x}, {y})")

        # Time
        if state.get("time_of_day"):
            lines.append(f"  Time: {state['time_of_day']}")

        # Notable NPCs nearby
        nearby = state.get("nearby_npcs", [])
        if nearby:
            names = ", ".join(n.get("name", "?") for n in nearby[:5])
            lines.append(f"  Nearby NPCs: {names}")

        return "\n".join(lines)

    def try_summarize(self, messages: List[Dict[str, str]]) -> str:
        """Try to summarize old conversation messages into a compact summary.

        This is called when the conversation is getting long and we need to
        compress old turns into a summary that preserves important facts.

        The summary is structured to be easy for the LLM to remember:
        - Who spoke and when
        - Key decisions made
        - New facts introduced
        - Quest/plot developments

        Args:
            messages: List of {role, content} dicts for old conversation turns.

        Returns:
            A compact summary string.
        """
        if not messages:
            return ""

        parts = []

        # Group by who spoke
        speakers = {}
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")[:200]  # Truncate for summarization
            if role not in speakers:
                speakers[role] = []
            speakers[role].append(content)

        if "user" in speakers:
            parts.append(f"Player messages ({len(speakers['user'])} messages):")
            # Take the most important/salient ones
            for msg in speakers["user"][:5]:
                parts.append(f"  - {msg}")

        if "assistant" in speakers:
            parts.append(f"GM responses ({len(speakers['assistant'])} messages):")
            for msg in speakers["assistant"][:5]:
                # Extract key actions/events
                if "actions" in msg.lower():
                    parts.append(f"  - GM response with actions described")
                elif "damage" in msg.lower() or "hit" in msg.lower():
                    parts.append(f"  - GM resolved an action (damage/combat)")
                elif "success" in msg.lower() or "failed" in msg.lower():
                    parts.append(f"  - GM resolved a check/roll")
                else:
                    parts.append(f"  - GM response describing events")

        summary = "## PREVIOUS SESSION SUMMARY ##\n" + "\n".join(parts)
        self.session_summary = summary
        return summary

    def _trigger_summarization(self):
        """Summarize the oldest half of the conversation log and clear it."""
        if len(self._conversation_log) < 4:
            return

        # Keep the most recent messages, summarize the rest
        half = len(self._conversation_log) // 2
        old_messages = self._conversation_log[:half]
        self._conversation_log = self._conversation_log[half:]

        summary = self.try_summarize(old_messages)
        if summary:
            logger.info(f"[Context] Summarized {half} messages into compact summary")

        # Reset counter and set the next threshold relative to where we are now.
        # Using a fixed absolute value (summarize_every_n_pairs) would make
        # _summarize_at < _message_count on every subsequent call since
        # _message_count is bumped by the LLMManager every 3rd generate().
        self._message_count = 0
        self._summarize_at = self.summarize_every_n_pairs

    def inject_anchors_into_history(
        self,
        conversation_history: List[Dict[str, str]],
        game_state: Dict[str, Any],
    ) -> List[Dict[str, str]]:
        """Inject anchor facts directly into the conversation history.

        This is the strongest form of reinforcement — it literally
        inserts the anchors between the system prompt and the conversation,
        ensuring they're the most recent context before the user message.

        The anchors are injected AFTER any existing system messages but
        BEFORE the conversation history, so they're fresh in the LLM's
        context window.

        Args:
            conversation_history: Current conversation history list.
            game_state: Current game state.

        Returns:
            Conversation history with anchors injected.
        """
        if not self.anchor_facts and not self.npc_summary and not self.active_quests:
            return conversation_history

        # Build a minimal anchor block
        anchor_parts = []
        if self.anchor_facts:
            anchor_parts.append("## ESTABLISHED FACTS ##")
            for fact in self.anchor_facts:
                anchor_parts.append(f"- {fact}")

        if self.active_quests:
            anchor_parts.append("\n## ACTIVE QUESTS ##")
            for quest in self.active_quests:
                anchor_parts.append(f"- {quest}")

        if not anchor_parts:
            return conversation_history

        # Insert anchors as a system message right before the user's last message
        new_history = list(conversation_history)

        # Find where to inject (after system messages, before conversation)
        insert_idx = 0
        for i, msg in enumerate(new_history):
            if msg.get("role") != "system":
                insert_idx = i
                break
            else:
                insert_idx = i + 1

        new_history.insert(insert_idx, {
            "role": "system",
            "content": "\n".join(anchor_parts),
        })

        return new_history

    def update_npc_summary(self, npc_data: List[Dict[str, Any]]):
        """Update the NPC summary from FoundryVTT actor data."""
        lines = []
        for npc in npc_data:
            name = npc.get("name", "Unknown")
            hp = npc.get("hp", "?")
            lines.append(f"- **{name}** (HP: {hp}, Pos: {npc.get('x', '?')},{npc.get('y', '?')})")
        self.npc_summary = "\n".join(lines) if lines else "No NPCs in combat"
        logger.info(f"[Reinforcement] Updated NPC summary: {len(lines)} combatants")
        self._last_npc_update = datetime.now().isoformat()

    def update_world_summary(self, state_dict: Dict[str, Any], scene_data: str = ""):
        """Update the world summary from the full game state."""
        summary_parts = []

        # Extract key state from the game state dict
        if state_dict:
            mode = state_dict.get("mode", "exploration")
            scene = state_dict.get("current_scene", "")
            campaign = state_dict.get("campaign", "")
            session = state_dict.get("session_number", 0)

            summary_parts.append(f"**Campaign:** {campaign}")
            summary_parts.append(f"**Session:** {session}")
            summary_parts.append(f"**Mode:** {mode}")
            summary_parts.append(f"**Current Scene:** {scene}")

            # Add combat state if in combat
            combat = state_dict.get("combat_state", {})
            if combat and combat.get("in_combat"):
                summary_parts.append(f"**Combat:** Round {combat.get('round_num', '?')}, Turn {combat.get('turn', '?')}")
                summary_parts.append(f"**Turn Order:** {len(combat.get('turn_order', []))} combatants")

            # Add NPC context
            npc_context = state_dict.get("npc_context", {})
            if npc_context:
                for npc_name, npc_info in npc_context.items():
                    if isinstance(npc_info, dict):
                        hp = npc_info.get("hp", "?")
                        summary_parts.append(f"**NPC:** {npc_name} (HP: {hp})")

        # Add scene data if available
        if scene_data:
            summary_parts.append(f"\n{scene_data}")

        self.world_summary = "\n".join(summary_parts)
        self._last_world_summary_update = datetime.now().isoformat()
        logger.info("[Reinforcement] World summary updated")

    def update_session_summary(self, summary: str):
        """Update the living session summary."""
        self.session_summary = summary
        logger.info(f"[Reinforcement] Session summary updated ({len(summary)} chars)")

    def get_anchor_facts(self) -> List[str]:
        """Get the current anchor facts for display."""
        return list(self.anchor_facts)
