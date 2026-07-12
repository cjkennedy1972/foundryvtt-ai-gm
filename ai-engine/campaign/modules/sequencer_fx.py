"""Sequencer — Cinematic FX orchestration toolkit.

Enables execution of complex, choreographed visual effects (explosions,
portal opens, spell animations) via `execute_js`. The engine can call
Sequencer to trigger dramatic moments at precise narrative timing.
"""

from campaign.modules.registry import ModuleIntegration, register


register(ModuleIntegration(module_id="sequencer"))
