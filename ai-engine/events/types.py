"""Typed event names and their state-projection reducers.

Events are stored as (type: str, payload: dict) in persistence/db.py's
`events` table (see persistence/migrations.py migration 1). This module is
the single place that knows how each type folds into a `game_state` dict —
EventStore.replay() applies these in order to rebuild state from history
instead of relying on in-place mutation.

A reducer takes (state, payload) and returns a NEW state dict — it must not
mutate `state` in place, since replay() reuses the same dict reference
across many events for a large event log.

NPC EVENT ENRICHMENT:
NPC-related events (NPC_MOVED, RELATIONSHIP_CHANGED) optionally carry an
actor_uuid field that correlates the NPC to its Foundry actor token. This
enables location tracking and settlement queries during gameplay.

Example NPC_MOVED payload:
  {
    "npc_id": "mara",
    "location": "tavern",
    "actor_uuid": "actor.123...",  # optional: set if NPC is mapped to an actor
  }
"""

from typing import Any, Callable, Dict

NPC_MOVED = "npc_moved"
RELATIONSHIP_CHANGED = "relationship_changed"
FACT_CANONIZED = "fact_canonized"
TIME_ADVANCED = "time_advanced"
ACTION_RESOLVED = "action_resolved"
SOLO_DEATH_SETBACK = "solo_death_setback"
LEGACY_NOTE = "legacy_note"  # pre-Phase-2 rows, backfilled by migration 1


def _reduce_npc_moved(state: Dict[str, Any], payload: dict) -> Dict[str, Any]:
    npcs = dict(state.get("npcs", {}))
    npc = dict(npcs.get(payload["npc_id"], {}))
    npc["location"] = payload["location"]
    npcs[payload["npc_id"]] = npc
    return {**state, "npcs": npcs}


def _reduce_relationship_changed(state: Dict[str, Any], payload: dict) -> Dict[str, Any]:
    key = f'{payload["source_id"]}->{payload["target_id"]}'
    relationships = dict(state.get("relationships", {}))
    relationships[key] = {
        "type": payload.get("relationship_type"),
        "strength": payload.get("strength"),
    }
    return {**state, "relationships": relationships}


def _reduce_fact_canonized(state: Dict[str, Any], payload: dict) -> Dict[str, Any]:
    facts = list(state.get("canon_facts", []))
    facts.append(payload["fact"])
    return {**state, "canon_facts": facts}


def _reduce_time_advanced(state: Dict[str, Any], payload: dict) -> Dict[str, Any]:
    elapsed = state.get("world_time_elapsed_seconds", 0) + payload.get("duration_seconds", 0)
    return {**state, "world_time_elapsed_seconds": elapsed}


def _reduce_action_resolved(state: Dict[str, Any], payload: dict) -> Dict[str, Any]:
    log = list(state.get("resolved_actions", []))
    log.append(payload)
    return {**state, "resolved_actions": log}


def _reduce_solo_death_setback(state: Dict[str, Any], payload: dict) -> Dict[str, Any]:
    setbacks = list(state.get("solo_death_setbacks", []))
    setbacks.append(payload)
    return {**state, "solo_death_setbacks": setbacks}


def _reduce_noop(state: Dict[str, Any], payload: dict) -> Dict[str, Any]:
    return state


REDUCERS: Dict[str, Callable[[Dict[str, Any], dict], Dict[str, Any]]] = {
    NPC_MOVED: _reduce_npc_moved,
    RELATIONSHIP_CHANGED: _reduce_relationship_changed,
    FACT_CANONIZED: _reduce_fact_canonized,
    TIME_ADVANCED: _reduce_time_advanced,
    ACTION_RESOLVED: _reduce_action_resolved,
    SOLO_DEATH_SETBACK: _reduce_solo_death_setback,
    LEGACY_NOTE: _reduce_noop,
}
