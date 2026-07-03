"""Maxwell's Manual of Malicious Maladies — condition tracking flags."""

from campaign.modules.registry import ModuleIntegration, NpcContext, register


def on_npc(ctx: NpcContext) -> None:
    conditions = ctx.npc.get("conditions")
    if isinstance(conditions, list):
        ctx.flags["mmm"] = {
            "track_conditions": True,
            "active_conditions": conditions,
        }


register(ModuleIntegration(module_id="mmm", on_npc=on_npc))
