"""Bossbar — a dramatic on-screen health bar for boss actors.

Bossbar renders a large health bar for any actor listed in the *scene* flag
``flags.bossbar.actors`` (each entry ``{uuid, style}`` — schema verified live on
Foundry v14). An NPC generated with ``boss: true`` gets an ``aigm.boss``
actor-flag marker here; the combat loop reads that marker at encounter start and
adds the boss to the active scene's bossbar list, clearing it when combat ends
(see combat/loop.py). This keeps the spectacle scoped to the boss fight.
"""

from campaign.modules.registry import ModuleIntegration, NpcContext, register


def on_npc(ctx: NpcContext) -> None:
    # Mark climactic villains so the combat loop can spotlight them. Keep it to
    # true bosses — a bar on every mook defeats the drama.
    if ctx.npc.get("boss"):
        ctx.flags.setdefault("aigm", {})["boss"] = True


register(ModuleIntegration(module_id="bossbar", on_npc=on_npc))
