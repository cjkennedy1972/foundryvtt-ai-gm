"""Patrol — waypoint routes for guard NPCs."""

from campaign.modules.registry import ModuleIntegration, NpcContext, register


def on_npc(ctx: NpcContext) -> None:
    npc = ctx.npc
    if npc.get("npc_type") != "guard":
        return
    config = {
        "active": True,
        "speed": npc.get("patrol_speed", 1),
        "pause": npc.get("patrol_pause", 3000),
    }
    if npc.get("patrol_route"):
        config["route"] = npc["patrol_route"]
    ctx.prototype_token.setdefault("flags", {})["patrol"] = config


register(ModuleIntegration(module_id="patrol", on_npc=on_npc))
