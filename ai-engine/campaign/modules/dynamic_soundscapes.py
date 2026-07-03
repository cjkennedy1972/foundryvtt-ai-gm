"""Dynamic Soundscapes — scene ambient audio config + playlist ambient flag."""

from campaign.modules.registry import ModuleIntegration, register


def on_scene(scene: dict, mods: dict):
    module_flags = scene.get("module_flags", {})
    config = module_flags.get("dynamic-soundscapes")
    if config:
        return config
    if scene.get("soundscape", "none") != "none":
        return {
            "ambient": True,
            "preset": scene.get("soundscape", ""),
            "volume": scene.get("soundscape_volume", 0.6),
        }
    return None


def on_playlist(pl: dict, mods: dict):
    return {"ambient": True}


register(ModuleIntegration(
    module_id="dynamic-soundscapes",
    on_scene=on_scene,
    on_playlist=on_playlist,
))
