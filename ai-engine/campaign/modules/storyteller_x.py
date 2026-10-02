"""StoryTeller X — journals that open as a book.

The module registers journal sheets through `Journal.registerSheet("journals", ...)`, so the open-book sheet's id is
`journals.StorySheet`, NOT `story-teller-x.StorySheet` (its own code mentions the latter, but checked live in Foundry a
journal flagged with it silently opens as the plain dnd5e sheet). A journal uses a sheet through core's
`flags.core.sheetClass`, which sits outside the module-id flag namespace the registry's flag hooks write to, so this is a
helper the deploy path calls on the flags of a journal it is about to create.
"""

from typing import Any, Dict

from campaign.modules.registry import ModuleIntegration, register

MODULE_ID = "story-teller-x"
BOOK_SHEET = "journals.StorySheet"


def book_sheet_flags(mods: Any) -> Dict[str, Any]:
    """`{"core": {"sheetClass": ...}}` when StoryTeller X is active, else nothing."""
    return {"core": {"sheetClass": BOOK_SHEET}} if MODULE_ID in (mods or ()) else {}


register(ModuleIntegration(module_id=MODULE_ID))
