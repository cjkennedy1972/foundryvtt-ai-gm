"""SmallTime — in-world time-of-day display config."""

from campaign.modules.registry import ModuleIntegration, register


def on_scene(scene: dict, mods: dict):
    module_flags = scene.get("module_flags", {})
    config = module_flags.get("smalltime")
    if config:
        return config
    if scene.get("time_of_day") is not None:
        return {
            "timeOfDay": scene.get("time_of_day", 12),
            "timePeriod": scene.get("time_period", "afternoon"),
        }
    return None


register(ModuleIntegration(module_id="smalltime", on_scene=on_scene))
