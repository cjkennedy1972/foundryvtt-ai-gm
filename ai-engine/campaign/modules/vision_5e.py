"""Vision 5e — darkvision/blindsight/tremorsense/truesight from NPC senses."""

from campaign.modules.registry import ModuleIntegration, NpcContext, register


def on_npc(ctx: NpcContext) -> None:
    senses = ctx.npc.get("senses")
    if not senses:
        return
    ctx.system.setdefault("attributes", {})["senses"] = {
        "darkvision": senses.get("darkvision", 0),
        "blindsight": senses.get("blindsight", 0),
        "tremorsense": senses.get("tremorsense", 0),
        "truesight": senses.get("truesight", 0),
        "units": "ft",
    }


register(ModuleIntegration(module_id="vision-5e", on_npc=on_npc))
