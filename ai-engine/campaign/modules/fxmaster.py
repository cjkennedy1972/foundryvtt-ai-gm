"""FXMaster — Weather and visual mood overlays.

Provides particle effects (rain, snow, fog) and canvas color-grading filters
to match narrated environmental conditions. The engine can call this to shift
visual tone in sync with narration.
"""

from campaign.modules.registry import ModuleIntegration, register


register(ModuleIntegration(module_id="fxmaster"))
