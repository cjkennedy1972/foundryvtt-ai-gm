"""RPG-X Quest Log — rich quest metadata flags."""

from campaign.modules.registry import ModuleIntegration, register


def on_quest(quest: dict, mods: dict):
    return {
        "questGiver": quest.get("quest_giver", ""),
        "location": quest.get("location", ""),
        "difficulty": quest.get("difficulty", "medium"),
        "xpReward": quest.get("xp_reward", 0),
        "timeLimitDays": quest.get("time_limit_days", 0),
        "calendarDueDate": quest.get("calendar_due_date", {}),
    }


register(ModuleIntegration(module_id="rpgx-quest-log", on_quest=on_quest))
