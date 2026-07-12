"""Monk's TokenBar — Group rolls and party UI.

Enables the engine to request group skill checks, group saves, and group
initiative rolls via a single call, reducing sequential action dispatch and
providing unified visual feedback to players.
"""

from campaign.modules.registry import ModuleIntegration, register


register(ModuleIntegration(module_id="monks-tokenbar"))
