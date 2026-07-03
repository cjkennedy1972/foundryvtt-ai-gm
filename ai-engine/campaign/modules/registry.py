"""ModuleIntegration registry — one entry per Foundry addon this engine
enriches deployed content for.

Replaces 47 inline '"x" in mods' checks that were scattered through
deploy_to_foundry / deploy_encounters (campaign/orchestrator.py). Each
addon's logic now lives in its own file under campaign/modules/ and
registers itself here; adding an addon integration is a new file, not an
edit to a 600-line function.

Two hook shapes, matching how the original code actually behaved:

- Flag hooks (on_journal, on_quest, on_scene, on_calendar_event,
  on_playlist, on_encounter_journal): `(entity_data, mods) -> dict | None`.
  Pure functions — return the flags to merge under entity.flags[module_id],
  or None to contribute nothing. No hook runs unless module_id is in mods
  (checked by run_flag_hook), and this shape is only safe because these
  entity types have no cross-module ordering dependencies in the original
  code (each module's contribution is independent of every other's).

- on_npc: `(ctx: NpcContext) -> None`, mutates ctx in place. NPC building
  is NOT independent across modules — midi-qol injects an attack bonus
  into weapon items autoanimations already created, so hooks run in
  MODULE_REGISTRY's iteration order (== registration order == the same
  sequence the original if-chain ran them in; see modules/__init__.py).

- on_loot_table: `async (table, mods) -> dict | None`. Only item-piles
  uses this; kept as its own shape since it's async (calls no I/O itself,
  but orchestrator.py awaits it) and returns a full Actor document, not a
  flags fragment.
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


@dataclass
class NpcContext:
    """Mutable state threaded through NPC-building module hooks.

    `system` and `flags` are the actual dicts that end up as the created
    Actor's "system" and "flags" fields — hooks mutate them in place rather
    than returning fragments, since some (vision-5e) need to reach into an
    already-populated nested structure (system["attributes"]).
    """
    npc: Dict[str, Any]
    mods: Dict[str, Any]
    flags: Dict[str, Any] = field(default_factory=dict)
    system: Dict[str, Any] = field(default_factory=dict)
    items: List[Dict[str, Any]] = field(default_factory=list)
    prototype_token: Dict[str, Any] = field(default_factory=dict)
    effects: List[Dict[str, Any]] = field(default_factory=list)


FlagHook = Callable[[dict, dict], Optional[dict]]


@dataclass
class ModuleIntegration:
    module_id: str
    on_npc: Optional[Callable[[NpcContext], None]] = None
    on_journal: Optional[FlagHook] = None
    on_quest: Optional[FlagHook] = None
    on_scene: Optional[FlagHook] = None
    on_calendar_event: Optional[FlagHook] = None
    on_playlist: Optional[FlagHook] = None
    on_encounter_journal: Optional[FlagHook] = None
    on_loot_table: Optional[Callable] = None  # async (table, mods) -> dict | None


MODULE_REGISTRY: "Dict[str, ModuleIntegration]" = {}


def register(integration: ModuleIntegration) -> None:
    MODULE_REGISTRY[integration.module_id] = integration


def run_npc_hooks(ctx: NpcContext) -> None:
    """Run every on_npc hook for modules active in ctx.mods, in registration order."""
    for module_id, integration in MODULE_REGISTRY.items():
        if module_id in ctx.mods and integration.on_npc:
            integration.on_npc(ctx)


def run_flag_hook(hook_name: str, entity_data: dict, mods: dict) -> Dict[str, Any]:
    """Run every `hook_name` hook for active modules; merge results.

    Returns {module_id: flags_dict} ready to `.update()` into an entity's
    flags dict alongside the "ai-gm" flags every entity already carries.
    """
    out: Dict[str, Any] = {}
    for module_id, integration in MODULE_REGISTRY.items():
        if module_id not in mods:
            continue
        hook = getattr(integration, hook_name, None)
        if not hook:
            continue
        result = hook(entity_data, mods)
        if result:
            out[module_id] = result
    return out
