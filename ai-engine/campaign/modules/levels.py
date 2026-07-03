"""Levels — multi-floor scene configuration."""

from campaign.modules.registry import ModuleIntegration, register


def on_scene(scene: dict, mods: dict):
    module_flags = scene.get("module_flags", {})
    config = module_flags.get("levels")
    if config:
        return config
    if scene.get("has_multiple_floors") and scene.get("floors"):
        return {"sceneLevels": scene["floors"]}
    return None


register(ModuleIntegration(module_id="levels", on_scene=on_scene))
