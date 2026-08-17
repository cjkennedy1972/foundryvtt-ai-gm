"""Persist NPCRegistry to SQLite (npc_records — see
persistence/migrations.py migration 2) so NPC memory/goals survive an
engine restart.

NPCRegistry itself stays synchronous and in-memory (many call sites
register/mutate NPCs outside any async context — see chat_listener.py,
context/loader.py, api/routes/npc.py). This module is the explicit,
async boundary: call `load()` once at startup and `save()` at natural
checkpoints (e.g. `/gm end session`), rather than persisting on every
mutation.
"""

import dataclasses
import logging

from npc.goals import Goal
from npc.registry import NPCRecord, NPCRegistry, NPCRelationship
from persistence.db import Database

logger = logging.getLogger(__name__)


async def save(db: Database, campaign: str, registry: NPCRegistry) -> None:
    """Upsert every NPC currently in *registry* under *campaign*."""
    for npc in registry.list_npcs():
        await db.upsert_npc_record(npc.npc_id, campaign, dataclasses.asdict(npc))
    logger.info(f"Saved {len(registry.npcs)} NPC record(s) for campaign '{campaign}'")


async def load(db: Database, campaign: str) -> NPCRegistry:
    """Rebuild an NPCRegistry from npc_records for *campaign*. Returns an
    empty registry if none are stored yet (first run, or a campaign that
    predates Phase 3 — the caller's existing seed path fills it from
    scratch as before)."""
    registry = NPCRegistry()
    for data in await db.get_npc_records(campaign):
        relationships = {
            target_id: NPCRelationship(**rel)
            for target_id, rel in data.get("relationships", {}).items()
        }
        goals = [Goal(**g) for g in data.get("goals", [])]
        # data came from dataclasses.asdict(npc) at save time, so its keys
        # already match every NPCRecord field — spread it rather than
        # hand-listing fields a second time (which silently drops any field
        # added to NPCRecord later without a matching update here).
        record = NPCRecord(**{**data, "relationships": relationships, "goals": goals})
        registry.npcs[record.npc_id] = record
        for rel in relationships.values():
            registry.relationships[(rel.source_id, rel.target_id)] = rel

    logger.info(f"Loaded {len(registry.npcs)} NPC record(s) for campaign '{campaign}'")
    return registry
