"""Combat Booster — encounter journal display flags."""

from campaign.modules.registry import ModuleIntegration, register


def on_encounter_journal(enc: dict, mods: dict):
    return {
        "encounterNote": True,
        "difficulty": enc.get("difficulty", "medium"),
        "xp_reward": enc.get("xp_award", 0),
        "show_encounter_status": True,
    }


register(ModuleIntegration(module_id="combatbooster", on_encounter_journal=on_encounter_journal))
