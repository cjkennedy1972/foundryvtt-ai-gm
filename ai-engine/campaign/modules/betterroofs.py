"""Better Roofs — scene roof rendering flag."""

from campaign.modules.registry import ModuleIntegration, register


def on_scene(scene: dict, mods: dict):
    module_flags = scene.get("module_flags", {})
    config = module_flags.get("betterroofs")
    if config:
        return config
    if scene.get("has_roof"):
        return {"roofEnabled": True}
    return None


register(ModuleIntegration(module_id="betterroofs", on_scene=on_scene))
