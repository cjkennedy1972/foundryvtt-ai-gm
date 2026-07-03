"""Simple Calendar Reborn — calendar event note data.

Module id is "foundryvtt-simple-calendar-reborn" (the file is named for
readability; Foundry module ids aren't valid Python module names as-is).
"""

from campaign.modules.registry import ModuleIntegration, register


def on_calendar_event(event: dict, mods: dict):
    return {
        "noteData": {
            "year": event.get("year", 1),
            "month": event.get("month", 1) - 1,
            "day": event.get("day", 1) - 1,
            "allDay": True,
            "playerVisible": event.get("visible_to_players", True),
            "categories": [event.get("type", "event")],
        }
    }


register(ModuleIntegration(
    module_id="foundryvtt-simple-calendar-reborn",
    on_calendar_event=on_calendar_event,
))
