"""Fog Weaver — atmospheric fog overlay configuration."""

from campaign.modules.registry import ModuleIntegration, register


def on_scene(scene: dict, mods: dict):
    module_flags = scene.get("module_flags", {})
    config = module_flags.get("fog-weaver")
    if config:
        return config
    if scene.get("fog_type", "none") != "none":
        return {
            "fogType": scene.get("fog_type", "light_fog"),
            "fogDensity": scene.get("fog_density", 0.2),
            "enabled": True,
        }
    return None


register(ModuleIntegration(module_id="fog-weaver", on_scene=on_scene))
