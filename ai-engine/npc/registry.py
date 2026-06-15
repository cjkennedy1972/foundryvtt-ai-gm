"""NPC registry and management — maintains NPC personality and relationship data."""

import logging
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class NPCRelationship:
    """Relationship between two NPCs or between NPC and PC."""

    source_id: str
    target_id: str
    target_name: str
    relationship_type: str  # ally, enemy, love, neutral, rival, mentor, etc.
    strength: float = 0.5  # 0-1, with 0.5 as neutral
    last_interaction: Optional[str] = None


@dataclass
class NPCRecord:
    """Complete NPC record with all personality and relationship data."""

    npc_id: str
    npc_name: str
    description: str
    personality: Optional[Dict] = None
    relationships: Dict[str, NPCRelationship] = field(default_factory=dict)
    appearance: Optional[str] = None
    class_name: Optional[str] = None
    level: Optional[int] = None
    alignment: Optional[str] = None
    notes: List[str] = field(default_factory=list)


class NPCRegistry:
    """Registry for managing NPC personalities and relationships."""

    def __init__(self):
        self.npcs: Dict[str, NPCRecord] = {}
        self.relationships: Dict[Tuple[str, str], NPCRelationship] = {}

    def register_npc(
        self,
        npc_id: str,
        npc_name: str,
        description: str,
        appearance: Optional[str] = None,
        class_name: Optional[str] = None,
        level: Optional[int] = None,
        alignment: Optional[str] = None,
    ) -> NPCRecord:
        """Register or update an NPC in the registry."""
        record = NPCRecord(
            npc_id=npc_id,
            npc_name=npc_name,
            description=description,
            appearance=appearance,
            class_name=class_name,
            level=level,
            alignment=alignment,
        )
        self.npcs[npc_id] = record
        logger.info(f"Registered NPC: {npc_name} ({npc_id})")
        return record

    def get_npc(self, npc_id: str) -> Optional[NPCRecord]:
        """Retrieve an NPC record."""
        return self.npcs.get(npc_id)

    def get_npc_by_name(self, npc_name: str) -> Optional[NPCRecord]:
        """Find an NPC by name."""
        for record in self.npcs.values():
            if record.npc_name.lower() == npc_name.lower():
                return record
        return None

    def set_npc_personality(self, npc_id: str, personality: Dict) -> bool:
        """Set personality data for an NPC."""
        npc = self.get_npc(npc_id)
        if not npc:
            return False
        npc.personality = personality
        logger.info(f"Updated personality for {npc.npc_name}")
        return True

    def add_relationship(
        self,
        source_id: str,
        target_id: str,
        target_name: str,
        relationship_type: str,
        strength: float = 0.5,
    ) -> NPCRelationship:
        """Add or update a relationship between two entities."""
        rel = NPCRelationship(
            source_id=source_id,
            target_id=target_id,
            target_name=target_name,
            relationship_type=relationship_type,
            strength=strength,
        )
        self.relationships[(source_id, target_id)] = rel

        # Also update the NPC record
        npc = self.get_npc(source_id)
        if npc:
            npc.relationships[target_id] = rel

        logger.info(f"{source_id} -> {target_name}: {relationship_type} ({strength:.0%})")
        return rel

    def get_relationship(self, source_id: str, target_id: str) -> Optional[NPCRelationship]:
        """Get relationship between two entities."""
        return self.relationships.get((source_id, target_id))

    def get_npc_relationships(self, npc_id: str) -> Dict[str, NPCRelationship]:
        """Get all relationships for an NPC."""
        npc = self.get_npc(npc_id)
        if not npc:
            return {}
        return npc.relationships

    def update_relationship(
        self,
        source_id: str,
        target_id: str,
        strength_delta: float,
        interaction_note: Optional[str] = None,
    ) -> bool:
        """Update relationship strength based on interaction."""
        rel = self.get_relationship(source_id, target_id)
        if not rel:
            return False

        # Adjust strength (clamped to 0-1)
        rel.strength = max(0.0, min(1.0, rel.strength + strength_delta))
        if interaction_note:
            rel.last_interaction = interaction_note

        logger.info(f"Updated {rel.source_id} -> {rel.target_name}: {rel.strength:.0%}")
        return True

    def get_npc_context(self, npc_id: str, include_relationships: bool = True) -> str:
        """Generate context for an NPC for inclusion in prompts."""
        npc = self.get_npc(npc_id)
        if not npc:
            return ""

        lines = [f"**{npc.npc_name}**"]

        if npc.class_name and npc.level:
            lines.append(f"Class: {npc.class_name} (Level {npc.level})")

        if npc.alignment:
            lines.append(f"Alignment: {npc.alignment}")

        if npc.appearance:
            lines.append(f"Appearance: {npc.appearance}")

        if npc.personality:
            if isinstance(npc.personality, dict):
                traits_str = "; ".join(
                    f"{cat}: {', '.join(t)}"
                    for cat, t in npc.personality.items()
                    if t
                )
                if traits_str:
                    lines.append(f"Personality: {traits_str}")

        if include_relationships and npc.relationships:
            rel_list = []
            for rel in npc.relationships.values():
                strength_str = "strongly " if rel.strength > 0.7 else ""
                if rel.strength > 0.5:
                    rel_list.append(f"{strength_str}{rel.relationship_type} with {rel.target_name}")
            if rel_list:
                lines.append(f"Relationships: {'; '.join(rel_list)}")

        if npc.notes:
            lines.extend(f"Note: {note}" for note in npc.notes[-3:])  # Last 3 notes

        return "\n".join(lines)

    def list_npcs(self) -> List[NPCRecord]:
        """Get all registered NPCs."""
        return list(self.npcs.values())

    def clear(self) -> None:
        """Clear all NPC data."""
        self.npcs.clear()
        self.relationships.clear()
        logger.info("Cleared NPC registry")
