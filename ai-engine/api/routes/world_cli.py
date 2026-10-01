"""fvtt-world-cli endpoints (read-only): is the bridge up, and which files the world references are missing.

Writes through World CLI are not exposed here; this is the first, no-mutation phase.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from api.deps import AppState, ErrorResponse, get_app_state
from foundry.world_cli import WorldCLIError

router = APIRouter(tags=["world-cli"])

# Protocol error codes that mean "not available right now" rather than "bad request".
_UNAVAILABLE = {"DAEMON_UNAVAILABLE", "NOT_CONFIGURED", "BRIDGE_NOT_READY", "TIMEOUT"}


def _error(e: WorldCLIError) -> JSONResponse:
    status = 503 if e.code in _UNAVAILABLE else 409 if e.code in {"APPROVAL_PENDING", "COMMAND_DENIED"} else 400
    return JSONResponse(status_code=status, content=ErrorResponse(error=e.message, code=e.code, details=e.details or None).model_dump())


def _client(state: AppState):
    return state.world_cli


def _disabled() -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content=ErrorResponse(error="World CLI is not enabled (set WORLD_CLI_ENABLED=true).", code="DISABLED").model_dump(),
    )


@router.get("/api/world-cli/status")
async def world_cli_status(state: AppState = Depends(get_app_state)):
    """The daemon's ping: bridge status, and whether the daemon is reachable at all."""
    client = _client(state)
    if client is None:
        return _disabled()
    try:
        return {"status": "ok", **(await client.call("system.ping"))}
    except WorldCLIError as e:
        return _error(e)


@router.get("/api/world-cli/audit-files")
async def world_cli_audit_files(
    scope: Optional[List[str]] = Query(None), limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0),
    state: AppState = Depends(get_app_state),
):
    """Files the world's documents reference that do not exist (broken portraits, tokens, sounds, maps)."""
    client = _client(state)
    if client is None:
        return _disabled()
    params = {"limit": limit, "offset": offset}
    if scope:
        params["scope"] = scope
    try:
        return {"status": "ok", **(await client.call("world.audit-files", params))}
    except WorldCLIError as e:
        return _error(e)
