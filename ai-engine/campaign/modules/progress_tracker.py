"""Progress Tracker — quest objective/completion tracking flags."""

from campaign.modules.registry import ModuleIntegration, register


def on_quest(quest: dict, mods: dict):
    return {
        "enabled": True,
        "status": quest.get("status", "not-started"),
        "objectives": len(quest.get("objectives", [])),
        "completed": 0,
    }


register(ModuleIntegration(module_id="progress-tracker", on_quest=on_quest))
