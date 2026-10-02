"""Storyteller's Cinema — a scene opens in cinematic mode when it has a cinematic background to show.

Flags go under `storyteller-cinema` on the scene (see immersion/cinema.py). A scene gets a cinematic default only when
the build produced an establishing image for it (`_cinematic_bg_src`): a stage with no backdrop is just a hidden map.
"""

from campaign.modules.registry import ModuleIntegration, register

SOCIAL_SCENE_TYPES = {"tavern", "settlement", "shop", "castle", "temple", "village", "city", "inn", "court"}


def on_scene(scene: dict, mods: dict):
    config = (scene.get("module_flags") or {}).get("storyteller-cinema")
    if config:
        return config
    background = scene.get("_cinematic_bg_src")
    if not background:
        return None
    social = str(scene.get("type", "")).lower() in SOCIAL_SCENE_TYPES
    return {"cinematicBg": background, "cinematicBgDim": 0.0, "viewMode": "cinematic" if social else "battlemap"}


register(ModuleIntegration(module_id="storyteller-cinema", on_scene=on_scene))
