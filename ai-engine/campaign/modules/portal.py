"""Portal — spawn summons and reinforcements with Portal's placement effects.

When Portal is active, summons and reinforcements use Portal's teleport
and placement effects instead of simply appearing on the map.
"""

from campaign.modules.registry import ModuleIntegration, NpcContext, register


def on_npc(ctx: NpcContext) -> None:
    """Mark summons and reinforcements for Portal teleport effects."""
    npc = ctx.npc
    is_summon = npc.get("is_summon") or npc.get("reinforcement_type")
    if is_summon:
        ctx.prototype_token.setdefault("flags", {})["portal"] = {
            "placementEffect": True,
            "sourceType": "teleport",
        }


register(ModuleIntegration(module_id="portal", on_npc=on_npc))
