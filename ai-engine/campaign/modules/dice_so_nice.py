"""Dice So Nice! — 3D dice rolling visualization.

Renders animated 3D dice on the canvas when rolls are made, making combat
feel more tactile and dramatic. No configuration needed — works automatically
when installed.
"""

from campaign.modules.registry import ModuleIntegration, register


register(ModuleIntegration(module_id="dice-so-nice"))
