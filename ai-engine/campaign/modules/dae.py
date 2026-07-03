"""Dynamic Active Effects (DAE) — active effects on NPCs, plus encounter journal flags.

times-up isn't its own top-level integration: it only ever modified DAE's
per-effect duration field in the original code, so it's checked here as a
modifier, not registered as a standalone module.
"""

from campaign.modules.registry import ModuleIntegration, NpcContext, register


def on_npc(ctx: NpcContext) -> None:
    active_effects = ctx.npc.get("active_effects")
    if not isinstance(active_effects, list):
        return
    for ae in active_effects:
        effect_data = {
            "name": ae.get("name") or ae.get("label") or "Effect",
            "icon": ae.get("icon", "icons/svg/aura.svg"),
            "description": ae.get("description", ""),
            "disabled": ae.get("disabled", False),
            "transfer": ae.get("transfer", True),
            "changes": ae.get("changes", []),
        }
        if "times-up" in ctx.mods and ae.get("duration"):
            effect_data["duration"] = ae["duration"]
        ctx.effects.append(effect_data)


def on_encounter_journal(enc: dict, mods: dict):
    return {"enable_active_effects": True, "track_conditions": True}


register(ModuleIntegration(module_id="dae", on_npc=on_npc, on_encounter_journal=on_encounter_journal))
