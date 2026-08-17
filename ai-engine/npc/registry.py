"""NPC registry and management — maintains NPC personality and relationship data."""

import logging
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field

from npc.goals import Goal

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
    voice: Optional[str] = None  # assigned TTS voice (session-persistent)
    goals: List[Goal] = field(default_factory=list)


class NPCRegistry:
    """Registry for managing NPC personalities and relationships.

    Supports bidirectional identity mapping between NPC IDs and Foundry actor UUIDs,
    enabling location tracking, event enrichment, and settlement queries.
    """

    def __init__(self):
        self.npcs: Dict[str, NPCRecord] = {}
        self.relationships: Dict[Tuple[str, str], NPCRelationship] = {}
        # Bidirectional identity mapping: actor_uuid <-> npc_id
        self._actor_uuid_to_npc_id: Dict[str, str] = {}  # foundry UUID -> npc_id
        self._npc_id_to_actor_uuid: Dict[str, str] = {}  # npc_id -> foundry UUID

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

    def add_goal(self, npc_id: str, goal: Goal) -> bool:
        """Add a goal to an NPC's goal list."""
        npc = self.get_npc(npc_id)
        if not npc:
            return False
        npc.goals.append(goal)
        logger.info(f"Added goal for {npc.npc_name}: {goal.description}")
        return True

    def get_active_goals(self, npc_id: str) -> List[Goal]:
        """Goals with status 'pending' or 'active', highest priority first."""
        npc = self.get_npc(npc_id)
        if not npc:
            return []
        active = [g for g in npc.goals if g.status in ("pending", "active")]
        return sorted(active, key=lambda g: g.priority, reverse=True)

    def list_npcs(self) -> List[NPCRecord]:
        """Get all registered NPCs."""
        return list(self.npcs.values())

    def map_actor_to_npc(self, actor_uuid: str, npc_id: str) -> bool:
        """Register a bidirectional mapping between a Foundry actor UUID and NPC ID."""
        if npc_id not in self.npcs:
            logger.warning(f"Cannot map unknown NPC ID: {npc_id}")
            return False

        self._actor_uuid_to_npc_id[actor_uuid] = npc_id
        self._npc_id_to_actor_uuid[npc_id] = actor_uuid
        logger.debug(f"Mapped actor {actor_uuid} <-> NPC {npc_id}")
        return True

    def get_npc_by_actor_uuid(self, actor_uuid: str) -> Optional[NPCRecord]:
        """Retrieve an NPC record by Foundry actor UUID."""
        npc_id = self._actor_uuid_to_npc_id.get(actor_uuid)
        return self.get_npc(npc_id) if npc_id else None

    def get_actor_uuid_for_npc(self, npc_id: str) -> Optional[str]:
        """Get the Foundry actor UUID for an NPC, if mapped."""
        return self._npc_id_to_actor_uuid.get(npc_id)

    def get_npc_id_for_actor(self, actor_uuid: str) -> Optional[str]:
        """Get the NPC ID for a Foundry actor UUID, if mapped."""
        return self._actor_uuid_to_npc_id.get(actor_uuid)

    def find_npc_by_name_fuzzy(self, name: str) -> Optional[Tuple[str, NPCRecord]]:
        """Find an NPC by name with fuzzy matching, returning (npc_id, record).

        Used during sync_with_foundry() to match Foundry actor names to NPCs.
        Returns the best match if found, None otherwise.
        """
        if not name or not name.strip():
            return None

        name_lower = name.lower().strip()
        best_match = None
        best_score = 0

        for npc_id, record in self.npcs.items():
            npc_name_lower = record.npc_name.lower()

            # Exact match (highest priority)
            if npc_name_lower == name_lower:
                return (npc_id, record)

            # Substring match
            if name_lower in npc_name_lower or npc_name_lower in name_lower:
                score = max(
                    len(name_lower) / len(npc_name_lower),
                    len(npc_name_lower) / len(name_lower),
                )
                if score > best_score:
                    best_score = score
                    best_match = (npc_id, record)

        return best_match if best_score > 0.5 else None

    async def sync_with_foundry(self, foundry_client, session_id: str) -> int:
        """Match NPCs to Foundry actors by name, build identity mapping.

        Attempts to find a Foundry actor for each registered NPC using fuzzy name
        matching. Returns the number of successful mappings.

        Args:
            foundry_client: FoundryClient instance
            session_id: Current session ID (for logging)

        Returns:
            Number of new mappings created
        """
        try:
            actors = await foundry_client.get_actors()
        except Exception as e:
            logger.error(f"Failed to fetch Foundry actors: {e}")
            return 0

        if not actors:
            logger.warning("No actors found in Foundry")
            return 0

        mapped_count = 0
        for actor in actors:
            actor_name = actor.get("name", "")
            actor_uuid = actor.get("uuid", "")

            if not actor_name or not actor_uuid:
                continue

            # Skip already-mapped actors
            if actor_uuid in self._actor_uuid_to_npc_id:
                continue

            # Find best NPC match by name
            match = self.find_npc_by_name_fuzzy(actor_name)
            if match:
                npc_id, npc_record = match
                # Skip if this NPC is already mapped to a different actor
                if self._npc_id_to_actor_uuid.get(npc_id):
                    continue

                if self.map_actor_to_npc(actor_uuid, npc_id):
                    logger.info(
                        f"[Session {session_id}] Mapped Foundry actor "
                        f"'{actor_name}' ({actor_uuid}) to NPC '{npc_record.npc_name}'"
                    )
                    mapped_count += 1

        logger.info(f"[Session {session_id}] Synced {mapped_count} NPC-actor mappings")
        return mapped_count

    def clear(self) -> None:
        """Clear all NPC data."""
        self.npcs.clear()
        self.relationships.clear()
        self._actor_uuid_to_npc_id.clear()
        self._npc_id_to_actor_uuid.clear()
        logger.info("Cleared NPC registry")
