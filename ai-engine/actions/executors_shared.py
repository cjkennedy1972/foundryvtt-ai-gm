"""Helpers shared between executors.py and the split-out action modules.

Both sides need these, and putting them here rather than in executors.py is
what keeps the imports acyclic: executors.py imports the action modules to
build its dispatch table, so those modules cannot import it back.

Deliberately small. A helper belongs here only when more than one module
needs it; anything used by a single module stays with that module. Moved
verbatim — no behaviour change.
"""

class ExecutionError(Exception):
    """Raised when an action cannot be executed due to missing dependencies."""
    pass


def _require(condition: bool, message: str):
    """FAIL-FAST: Raise ExecutionError if condition is False."""
    if not condition:
        raise ExecutionError(message)


def _extract_token_id(res: dict) -> str:
    """Pull the created/moved token id from place_token's varied return shapes.

    - move/dedup path: {"moved": True, "token_id": "..."}
    - create path (canvas_create): {"data": [{"_id": "..."}], "type": "create-canvas-document-result"}
    - simple: {"id": "..."}
    """
    if not isinstance(res, dict):
        return ""
    tid = res.get("token_id") or res.get("id")
    if tid:
        return tid
    data = res.get("data")
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return data[0].get("_id") or data[0].get("id") or ""
    if isinstance(data, dict):
        return data.get("_id") or data.get("id") or ""
    return ""
