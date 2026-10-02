"""Light Switch — generated scenes place torches and lanterns players can turn on/off.

Converts the scene's lights array into Light Switch-compatible objects so
players can interactively control torches, lanterns, and magical light sources.
"""

from campaign.modules.registry import ModuleIntegration, register


def on_scene(scene: dict, mods: dict):
    """Create Light Switch flags for scene lights if they exist."""
    setup = scene.get("scene_setup", {})
    lights = setup.get("lights", [])
    if not lights:
        return None

    # ponytail: Light Switch requires lights to be objects in the scene;
    # the lights array is positional data. Store the config for the relay's
    # world_cli_writes or future scene enrichment to convert to Light objects.
    return {
        "lights": lights,
        "interactive": True,
    }


register(ModuleIntegration(module_id="light-switch", on_scene=on_scene))
