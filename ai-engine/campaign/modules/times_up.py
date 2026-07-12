"""Times Up — Automatic effect expiration by turn/round.

Automatically expires ActiveEffects when their duration runs out (by turn
count or round count). Pairs with DAE to handle effect cleanup automatically.
"""

from campaign.modules.registry import ModuleIntegration, register


register(ModuleIntegration(module_id="times-up"))
