"""Importing this package populates registry.MODULE_REGISTRY with every
known addon integration (each submodule calls register() at import time).

Import order matters for one pair: autoanimations must register before
midi_qol, since midi_qol's on_npc hook reaches into weapon items
autoanimations already added to the shared NpcContext. Everything else is
independent of order — see registry.py's docstring for why NPC hooks are
order-sensitive at all while flag hooks (scene/journal/quest/...) aren't.
"""

from campaign.modules import (  # noqa: F401
    autoanimations,
    midi_qol,
    mmm,
    item_piles,
    lootsheet_simple,
    token_notes,
    polyglot,
    patrol,
    vision_5e,
    dae,
    dynamic_soundscapes,
    levels,
    betterroofs,
    fog_weaver,
    smalltime,
    simple_calendar,
    progress_tracker,
    rpgx_quest_log,
    bossbar,
    dfreds_convenient_effects,
    dice_so_nice,
    times_up,
    sequencer_fx,
    fxmaster,
    monks_tokenbar,
)

from campaign.modules.registry import MODULE_REGISTRY  # noqa: F401, E402
