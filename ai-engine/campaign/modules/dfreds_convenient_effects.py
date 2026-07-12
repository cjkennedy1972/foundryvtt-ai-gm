"""DFreds Convenient Effects integration.

Automatically applies mechanical status effects when conditions are applied.
This ensures that applying a condition (poisoned, blind, prone, etc.) has
real mechanical consequences in the game, not just a cosmetic icon.

DFreds Convenient Effects provides real ActiveEffects for standard D&D 5e
conditions that modify attack rolls, saves, and other mechanical aspects
automatically.
"""

from campaign.modules.registry import ModuleIntegration, NpcContext, register


def on_npc(ctx: NpcContext) -> None:
    # DFreds requires no special NPC configuration — it auto-applies effects
    # when conditions are added via the dnd5e system status effect IDs.
    # Just mark that this module is active so the engine knows conditions
    # will have mechanical weight.
    ctx.prototype_token.setdefault("flags", {})["dfrds-convenient-effects"] = {"enabled": True}


register(ModuleIntegration(module_id="dfreds-convenient-effects", on_npc=on_npc))
