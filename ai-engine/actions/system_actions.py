"""Session and engine control: arbitrary JS, pause/resume, macros.

Split out of actions/executors.py, which had grown to 2,445 lines. The
functions are moved verbatim; executors.py still owns the ACTION_HANDLERS
dispatch table and re-exports these names, so existing imports keep working.
"""

import asyncio
import html
import logging
from pathlib import Path
from typing import Any, Optional

from actions.executors_shared import ExecutionError, _extract_token_id, _require
from config import settings
from foundry.client import FoundryClient
from tts import playback as tts_playback
from utils.tasks import spawn

logger = logging.getLogger(__name__)


async def execute_execute_js(
    code: str,
    description: Optional[str] = None,
    foundry: FoundryClient = None,
    source: Optional[str] = None,
) -> dict:
    """Execute arbitrary JavaScript in the Foundry client.

    Disabled unless ``allow_execute_js`` is set. This action is reachable from
    player chat via the LLM, so an always-on bridge to arbitrary Foundry JS lets
    a prompt-injected message run destructive scripts against the world.
    """
    from config import settings as _settings
    desc = description or (code[:60] if code else "<empty>")
    if not code or not code.strip():
        logger.warning("[JS] execute_js called with empty code")
        return {"type": "execute_js", "description": desc, "success": False, "error": "Code is empty"}
    if not getattr(_settings, "allow_execute_js", False):
        logger.warning(f"[JS] Blocked execute_js (allow_execute_js=false): {desc}")
        return {
            "type": "execute_js",
            "description": desc,
            "success": False,
            "error": "execute_js is disabled. Set ALLOW_EXECUTE_JS=true to enable arbitrary Foundry JavaScript.",
        }
    logger.info(f"[JS] Executing: {desc}")
    if not foundry or not foundry.is_connected:
        logger.error("[JS] execute_js called with disconnected Foundry client")
        return {"type": "execute_js", "description": desc, "success": False, "error": "Foundry is not connected"}
    result = await foundry.execute_js(code)
    return {"type": "execute_js", "description": desc, "result": result}


async def execute_pause_game(
    reason: Optional[str] = None,
    foundry: FoundryClient = None,
    app_state=None,
    source: Optional[str] = None,
) -> dict:
    """Pause both the AI-GM and FoundryVTT."""
    # Pause AI processing
    chat_listener = getattr(app_state, "chat_listener", None)
    if chat_listener:
        chat_listener._running = False

    # Pause Foundry for all players. Fixed, non-parameterized snippet, so
    # ALLOW_EXECUTE_JS does not apply: it gates the LLM-driven execute_js
    # action above, not first-party calls like this one.
    if foundry:
        try:
            await foundry.execute_js("if(!game.paused){game.togglePause(true,true);}")
        except Exception as e:
            logger.warning(f"[Pause] Foundry pause failed: {e}")

    if reason:
        try:
            await foundry.chat_message(f"*{reason}*", speaker="GM")
        except Exception:
            logger.debug("[Pause] Could not post the pause notice to chat", exc_info=True)

    logger.info(f"[Pause] Game paused. reason={reason!r}")
    return {"type": "pause_game", "reason": reason}


async def execute_resume_game(
    foundry: FoundryClient = None,
    app_state=None,
    source: Optional[str] = None,
) -> dict:
    """Resume both the AI-GM and FoundryVTT."""
    # Resume AI processing
    chat_listener = getattr(app_state, "chat_listener", None)
    if chat_listener:
        chat_listener._running = True

    # Unpause Foundry for all players. Fixed snippet; see execute_pause_game.
    if foundry:
        try:
            await foundry.execute_js("if(game.paused){game.togglePause(false,true);}")
        except Exception as e:
            logger.warning(f"[Resume] Foundry unpause failed: {e}")

    logger.info("[Resume] Game resumed.")
    return {"type": "resume_game"}


async def execute_execute_macro(
    macro_id: str,
    overrides: Optional[dict] = None,
    app_state=None,
    source: Optional[str] = None,
) -> dict:
    """Execute a registered GM macro for automation (music cues, effect setup, etc.).

    Resolves the macro to a concrete action and dispatches it, so the macro's
    payload gets the same schema validation and audit trail as a directly
    issued action. This previously called effects_manager.execute_macro(),
    which does not exist on EffectsManager — every invocation raised
    AttributeError and returned success=False, so no macro had ever run.
    """
    _require(
        app_state and getattr(app_state, "macro_manager", None),
        "Macro manager not available — cannot execute macro"
    )
    dispatcher = getattr(app_state, "action_dispatcher", None)
    _require(dispatcher, "Action dispatcher not available — cannot execute macro")

    action = app_state.macro_manager.resolve_macro(macro_id, overrides=overrides or {})
    if source:
        action["source"] = source
    if action.get("error"):
        logger.warning(f"[Macro] {macro_id} not executed: {action['error']}")
        return {"type": "execute_macro", "macro_id": macro_id, "success": False, "error": action["error"]}

    result = await dispatcher.execute(action)
    return {
        "type": "execute_macro",
        "macro_id": macro_id,
        "action_type": action["type"],
        "success": bool(result.get("success")),
        "error": result.get("error"),
        "result": result,
    }
