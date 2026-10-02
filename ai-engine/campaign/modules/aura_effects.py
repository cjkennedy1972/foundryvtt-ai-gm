"""Aura Effects — draw spell and hazard zones.

Marks NPCs with spells that create auras (Spirit Guardians, poison clouds,
frightful presence) so Aura Effects can render visual zones on the battle map.
"""

from campaign.modules.registry import ModuleIntegration, NpcContext, register


def on_npc(ctx: NpcContext) -> None:
    """Mark aura-creating spells and abilities on NPCs."""
    npc = ctx.npc
    auras = npc.get("auras", [])
    if auras:
        ctx.prototype_token.setdefault("flags", {})["aura-effects"] = {
            "auras": auras
        }


register(ModuleIntegration(module_id="aura-effects", on_npc=on_npc))
