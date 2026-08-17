"""Action audit trail — classify and record what the AI-GM actually did.

The AI-GM runs unattended, so there is nobody to gate an action on: a
"pending approval" queue in this process has no reviewer and only stalls the
table. What is genuinely useful after the fact is the record — which
mechanically consequential things the AI did, with what parameters, and
whether they succeeded — so a GM reading the log afterwards can see how the
world changed and why.

This module owns two things and nothing else:

* CONSEQUENTIAL_ACTIONS — the classification. Every name in it MUST be a real
  key in actions.executors.ACTION_HANDLERS; tests/test_action_audit.py
  enforces that, because the previous (approval) version of this set had
  drifted to nine names that no longer existed as actions, which silently
  disabled the classification entirely.
* audit_record() — the per-action log line plus the payload the durable trail
  is built from.

Durable storage is the existing event log, not a second store: the dispatcher
stamps each result with `_audit`, and ChatListener folds that into the
ACTION_RESOLVED event it already appends (events/store.py). That covers every
action the autonomous loop takes. Actions dispatched directly from the REST
API are human-initiated and recorded by the HTTP access log instead.
"""

import json
import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


# Actions that change the mechanical state of the world — hit points,
# conditions, resources, inventory, character sheets, or arbitrary code.
# These are logged at INFO with their parameters; everything else (narration,
# camera, audio, queries) is DEBUG.
CONSEQUENTIAL_ACTIONS = frozenset({
    "apply_condition",
    "apply_token_effect",
    "attack_with_item",
    "cast_spell",
    "death_save",
    "end_encounter",
    "execute_js",
    "grant_inspiration",
    "grapple",
    "long_rest",
    "set_exhaustion",
    "short_rest",
    "start_encounter",
    "update_hp",
    "use_action",
    "use_save_item",
})

# Keys the dispatcher injects into handler kwargs — infrastructure, not
# parameters the LLM chose, and not serializable.
_INJECTED_KEYS = ("foundry", "app_state")

# Parameter summaries are for humans reading a log; execute_js code and
# generated prose would otherwise dominate the line.
_MAX_PARAM_CHARS = 400


def is_consequential(action_type: str) -> bool:
    """True if *action_type* mutates mechanical game state."""
    return action_type in CONSEQUENTIAL_ACTIONS


def summarize_params(params: Dict[str, Any]) -> str:
    """Render handler kwargs as a bounded, log-safe one-liner."""
    safe = {k: v for k, v in params.items() if k not in _INJECTED_KEYS}
    try:
        text = json.dumps(safe, default=str, sort_keys=True)
    except (TypeError, ValueError):
        text = repr(safe)
    if len(text) > _MAX_PARAM_CHARS:
        text = text[:_MAX_PARAM_CHARS] + "…"
    return text


def audit_record(action_type: str, params: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
    """Log *action_type*'s outcome and return the record for the event log.

    Consequential actions log at INFO on success and WARNING on failure, so a
    failed mechanical change is visible without turning on debug logging.
    """
    consequential = is_consequential(action_type)
    params_summary = summarize_params(params)
    succeeded = bool(result.get("success", True))

    message = "[Audit] %s %s params=%s" % (
        action_type,
        "ok" if succeeded else f"FAILED ({result.get('error')})",
        params_summary,
    )
    if not consequential:
        logger.debug(message)
    elif succeeded:
        logger.info(message)
    else:
        logger.warning(message)

    return {
        "consequential": consequential,
        "params": params_summary,
    }
