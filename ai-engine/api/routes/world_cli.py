"""fvtt-world-cli endpoints: is the bridge up, which files the world references are missing, and one-step
pairing with the engine's own GM session. No world writes are exposed here.
"""

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from api.deps import AppState, ErrorResponse, get_app_state
from config import settings
from foundry.world_cli import WorldCLIError
from foundry.world_cli_setup import SetupError, pair_world_cli, validate_allow

logger = logging.getLogger("ai-gm")

router = APIRouter(tags=["world-cli"])

# Protocol error codes that mean "not available right now" rather than "bad request".
_UNAVAILABLE = {"DAEMON_UNAVAILABLE", "DAEMON_LOST", "NOT_CONFIGURED", "BRIDGE_NOT_READY", "TIMEOUT"}


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


class PairRequest(BaseModel):
    # Commands to allow without a GM click in sessions started with the returned seed. Defaults to
    # clearing scene placeables when World CLI writes are on. Code-running and access-changing
    # commands are refused whatever is asked.
    allow_commands: Optional[List[str]] = Field(default=None, max_length=50)


@router.post("/api/world-cli/pair")
async def world_cli_pair(req: PairRequest, state: AppState = Depends(get_app_state)):
    """Pair World CLI with the engine's own GM session and return the relay seed that keeps it paired.

    The response carries a credential, so it is for the operator who holds the admin token: put the
    returned `export` line where the relay's environment is set (start.sh) and restart the relay.
    """
    client = _client(state)
    if client is None:
        return _disabled()
    if state.foundry_client is None or not state.foundry_client.is_connected:
        return JSONResponse(status_code=503, content=ErrorResponse(
            error="Connect to a Foundry world first (a GM session is needed to pair).", code="NO_FOUNDRY").model_dump())
    allow = req.allow_commands
    if allow is None:
        allow = [f"scene.{k}.delete-many" for k in ("wall", "light", "sound")] if settings.world_cli_writes_enabled else []
    try:
        validate_allow(allow)
        return {"status": "ok", **(await pair_world_cli(client, state.foundry_client, allow))}
    except SetupError as e:
        logger.warning(f"[WorldCLI] setup failed: {e.message}" + (f" ({e.detail!r})" if e.detail is not None else ""))
        return JSONResponse(status_code=400, content=ErrorResponse(error=e.message, code="SETUP_FAILED").model_dump())
    except WorldCLIError as e:
        return _error(e)
