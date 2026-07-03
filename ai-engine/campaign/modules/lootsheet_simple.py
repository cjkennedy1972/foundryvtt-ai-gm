"""Loot Sheet Simple — merchant sheet fallback when Item Piles isn't active."""

from campaign.modules.registry import ModuleIntegration, NpcContext, register


def on_npc(ctx: NpcContext) -> None:
    if "item-piles" not in ctx.mods and ctx.npc.get("npc_type") == "merchant":
        ctx.flags["lootsheet-simple"] = {"lootsheettype": "Merchant"}


register(ModuleIntegration(module_id="lootsheet-simple", on_npc=on_npc))
