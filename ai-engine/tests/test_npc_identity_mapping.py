"""Tests for NPC identity mapping — bidirectional NPC ID <-> Foundry actor UUID mapping.

Enables event enrichment, location tracking, and settlement queries by correlating
NPCs with their Foundry actor tokens.

Run:
    cd ai-engine && python -m pytest tests/test_npc_identity_mapping.py -v
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from npc.registry import NPCRegistry, NPCRecord


class TestNPCIdentityMapping:
    """Tests for bidirectional NPC ID <-> actor UUID mapping."""

    def test_map_actor_to_npc_creates_bidirectional_mapping(self):
        """Mapping an actor UUID to NPC ID creates a bidirectional link."""
        registry = NPCRegistry()
        registry.register_npc("mara", "Mara the Wise", "A mysterious wizard")

        success = registry.map_actor_to_npc("actor-uuid-123", "mara")

        assert success is True
        assert registry.get_npc_id_for_actor("actor-uuid-123") == "mara"
        assert registry.get_actor_uuid_for_npc("mara") == "actor-uuid-123"

    def test_map_actor_to_npc_fails_for_unknown_npc(self):
        """Mapping an unknown NPC ID returns False."""
        registry = NPCRegistry()

        success = registry.map_actor_to_npc("actor-uuid-123", "nonexistent")

        assert success is False
        assert registry.get_npc_id_for_actor("actor-uuid-123") is None

    def test_get_npc_by_actor_uuid_returns_npc_record(self):
        """Can retrieve NPC record by actor UUID."""
        registry = NPCRegistry()
        registry.register_npc("mara", "Mara the Wise", "A mysterious wizard")
        registry.map_actor_to_npc("actor-uuid-123", "mara")

        npc = registry.get_npc_by_actor_uuid("actor-uuid-123")

        assert npc is not None
        assert npc.npc_id == "mara"
        assert npc.npc_name == "Mara the Wise"

    def test_get_npc_by_actor_uuid_returns_none_for_unmapped_actor(self):
        """Retrieving unmapped actor UUID returns None."""
        registry = NPCRegistry()

        npc = registry.get_npc_by_actor_uuid("unknown-uuid")

        assert npc is None

    def test_find_npc_by_name_exact_match(self):
        """Fuzzy match finds exact name match (highest priority)."""
        registry = NPCRegistry()
        registry.register_npc("mara", "Mara the Wise", "A wizard")
        registry.register_npc("kess", "Kess the Swift", "A rogue")

        match = registry.find_npc_by_name_fuzzy("Mara the Wise")

        assert match is not None
        npc_id, npc_record = match
        assert npc_id == "mara"
        assert npc_record.npc_name == "Mara the Wise"

    def test_find_npc_by_name_case_insensitive(self):
        """Fuzzy match is case-insensitive."""
        registry = NPCRegistry()
        registry.register_npc("mara", "Mara the Wise", "A wizard")

        match = registry.find_npc_by_name_fuzzy("mara the wise")

        assert match is not None
        npc_id, _ = match
        assert npc_id == "mara"

    def test_find_npc_by_name_substring_match(self):
        """Fuzzy match finds substring matches (lower priority than exact)."""
        registry = NPCRegistry()
        registry.register_npc("mara", "Mara the Wise", "A wizard")
        registry.register_npc("kess", "Kess the Swift", "A rogue")

        # Partial name should still match
        match = registry.find_npc_by_name_fuzzy("Mara")

        assert match is not None
        npc_id, _ = match
        assert npc_id == "mara"

    def test_find_npc_by_name_no_match_below_threshold(self):
        """Fuzzy match returns None if similarity is below threshold."""
        registry = NPCRegistry()
        registry.register_npc("mara", "Mara the Wise", "A wizard")

        # Very different name
        match = registry.find_npc_by_name_fuzzy("Xyz")

        assert match is None

    def test_find_npc_by_name_returns_none_for_empty_input(self):
        """Fuzzy match returns None for empty or whitespace-only input."""
        registry = NPCRegistry()
        registry.register_npc("mara", "Mara the Wise", "A wizard")

        assert registry.find_npc_by_name_fuzzy("") is None
        assert registry.find_npc_by_name_fuzzy("   ") is None
        assert registry.find_npc_by_name_fuzzy(None) is None

    @pytest.mark.asyncio
    async def test_sync_with_foundry_maps_actors_to_npcs(self):
        """sync_with_foundry matches actors to NPCs and creates mappings."""
        registry = NPCRegistry()
        registry.register_npc("mara", "Mara the Wise", "A wizard")
        registry.register_npc("kess", "Kess the Swift", "A rogue")

        # Mock Foundry client
        foundry_client = AsyncMock()
        foundry_client.get_actors = AsyncMock(
            return_value=[
                {"uuid": "actor-uuid-1", "name": "Mara the Wise"},
                {"uuid": "actor-uuid-2", "name": "Kess the Swift"},
            ]
        )

        mapped_count = await registry.sync_with_foundry(foundry_client, "session-1")

        assert mapped_count == 2
        assert registry.get_npc_id_for_actor("actor-uuid-1") == "mara"
        assert registry.get_npc_id_for_actor("actor-uuid-2") == "kess"

    @pytest.mark.asyncio
    async def test_sync_with_foundry_handles_empty_actor_list(self):
        """sync_with_foundry gracefully handles empty actor list."""
        registry = NPCRegistry()
        registry.register_npc("mara", "Mara the Wise", "A wizard")

        foundry_client = AsyncMock()
        foundry_client.get_actors = AsyncMock(return_value=[])

        mapped_count = await registry.sync_with_foundry(foundry_client, "session-1")

        assert mapped_count == 0

    @pytest.mark.asyncio
    async def test_sync_with_foundry_skips_already_mapped_actors(self):
        """sync_with_foundry skips actors already mapped to NPCs."""
        registry = NPCRegistry()
        registry.register_npc("mara", "Mara the Wise", "A wizard")
        registry.register_npc("kess", "Kess the Swift", "A rogue")

        # Pre-map mara
        registry.map_actor_to_npc("actor-uuid-1", "mara")

        foundry_client = AsyncMock()
        foundry_client.get_actors = AsyncMock(
            return_value=[
                {"uuid": "actor-uuid-1", "name": "Mara the Wise"},  # Already mapped
                {"uuid": "actor-uuid-2", "name": "Kess the Swift"},  # New
            ]
        )

        mapped_count = await registry.sync_with_foundry(foundry_client, "session-1")

        # Should only count the new mapping (kess)
        assert mapped_count == 1
        assert registry.get_npc_id_for_actor("actor-uuid-1") == "mara"
        assert registry.get_npc_id_for_actor("actor-uuid-2") == "kess"

    @pytest.mark.asyncio
    async def test_sync_with_foundry_handles_missing_actor_data(self):
        """sync_with_foundry handles actors with missing name or UUID fields."""
        registry = NPCRegistry()
        registry.register_npc("mara", "Mara the Wise", "A wizard")

        foundry_client = AsyncMock()
        foundry_client.get_actors = AsyncMock(
            return_value=[
                {"uuid": "actor-uuid-1", "name": "Mara the Wise"},  # Good
                {"uuid": "actor-uuid-2"},  # Missing name
                {"name": "Unknown Actor"},  # Missing UUID
                {},  # Missing both
            ]
        )

        mapped_count = await registry.sync_with_foundry(foundry_client, "session-1")

        # Should only map the valid actor
        assert mapped_count == 1
        assert registry.get_npc_id_for_actor("actor-uuid-1") == "mara"

    @pytest.mark.asyncio
    async def test_sync_with_foundry_handles_foundry_error(self):
        """sync_with_foundry handles errors from Foundry client gracefully."""
        registry = NPCRegistry()
        registry.register_npc("mara", "Mara the Wise", "A wizard")

        foundry_client = AsyncMock()
        foundry_client.get_actors = AsyncMock(side_effect=Exception("Connection failed"))

        mapped_count = await registry.sync_with_foundry(foundry_client, "session-1")

        assert mapped_count == 0

    def test_clear_registry_clears_identity_mappings(self):
        """clear() removes all identity mappings."""
        registry = NPCRegistry()
        registry.register_npc("mara", "Mara the Wise", "A wizard")
        registry.map_actor_to_npc("actor-uuid-1", "mara")

        registry.clear()

        assert registry.get_npc_by_actor_uuid("actor-uuid-1") is None
        assert registry.get_actor_uuid_for_npc("mara") is None
        assert len(registry.list_npcs()) == 0

    def test_mapping_prevents_one_actor_to_multiple_npcs(self):
        """Attempting to map the same actor UUID to different NPCs updates the mapping."""
        registry = NPCRegistry()
        registry.register_npc("mara", "Mara the Wise", "A wizard")
        registry.register_npc("kess", "Kess the Swift", "A rogue")

        # Map actor to mara
        registry.map_actor_to_npc("actor-uuid-1", "mara")
        assert registry.get_npc_id_for_actor("actor-uuid-1") == "mara"

        # Re-map to kess (should update)
        registry.map_actor_to_npc("actor-uuid-1", "kess")
        assert registry.get_npc_id_for_actor("actor-uuid-1") == "kess"

    def test_mapping_prevents_one_npc_to_multiple_actors(self):
        """Attempting to map the same NPC to different actor UUIDs updates the mapping."""
        registry = NPCRegistry()
        registry.register_npc("mara", "Mara the Wise", "A wizard")

        # Map mara to actor-uuid-1
        registry.map_actor_to_npc("actor-uuid-1", "mara")
        assert registry.get_actor_uuid_for_npc("mara") == "actor-uuid-1"

        # Re-map to actor-uuid-2 (should update)
        registry.map_actor_to_npc("actor-uuid-2", "mara")
        assert registry.get_actor_uuid_for_npc("mara") == "actor-uuid-2"
