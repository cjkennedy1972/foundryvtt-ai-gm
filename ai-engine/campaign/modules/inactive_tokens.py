"""Inactive Tokens — place ambushers and hidden NPCs as inactive tokens.

When Inactive Tokens is active, guards and monsters marked as hidden/ambushers
are placed as inactive (not visible) and activated when revealed during play.
"""

from campaign.modules.registry import ModuleIntegration, NpcContext, register


def on_npc(ctx: NpcContext) -> None:
    """Mark hidden/ambusher NPCs as inactive tokens."""
    npc = ctx.npc
    is_hidden = npc.get("hidden") or npc.get("is_ambusher")
    if is_hidden:
        ctx.prototype_token.setdefault("flags", {})["inactive-tokens"] = {
            "inactive": True
        }


register(ModuleIntegration(module_id="inactive-tokens", on_npc=on_npc))
