"""
Context Reinforcer — prevents LLM drift by periodically injecting fresh
context into the conversation.

1. **Anchor facts** — campaign lore that must not be contradicted, retrieved
   for the current scene (LLMManager._build_anchor_facts)
2. **Reality checks** — a compact list of verified game state, NPC and world
   facts to ground the model's output

What happened in play is not kept here: context/campaign_memory.py compacts
the raw conversation log and supplies that on every turn.

    reinforcer = ContextReinforcer(anchor_facts=[...])
    reinforcement_msg = reinforcer.get_reinforcement(active_state=state_dict)
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)



class ContextReinforcer:
    """Prevents LLM drift by anchoring to hard facts.

    Attributes:
        anchor_facts: Set of immutable facts the LLM must never forget.
        npc_summary: Current NPC list and key traits.
        world_summary: Worldbuilding and setting notes.
        active_players: List of player names and their current situation.
    """

    def __init__(
        self,
        anchor_facts: Optional[List[str]] = None,
        npc_summary: str = "",
        world_summary: str = "",
        active_players: Optional[List[str]] = None,
    ):
        self.anchor_facts: Set[str] = set(anchor_facts) if anchor_facts else set()
        self.npc_summary = npc_summary
        self.world_summary = world_summary
        self.active_players: List[str] = active_players or []

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
            # Only when there are some. Nothing writes combat_combatants — the
            # reinforcement manager supplies mode, scene, in_combat,
            # combat_round and nearby_npcs — so this rendered as a bare
            # "Combatants:" on every pass during a fight, telling the model the
            # fight had no participants rather than saying nothing about them.
            combatants = state.get("combat_combatants") or []
            if combatants:
                names = ", ".join(c.get("name", "?") for c in combatants[:10])
                lines.append(f"  Combatants: {names}")
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
            # A GameMode renders as "GameMode.COMBAT" in an f-string, which is
            # what went into the prompt. GameState.get_summary already guards
            # this the same way, and tolerates a plain string from a
            # deserialised state.
            mode = mode.value if hasattr(mode, "value") else str(mode)
            scene = state_dict.get("current_scene", "")
            campaign = state_dict.get("campaign", "")
            session = state_dict.get("session_number", 0)

            summary_parts.append(f"**Campaign:** {campaign}")
            summary_parts.append(f"**Session:** {session}")
            summary_parts.append(f"**Mode:** {mode}")
            summary_parts.append(f"**Current Scene:** {scene}")

            # GameState dumps this under "combat", and CombatState calls the
            # field "round". Reading "combat_state" and "round_num" meant the
            # whole block was dropped from every world summary.
            combat = state_dict.get("combat") or {}
            if combat.get("in_combat"):
                summary_parts.append(
                    f"**Combat:** Round {combat.get('round', '?')}, "
                    f"Turn {combat.get('turn', '?')}"
                )
                summary_parts.append(
                    f"**Turn Order:** {len(combat.get('turn_order', []))} combatants"
                )

            # GameState declares npc_context as a str, and set_npc_context
            # writes one. Iterating .items() on it raised AttributeError
            # straight into the route's 500 handler as soon as a scene had
            # any NPC context at all.
            npc_context = state_dict.get("npc_context")
            if isinstance(npc_context, dict):
                for npc_name, npc_info in npc_context.items():
                    if isinstance(npc_info, dict):
                        hp = npc_info.get("hp", "?")
                        summary_parts.append(f"**NPC:** {npc_name} (HP: {hp})")
            elif npc_context:
                summary_parts.append(f"**NPCs:** {npc_context}")

        # Add scene data if available
        if scene_data:
            summary_parts.append(f"\n{scene_data}")

        self.world_summary = "\n".join(summary_parts)
        self._last_world_summary_update = datetime.now().isoformat()
        logger.info("[Reinforcement] World summary updated")

    def get_anchor_facts(self) -> List[str]:
        """Get the current anchor facts for display."""
        return list(self.anchor_facts)
