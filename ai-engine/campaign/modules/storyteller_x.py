"""StoryTeller X — journals that open as a book.

The module registers journal sheets (`story-teller-x.StorySheet` is the open-book one). A journal uses a sheet through
core's `flags.core.sheetClass`, which sits outside the module-id flag namespace the registry's flag hooks write to,
so this is a helper the deploy path calls on the flags of a journal it is about to create.
"""

from typing import Any, Dict

from campaign.modules.registry import ModuleIntegration, register

MODULE_ID = "story-teller-x"
BOOK_SHEET = "story-teller-x.StorySheet"


def book_sheet_flags(mods: Any) -> Dict[str, Any]:
    """`{"core": {"sheetClass": ...}}` when StoryTeller X is active, else nothing."""
    return {"core": {"sheetClass": BOOK_SHEET}} if MODULE_ID in (mods or ()) else {}


register(ModuleIntegration(module_id=MODULE_ID))
