"""
Action Dispatcher — routes LLM action requests to the appropriate executor.

All action payloads are validated against Pydantic schemas *before*
handlers are called, so extra/misnamed LLM keys cannot leak into
Foundry and numeric fields are clamped to game-safe bounds.
"""

import inspect
import logging
from typing import Dict, Any, List

from actions.executors import ACTION_HANDLERS
from actions.schemas import ACTION_SCHEMAS, MIN_DAMAGE, MAX_DAMAGE
from actions.approval import ApprovalStatus
from config import settings
from foundry.client import FoundryClient

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _validate_action(action_type: str, payload: Dict[str, Any]):
    """Validate *payload* against the Pydantic model for *action_type*.

    Returns (validated_kwargs, error_msg).  On success error_msg is None.
    Validation is STRICT: no unknown fields, no type coercion surprises.
    """
    schema_cls = ACTION_SCHEMAS.get(action_type)
    if not schema_cls:
        return None, f"No schema for action type: {action_type}"

    try:
        # Reject unknown fields to catch LLM hallucinations early
        allowed_fields = set(schema_cls.model_fields.keys())
        unknown_fields = set(k for k in payload.keys() if k not in allowed_fields and k != "type")
        if unknown_fields:
            return None, f"Unknown fields in action '{action_type}': {', '.join(sorted(unknown_fields))}"

        # Remove 'type' field from payload before validation since schemas don't define it
        payload_for_validation = {k: v for k, v in payload.items() if k != "type"}
        validated = schema_cls(**payload_for_validation)
    except Exception as exc:
        return None, f"Validation error for action '{action_type}': {exc}"

    # model_dump(exclude_unset=True) drops any schema-level defaults
    # so only fields the LLM actually sent are passed on.
    kwargs = validated.model_dump(exclude_unset=True)
    return kwargs, None


def _clamp_damage(value: int) -> tuple[int, str | None]:
    """Clamp *value* to [MIN_DAMAGE, MAX_DAMAGE] and return reason or None."""
    reason = None
    if value < MIN_DAMAGE:
        reason = f"damage clamped from {value} to {MIN_DAMAGE}"
        value = MIN_DAMAGE
    elif value > MAX_DAMAGE:
        reason = f"damage clamped from {value} to {MAX_DAMAGE}"
        value = MAX_DAMAGE
    return value, reason


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


class ActionDispatcher:
    def __init__(self, foundry_client: FoundryClient, app_state = None, approval_workflow = None):
        self.foundry = foundry_client
        self.app_state = app_state
        self.approval_workflow = approval_workflow

    async def execute(self, action: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a single action with schema validation."""
        action_type = action.get("type")
        if not action_type:
            return {"error": "No action type specified", "raw": action}

        handler = ACTION_HANDLERS.get(action_type)
        if not handler:
            logger.warning(f"Unknown action type: {action_type}")
            return {
                "type": action_type,
                "error": f"Unknown action type: {action_type}",
                "success": False,
            }

        # --- validation / whitelist -----------------------------------------
        kwargs, error_msg = _validate_action(action_type, action)
        if error_msg:
            logger.warning(f"Action rejected ({action_type}): {error_msg}")
            return {
                "type": action_type,
                "error": error_msg,
                "success": False,
            }

        # --- allow_execute_js gate — must come BEFORE dispatch -----------
        if action_type == "execute_js":
            if not getattr(settings, "allow_execute_js", False):
                logger.warning(
                    "execute_js rejected: ALLOW_EXECUTE_JS is not enabled. "
                    "To enable, set ALLOW_EXECUTE_JS=true in .env."
                )
                return {
                    "type": action_type,
                    "error": "Arbitrary JavaScript execution is disabled. "
                             "Enable ALLOW_EXECUTE_JS=true in .env to use it.",
                    "success": False,
                }

        # --- clamping for damage -------------------------------------------
        if action_type == "update_hp":
            kwargs["damage"], clamp_reason = _clamp_damage(kwargs["damage"])
            if clamp_reason:
                logger.info(f"Action {action_type}: {clamp_reason}")

        # --- approval gate (P2a) -------------------------------------------
        if self.approval_workflow and self.approval_workflow.is_consequential(action_type):
            # Check if any pending proposals have timed out (auto-approve)
            self.approval_workflow._process_timeouts()

            proposal = self.approval_workflow.propose(
                action_type=action_type,
                actor_id=kwargs.get("actor_id") or kwargs.get("token_id"),
                target_id=kwargs.get("target_id") or kwargs.get("target_token_id"),
                parameters=kwargs,
                description=f"Consequential action: {action_type}",
                reasoning=f"This {action_type} affects game state and requires GM approval"
            )

            # In timeout mode, if this just timed out immediately, it auto-approved
            if proposal.status == ApprovalStatus.APPROVED_AUTO:
                logger.info(f"Action auto-approved (timeout mode): {proposal.id}")
                # Fall through to execution below
            else:
                logger.info(f"Action queued for approval: {proposal.id} ({action_type})")
                return {
                    "type": action_type,
                    "queued_for_approval": True,
                    "proposal_id": proposal.id,
                    "description": proposal.description,
                    "mode": self.approval_workflow.mode,
                    "success": False,  # Not executed yet
                }

        # --- inject dependencies based on handler signature -----------------
        handler_sig = inspect.signature(handler)
        if "foundry" in handler_sig.parameters:
            kwargs["foundry"] = self.foundry
        if "app_state" in handler_sig.parameters:
            kwargs["app_state"] = self.app_state

        # --- execute --------------------------------------------------------
        try:
            result = await handler(**kwargs)

            # Guard against non-dict returns (prevents TypeError on next line).
            if not isinstance(result, dict):
                result = {"type": action_type, "raw_result": result}

            # Propagate inner failure — some executors wrap the Foundry result
            # dict under a "result" key without hoisting success/error to the top.
            # The relay signals failure either with success=False OR with a
            # truthy "error" and no success flag (e.g. move_token's
            # {"error":"Entity not found","type":"update-result"}); both must be
            # surfaced so the failure-retry path sees them instead of reporting
            # a phantom success.
            inner = result.get("result")
            inner_failed = isinstance(inner, dict) and (
                inner.get("success") is False
                or (inner.get("error") and inner.get("success") is not True)
            )
            if inner_failed:
                result["success"] = False
                result.setdefault(
                    "error",
                    inner.get("error") or inner.get("message") or "Foundry returned failure",
                )
            elif "success" not in result:
                result["success"] = True
            return result

        except Exception as e:
            logger.error(f"Action execution failed ({action_type}): {e}", exc_info=True)
            return {
                "type": action_type,
                "error": str(e),
                "success": False,
            }

    async def execute_batch(self, actions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Execute multiple actions in sequence."""
        results = []
        for action in actions:
            result = await self.execute(action)
            results.append(result)
            logger.info(f"[{result.get('type', '?')}] {result}")
        return results

    @property
    def available_actions(self) -> List[str]:
        return list(ACTION_HANDLERS.keys())
