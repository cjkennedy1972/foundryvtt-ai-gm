"""Campaign lifecycle endpoints: scan, build, deploy, start, restart, teardown,
asset regeneration, enrichment, analyze/optimize, plus session end.

Moved verbatim from main.py (Phase 1 of the modular architecture split);
request/response models live here too — they are used by no other module.
"""

import json
import logging
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from api.deps import (
    ApiError,
    AppState,
    ErrorResponse,
    broadcast_state_update,
    get_app_state,
    require_foundry,
)
from campaign.vault import CampaignStore
from config import settings
from state.models import GameMode
from utils.tasks import spawn

logger = logging.getLogger("ai-gm")

router = APIRouter(tags=["campaign"])


async def _select_campaign_world(campaign_name: str, state: AppState) -> str | None:
    """Open the Foundry world linked to the campaign before operating on it.

    The association is saved in the campaign registry after its first successful
    launch. An unlinked legacy campaign may use the local ``FOUNDRY_WORLD``
    bootstrap setting, but no repository default chooses a world for the user.
    """
    from campaign.obsidian_sync import get_campaign_world

    if not state.relay_manager or not state.foundry_client:
        return "Foundry session manager is unavailable"

    # Starting a campaign owns the Foundry connection lifecycle. The dashboard
    # may have started the relay already for manual pairing; otherwise start it
    # now, but do not start Chrome until a linked world is selected below.
    if settings.relay_managed and not state.relay_manager.status().get("running"):
        try:
            await state.relay_manager.start()
        except Exception as exc:
            return f"Could not start the relay: {exc}"

    linked = get_campaign_world(campaign_name) or {}
    world_name = linked.get("world_name") or settings.foundry_world
    world_id = linked.get("world_id") or ""
    if not world_name:
        # New campaigns use a manually created and paired Foundry world. If it
        # is already connected to the relay, adopt and link it on first start;
        # otherwise give an actionable setup error without launching Chrome.
        if not await state.foundry_client.connect(max_retries=3):
            return (
                f"Campaign '{campaign_name}' is not linked to a Foundry world. "
                "Create the world in Foundry, enable and pair the relay module, "
                "then start this campaign again."
            )
        try:
            result = await state.foundry_client.execute_js(
                "return {title: game.world?.title ?? '', id: game.world?.id ?? ''};"
            )
            current = result.get("result") or {}
            world_name = current.get("title") or ""
            world_id = current.get("id") or ""
        except Exception:
            world_name = ""
            world_id = ""
        if not world_name and not world_id:
            return "The paired Foundry client did not report an active world."
        from campaign.obsidian_sync import link_world_to_campaign
        link_world_to_campaign(campaign_name, world_name, world_id)
        logger.info("Linked manually paired world '%s' to campaign '%s'", world_name, campaign_name)
        return None

    # Do not disrupt a healthy session already connected to this campaign's world.
    if state.foundry_client.is_connected:
        try:
            result = await state.foundry_client.execute_js(
                "return {title: game.world?.title ?? '', id: game.world?.id ?? ''};"
            )
            current = result.get("result") or {}
            if current.get("title") == world_name or (world_id and current.get("id") == world_id):
                return None
        except Exception:
            logger.info("Could not inspect current Foundry world; selecting campaign world")

    # A headless browser can host only one world. Restarting it is intentional
    # when the selected campaign points to another world.
    client_id = await state.relay_manager.restart_headless_session(world_name=world_name)
    if not client_id:
        return f"Could not launch Foundry world '{world_name}' for campaign '{campaign_name}'"
    settings.relay_headless_client_id = client_id
    await state.foundry_client.disconnect()
    if not await state.foundry_client.connect(max_retries=3):
        return f"Foundry world '{world_name}' launched but the AI-GM could not connect to it"
    return None


class CampaignCreate(BaseModel):
    name: str
    vault_files: List[str] = Field(default_factory=list)
    description: str = ""


@router.post("/api/campaign/load", response_model=dict)
async def load_campaign(campaign: CampaignCreate, state: AppState = Depends(get_app_state)):

    """Load or create a new campaign with its own vault subfolder."""
    if not state.campaign_loader:
        return {
            "status": "error",
            "error": "Campaign loader not initialized",
            "name": campaign.name,
            "folder": "",
            "loaded_files": [],
        }

    result = await state.campaign_loader.load_custom_campaign(
        campaign.name, campaign.vault_files
    )
    return {
        "status": "ok",
        "name": campaign.name,
        "folder": result.get("folder", ""),
        "loaded_files": result.get("linked_files", []),
    }


class CampaignScanRequest(BaseModel):
    """Request body for scanning a FoundryVTT world."""
    world_name: Optional[str] = None


class CampaignScanResponse(BaseModel):
    status: str
    scan_id: str
    world: Dict[str, Any] = Field(default_factory=dict)
    scenes: List[Dict[str, Any]] = Field(default_factory=list)
    actors: List[Dict[str, Any]] = Field(default_factory=list)
    items: List[Dict[str, Any]] = Field(default_factory=list)
    journal: List[Dict[str, Any]] = Field(default_factory=list)
    quests: List[Dict[str, Any]] = Field(default_factory=list)
    modules: List[Dict[str, Any]] = Field(default_factory=list)
    capabilities: Dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None


class CampaignBuildRequest(BaseModel):
    """Request body for building a new campaign."""
    name: str
    description: str = ""
    theme: str = ""
    seed_ideas: str = ""
    scale: str = ""
    level_range: str = "1-5"
    vault_files: List[str] = Field(default_factory=list)
    create_world: bool = False
    foundry_world_name: Optional[str] = None
    foundry_system_id: str = "dnd5e"
    generate_prologue: bool = True


class CampaignExtendRequest(BaseModel):
    """Request body for extending an existing campaign with a new arc."""
    campaign_name: str
    current_level: int = 1


class CampaignTeardownRequest(BaseModel):
    """Request body for removing campaign content from FoundryVTT."""
    campaign_name: str


class CampaignTeardownResponse(BaseModel):
    status: str
    campaign_name: str
    deleted: Dict[str, Any] = Field(default_factory=dict)
    errors: List[str] = Field(default_factory=list)


class CampaignExtendResponse(BaseModel):
    status: str
    campaign_name: str
    arc_number: int = 0
    arc_title: str = ""
    steps_completed: List[Dict[str, Any]] = Field(default_factory=list)
    arc_data: Optional[Dict[str, Any]] = None
    assets: Dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None


class CampaignImportRequest(BaseModel):
    """Request body for importing a published campaign folder."""
    source_path: str
    campaign_name: str
    create_world: bool = True
    foundry_world_name: Optional[str] = None
    foundry_system_id: str = "dnd5e"
    level_range: str = "1-5"
    journal_pack: Optional[str] = None


class CampaignBuildResponse(BaseModel):
    status: str
    campaign_id: str
    campaign_name: str
    steps_completed: List[Dict[str, Any]] = Field(default_factory=list)
    scan_data: Optional[Dict[str, Any]] = None
    generated_data: Optional[Dict[str, Any]] = None
    maps_generated: Dict[str, Any] = Field(default_factory=dict)
    progress: int = 0
    total_steps: int = 0
    error: Optional[str] = None
    ready_to_start: bool = False
    import_summary: Optional[Dict[str, Any]] = None


async def _attach_or_create_world(
    state: AppState,
    *,
    campaign_name: str,
    create_world: bool,
    foundry_world_name: Optional[str] = None,
    foundry_system_id: str = "dnd5e",
    description: str = "",
) -> tuple[Optional[str], Optional[Dict[str, str]], Optional[CampaignBuildResponse]]:
    """Resolve a Foundry world for campaign build/import.

    When *create_world* is True, clones the template world, starts a relay
    headless session for it, and connects the Foundry client.
    When False, attaches to the currently paired live world (or launches a
    named offline world explicitly).

    Returns:
        (created_world_name, paired_world, error_response)
        *error_response* is non-None when the world could not be resolved.
    """
    created_world_name: Optional[str] = None
    paired_world: Optional[Dict[str, str]] = None

    if create_world:
        if not state.relay_manager:
            return None, None, CampaignBuildResponse(
                status="error",
                campaign_id=f"campaign-{uuid.uuid4().hex[:8]}",
                campaign_name=campaign_name,
                error="Relay manager is unavailable for automatic world creation",
            )
        if settings.relay_managed and not state.relay_manager.status().get("running"):
            try:
                await state.relay_manager.start()
            except Exception as exc:
                return None, None, CampaignBuildResponse(
                    status="error",
                    campaign_id=f"campaign-{uuid.uuid4().hex[:8]}",
                    campaign_name=campaign_name,
                    error=f"Could not start the relay: {exc}",
                )
        world_name = foundry_world_name or campaign_name
        from foundry.world_template import clone_world
        try:
            clone = clone_world(
                world_name,
                description=description,
                expected_system=foundry_system_id.strip(),
            )
        except ValueError as exc:
            return None, None, CampaignBuildResponse(
                status="error",
                campaign_id=f"campaign-{uuid.uuid4().hex[:8]}",
                campaign_name=campaign_name,
                error=str(exc),
            )
        client_id = await state.relay_manager.ensure_headless_session(
            world_name=clone.world_name,
        )
        if not client_id:
            return None, None, CampaignBuildResponse(
                status="error",
                campaign_id=f"campaign-{uuid.uuid4().hex[:8]}",
                campaign_name=campaign_name,
                error="Cloned the Foundry world but the relay could not launch and connect it",
            )
        settings.relay_headless_client_id = client_id
        if state.foundry_client:
            await state.foundry_client.disconnect()
            if not await state.foundry_client.connect(max_retries=3):
                return None, None, CampaignBuildResponse(
                    status="error",
                    campaign_id=f"campaign-{uuid.uuid4().hex[:8]}",
                    campaign_name=campaign_name,
                    error="Cloned the Foundry world but the AI-GM could not connect to it",
                )
        created_world_name = clone.world_name
        paired_world = {"title": clone.world_name, "id": clone.world_id}
        logger.info(
            "Cloned and connected Foundry world '%s' (id=%s, client=%s)",
            clone.world_name, clone.world_id, client_id,
        )
    else:
        if not state.relay_manager or not state.foundry_client:
            return None, None, CampaignBuildResponse(
                status="error",
                campaign_id=f"campaign-{uuid.uuid4().hex[:8]}",
                campaign_name=campaign_name,
                error="Start the relay and pair a Foundry world before building this campaign",
            )
        if settings.relay_managed and not state.relay_manager.status().get("running"):
            try:
                await state.relay_manager.start(start_foundry=False)
            except Exception as exc:
                return None, None, CampaignBuildResponse(
                    status="error",
                    campaign_id=f"campaign-{uuid.uuid4().hex[:8]}",
                    campaign_name=campaign_name,
                    error=f"Could not start the relay: {exc}",
                )
        if not state.foundry_client.is_connected:
            target_world = foundry_world_name or settings.foundry_world
            if not target_world:
                return None, None, CampaignBuildResponse(
                    status="error",
                    campaign_id=f"campaign-{uuid.uuid4().hex[:8]}",
                    campaign_name=campaign_name,
                    error=(
                        "No paired Foundry world is connected and this build does not "
                        "name one. If this is a new campaign, set 'create_world' to have "
                        "the AI-GM create the world, or set 'foundry_world_name' to an "
                        "existing world; otherwise open the world in Foundry, pair the "
                        "relay module, and build again."
                    ),
                )
            client_id = await state.relay_manager.ensure_headless_session(world_name=target_world)
            if client_id:
                settings.relay_headless_client_id = client_id
            if not await state.foundry_client.connect(max_retries=3):
                return None, None, CampaignBuildResponse(
                    status="error",
                    campaign_id=f"campaign-{uuid.uuid4().hex[:8]}",
                    campaign_name=campaign_name,
                    error=f"The relay could not connect to Foundry world '{target_world}'.",
                )
        world_result = await state.foundry_client.execute_js(
            "return {title: game.world?.title ?? '', id: game.world?.id ?? ''};"
        )
        world = world_result.get("result") or {}
        if not world.get("title") and not world.get("id"):
            return None, None, CampaignBuildResponse(
                status="error",
                campaign_id=f"campaign-{uuid.uuid4().hex[:8]}",
                campaign_name=campaign_name,
                error="The paired Foundry client did not report an active world.",
            )
        paired_world = {"title": world.get("title") or "", "id": world.get("id") or ""}
        logger.info("Building campaign '%s' in manually paired world '%s'", campaign_name, paired_world["title"])

    return created_world_name, paired_world, None


class CampaignStartRequest(BaseModel):
    """Request body for starting/continuing a campaign session."""
    campaign_name: str
    continue_from_last: bool = False


class CampaignStartResponse(BaseModel):
    status: str
    session_id: str
    campaign_name: str
    current_scene: str = ""
    active_actors: int = 0
    message: str = ""
    error: Optional[str] = None


class SessionEndRequest(BaseModel):
    """Request body for ending a session prematurely."""
    reason: str = "GM ended session"


class SessionEndResponse(BaseModel):
    status: str
    session_id: str
    campaign_name: str
    summary: str = ""
    error: Optional[str] = None


class CampaignListResponse(BaseModel):
    campaigns: List[Dict[str, Any]] = Field(default_factory=list)
    error: Optional[str] = None


class CampaignDeployRequest(BaseModel):
    """Request to deploy an existing campaign to FoundryVTT."""
    campaign_name: str


class CampaignDeployResponse(BaseModel):
    """Response from campaign deployment."""
    status: str
    campaign_name: str
    scenes_deployed: int = 0
    npcs_deployed: int = 0
    journal_entries_deployed: int = 0
    quest_logs_deployed: int = 0
    loot_tables_deployed: int = 0
    error: Optional[str] = None


class CampaignRegenerateAssetsRequest(BaseModel):
    """Request to regenerate maps/portraits for an existing campaign."""
    campaign_name: str
    attach_to_foundry: bool = True


class CampaignRegenerateAssetsResponse(BaseModel):
    """Response from asset regeneration."""
    status: str
    campaign_name: str
    maps_generated: int = 0
    portraits_generated: int = 0
    scenes_attached: int = 0
    portraits_attached: int = 0
    errors: List[str] = Field(default_factory=list)
    error: Optional[str] = None


# --- Campaign Wizard Endpoints ---

@router.post("/api/campaign/scan", response_model=CampaignScanResponse)
async def scan_world_endpoint(request: CampaignScanRequest, state: AppState = Depends(get_app_state)):

    """Scan the connected FoundryVTT world and catalog all resources.

    This endpoint performs a comprehensive scan of:
    - World structure and metadata
    - All scenes (maps) with token counts and lighting
    - All actors (NPCs, monsters, PCs)
    - All items/equipment
    - Journal entries
    - Active quests/encounters
    - Available modules/add-ons and their capabilities
    """
    if not state.foundry_client or not state.foundry_client.is_connected:
        return CampaignScanResponse(
            status="error",
            scan_id=f"scan-{uuid.uuid4().hex[:8]}",
            error="Not connected to FoundryVTT",
        )

    try:
        logger.info(f"Scanning FoundryVTT world: {request.world_name or 'unknown'}")

        # Step 1: Run full world scan
        scan_data = await state.foundry_client.scan_world()

        # Step 2: Analyze capabilities from scan
        capabilities = await state.foundry_client.discover_addon_capabilities(scan_data)

        response = CampaignScanResponse(
            status="ok",
            scan_id=f"scan-{uuid.uuid4().hex[:8]}",
            world=scan_data.get("world", {}),
            scenes=scan_data.get("scenes", []),
            actors=scan_data.get("actors", []),
            items=scan_data.get("items", []),
            journal=scan_data.get("journal", []),
            quests=scan_data.get("quests", []),
            modules=scan_data.get("modules", []),
            capabilities=capabilities,
        )

        logger.info(
            f"World scan complete: {len(response.scenes)} scenes, "
            f"{len(response.actors)} actors, {len(response.items)} items, "
            f"{len(response.modules)} modules"
        )
        return response

    except Exception as e:
        logger.exception("World scan failed")
        return CampaignScanResponse(
            status="error",
            scan_id=f"scan-{uuid.uuid4().hex[:8]}",
            error=str(e),
        )


@router.post("/api/campaign/build", response_model=CampaignBuildResponse)
async def build_campaign_endpoint(request: CampaignBuildRequest, state: AppState = Depends(get_app_state)):

    """Generate a new campaign from structured campaign info.

    Pipeline:
    1. Construct prompt from name, description, theme, seed_ideas, scale
    2. LLM generates structured campaign data (NPCs, locations, quests, arcs)
    3. Campaign saved to Obsidian vault
    4. ComfyUI generates map images for locations
    5. Returns full campaign structure and manifest
    6. Scan FoundryVTT world for existing resources
    7. Generate maps via oMLX Z-Image-Turbo (fallback ComfyUI)
    """
    from campaign.orchestrator import CampaignOrchestrator
    import httpx

    llm_client = httpx.AsyncClient(timeout=300)
    try:
        created_world_name, paired_world, err = await _attach_or_create_world(
            state,
            campaign_name=request.name,
            create_world=request.create_world,
            foundry_world_name=request.foundry_world_name,
            foundry_system_id=request.foundry_system_id,
            description=request.description or "",
        )
        if err is not None:
            return err

        # Resolve paths
        vault_path = settings.campaign_vault_path

        # Build the full prompt from all user inputs
        full_prompt = f"Create a D&D 5e campaign named '{request.name}'."
        if request.description:
            full_prompt += f"\n\nTheme: {request.description}"
        if request.theme:
            full_prompt += f"\n\nTheme setting: {request.theme}"
        if request.seed_ideas:
            full_prompt += f"\n\nSeed ideas from user: {request.seed_ideas}"
        if request.scale:
            full_prompt += f"\n\nCampaign scale: {request.scale}"
        if request.level_range and request.level_range != "1-5":
            full_prompt += f"\n\nLevel range: {request.level_range}"

        orch = CampaignOrchestrator()

        result = await orch.build_campaign(
            prompt=full_prompt,
            campaign_name=request.name,
            llm_client=llm_client,
            foundry_client=state.foundry_client if state.foundry_client and state.foundry_client.is_connected else None,
            vault_path=settings.campaign_vault_path,
            comfyui_url=settings.comfyui_url,
            omlx_url=getattr(settings, "omlx_base_url", None) or getattr(settings, "omlx_url", None),
            omlx_model=getattr(settings, "omlx_model", "Z-Image-Turbo"),
            omlx_api_key=getattr(settings, "omlx_api_key", None),
            on_progress=None,
            level_range=request.level_range or "1-5",
        )

        if (created_world_name or paired_world) and result.get("status") in {"ok", "success", "complete"}:
            from campaign.obsidian_sync import link_world_to_campaign

            link_name = paired_world["title"] if paired_world else created_world_name
            link_id = paired_world["id"] if paired_world else ""
            if not link_world_to_campaign(request.name, link_name, link_id):
                return CampaignBuildResponse(
                    status="error",
                    campaign_id=result.get("campaign_id", f"campaign-{uuid.uuid4().hex[:8]}"),
                    campaign_name=request.name,
                    error="Campaign was built, but its Foundry world link could not be saved",
                    ready_to_start=False,
                )

        # Map orchestrator result to our response model
        assets = result.get("assets") or {}
        return CampaignBuildResponse(
            status=result.get("status", "error"),
            campaign_id=result.get("campaign_id", f"campaign-{uuid.uuid4().hex[:8]}"),
            campaign_name=request.name,
            steps_completed=result.get("steps_completed", []),
            scan_data=result.get("scan_data"),
            generated_data=result.get("generated_data"),
            maps_generated=assets,
            progress=result.get("progress", 0),
            total_steps=result.get("total_steps", 5),
            error=result.get("error"),
            ready_to_start=result.get("ready_to_start", result.get("status") in ("success", "complete")),
        )
    except Exception as e:
        logger.exception("Campaign build failed")
        return CampaignBuildResponse(
            status="error",
            campaign_id=f"campaign-{uuid.uuid4().hex[:8]}",
            campaign_name=request.name,
            error=str(e),
            ready_to_start=False,
        )
    finally:
        await llm_client.aclose()


@router.post("/api/campaign/import", response_model=CampaignBuildResponse)
async def import_campaign_endpoint(request: CampaignImportRequest, state: AppState = Depends(get_app_state)):
    """Import a published campaign folder and deploy it through the build pipeline.

    Validates the source path, delegates to CampaignOrchestrator.import_campaign,
    and links the resulting campaign to a Foundry world (same pattern as build).
    """
    from campaign.orchestrator import CampaignOrchestrator
    import httpx
    from pathlib import Path as _Path

    # Validate source path exists
    src = _Path(request.source_path).expanduser().resolve()
    if not src.is_dir():
        return CampaignBuildResponse(
            status="error",
            campaign_id=f"campaign-{uuid.uuid4().hex[:8]}",
            campaign_name=request.campaign_name,
            error=f"Source folder not found: {src}",
        )

    llm_client = httpx.AsyncClient(timeout=300)
    try:
        created_world_name, paired_world, err = await _attach_or_create_world(
            state,
            campaign_name=request.campaign_name,
            create_world=request.create_world,
            foundry_world_name=request.foundry_world_name,
            foundry_system_id=request.foundry_system_id,
            description=f"Imported: {request.campaign_name}",
        )
        if err is not None:
            return err

        # Run the import
        orch = CampaignOrchestrator()
        result = await orch.import_campaign(
            source_path=str(src),
            campaign_name=request.campaign_name,
            llm_client=llm_client,
            foundry_client=state.foundry_client if state.foundry_client and state.foundry_client.is_connected else None,
            vault_path=settings.campaign_vault_path,
            comfyui_url=settings.comfyui_url,
            omlx_url=getattr(settings, "omlx_base_url", None) or getattr(settings, "omlx_url", None),
            omlx_api_key=getattr(settings, "omlx_api_key", None),
            on_progress=None,
            level_range=request.level_range or "1-5",
            journal_pack=request.journal_pack,
        )

        # Link world to campaign on success
        if (created_world_name or paired_world) and result.get("status") in {"ok", "success", "complete"}:
            from campaign.obsidian_sync import link_world_to_campaign
            link_name = paired_world["title"] if paired_world else created_world_name
            link_id = paired_world["id"] if paired_world else ""
            if not link_world_to_campaign(request.campaign_name, link_name, link_id):
                return CampaignBuildResponse(
                    status="error",
                    campaign_id=result.get("campaign_id", f"campaign-{uuid.uuid4().hex[:8]}"),
                    campaign_name=request.campaign_name,
                    error="Campaign was imported, but its Foundry world link could not be saved",
                    ready_to_start=False,
                )

        assets = result.get("assets") or {}
        return CampaignBuildResponse(
            status=result.get("status", "error"),
            campaign_id=result.get("campaign_id", f"campaign-{uuid.uuid4().hex[:8]}"),
            campaign_name=request.campaign_name,
            steps_completed=result.get("steps_completed", result.get("steps", [])),
            scan_data=result.get("scan_data"),
            generated_data=result.get("generated_data"),
            maps_generated=assets,
            progress=result.get("progress", 0),
            total_steps=result.get("total_steps", 5),
            error=result.get("error"),
            ready_to_start=result.get("ready_to_start", result.get("status") in ("success", "complete")),
            import_summary=result.get("import_summary"),
        )
    except Exception as e:
        logger.exception("Campaign import failed")
        return CampaignBuildResponse(
            status="error",
            campaign_id=f"campaign-{uuid.uuid4().hex[:8]}",
            campaign_name=request.campaign_name,
            error=str(e),
            ready_to_start=False,
        )
    finally:
        await llm_client.aclose()


@router.post("/api/campaign/extend", response_model=CampaignExtendResponse)
async def extend_campaign_endpoint(request: CampaignExtendRequest, state: AppState = Depends(get_app_state)):
    """Extend an existing campaign with a new story arc.

    Loads the existing campaign, generates the next arc's scenes/NPCs/encounters
    via LLM (scaled to the party's current level), and deploys the new content
    into FoundryVTT alongside what already exists.
    """
    from campaign.orchestrator import CampaignOrchestrator
    import httpx

    llm_client = httpx.AsyncClient(timeout=300)
    try:
        world_error = await _select_campaign_world(request.campaign_name, state)
        if world_error:
            return CampaignExtendResponse(
                status="error", campaign_name=request.campaign_name, error=world_error,
            )
        orch = CampaignOrchestrator()
        result = await orch.extend_campaign_arc(
            campaign_name=request.campaign_name,
            current_level=request.current_level,
            llm_client=llm_client,
            foundry_client=state.foundry_client if state.foundry_client and state.foundry_client.is_connected else None,
            vault_path=settings.campaign_vault_path,
            comfyui_url=settings.comfyui_url,
            omlx_url=getattr(settings, "omlx_base_url", None) or getattr(settings, "omlx_url", None),
            omlx_api_key=getattr(settings, "omlx_api_key", None),
            on_progress=None,
        )
        return CampaignExtendResponse(
            status=result.get("status", "error"),
            campaign_name=request.campaign_name,
            arc_number=result.get("arc_number", 0),
            arc_title=result.get("arc_title", ""),
            steps_completed=result.get("steps", []),
            arc_data=result.get("arc_data"),
            assets=result.get("assets", {}),
            error=result.get("error"),
        )
    except Exception as e:
        logger.exception("Campaign arc extension failed")
        return CampaignExtendResponse(
            status="error",
            campaign_name=request.campaign_name,
            error=str(e),
        )
    finally:
        await llm_client.aclose()


@router.post("/api/campaign/teardown", response_model=CampaignTeardownResponse)
async def teardown_campaign_endpoint(request: CampaignTeardownRequest, state: AppState = Depends(get_app_state)):
    """Remove all AI-GM-created content for a campaign from the connected FoundryVTT world.

    Deletes every Scene, Actor, JournalEntry, RollTable, and Playlist that
    has a flags["ai-gm"] marker (set by the deployment pipeline), plus a
    UUID-based fallback pass using the stored deployment state.

    The Obsidian vault and local campaign_assets files are NOT touched.
    """
    from campaign.orchestrator import CampaignOrchestrator

    world_error = await _select_campaign_world(request.campaign_name, state)
    if world_error:
        return CampaignTeardownResponse(
            status="error", campaign_name=request.campaign_name, errors=[world_error],
        )
    if not state.foundry_client or not state.foundry_client.is_connected:
        return CampaignTeardownResponse(
            status="error",
            campaign_name=request.campaign_name,
            errors=["Not connected to FoundryVTT — open the world in Foundry first"],
        )

    try:
        orch = CampaignOrchestrator()
        result = await orch.teardown_campaign(
            campaign_name=request.campaign_name,
            foundry_client=state.foundry_client,
        )
        return CampaignTeardownResponse(
            status=result.get("status", "ok"),
            campaign_name=request.campaign_name,
            deleted=result.get("deleted", {}),
            errors=result.get("errors", []),
        )
    except Exception as e:
        logger.exception("Campaign teardown failed")
        return CampaignTeardownResponse(
            status="error",
            campaign_name=request.campaign_name,
            errors=[str(e)],
        )


async def _deploy_campaign_to_world(campaign_name: str, state: AppState) -> Dict[str, Any]:
    """Deploy a vault campaign to the connected world with assets and walls restored.

    Full redeploy pipeline shared by the deploy and restart endpoints:
    1. Re-upload local maps/portraits so background_src/portrait_src resolve
       (idempotent: same Foundry path + filename each time).
    2. Scan the world so module-specific flags (Item Piles, Midi QOL, patrol,
       soundscapes, …) are applied — previously skipped on redeploy.
    3. Deploy all entities.
    4. Enrich scenes: walls, doors, lights, sounds, fog/vision config.
    5. Persist deployment state and updated asset references.

    Raises FileNotFoundError if the campaign isn't in the vault.
    """
    from campaign.orchestrator import CampaignOrchestrator

    store = CampaignStore(campaign_name)
    campaign_data = await store.load()

    orch = CampaignOrchestrator()
    safe_name = store.safe_name
    asset_output_dir = store.maps_dir

    # ── 1. Restore assets ──
    if asset_output_dir.exists():
        map_upload = await orch.upload_maps_to_foundry(
            campaign_data, state.foundry_client, asset_output_dir, safe_name
        )
        portrait_upload = await orch.upload_portraits_to_foundry(
            campaign_data, state.foundry_client, asset_output_dir, safe_name
        )
        logger.info(
            f"[Deploy] Asset restore: {map_upload.get('uploaded', 0)} maps, "
            f"{portrait_upload.get('uploaded', 0)} portraits re-uploaded"
        )

    # ── 2. Scan for active modules so addon flags apply on redeploy too ──
    scan_result = None
    try:
        scan_result = await orch.scan_foundry_world(state.foundry_client)
    except Exception as e:
        logger.warning(f"[Deploy] World scan failed (module flags skipped): {e}")

    # ── 3. Deploy entities ──
    deployment_result = await orch.deploy_to_foundry(
        campaign_data,
        state.foundry_client,
        {"maps": [], "portraits": []},
        scan_result=scan_result,
    )

    # ── 4. Walls, lights, sounds, scene config ──
    try:
        enrich_summary = await orch.enrich_scenes(
            campaign_data, state.foundry_client, deployment_result
        )
        deployment_result["scene_enrichment"] = enrich_summary
        logger.info(f"[Deploy] Enrichment: {enrich_summary}")
    except Exception as e:
        logger.warning(f"[Deploy] Scene enrichment failed: {e}")

    # ── 5. Persist deployment state + asset references ──
    try:
        await store.save_deployment(deployment_result)
        # background_src/portrait_src may have been (re)computed — keep them.
        await store.save(campaign_data)
    except Exception as e:
        logger.warning(f"[Deploy] Failed to persist deployment state: {e}")

    return deployment_result


@router.post("/api/campaign/deploy", response_model=CampaignDeployResponse)
async def deploy_campaign_endpoint(request: CampaignDeployRequest, state: AppState = Depends(get_app_state)):

    """Deploy an existing campaign from the vault to FoundryVTT.

    Loads the campaign JSON from the Obsidian vault, restores maps and
    portraits, deploys all entities, and places walls/lights/sounds.
    """
    try:
        logger.info(f"Deploying campaign: {request.campaign_name}")

        world_error = await _select_campaign_world(request.campaign_name, state)
        if world_error:
            return CampaignDeployResponse(
                status="error", campaign_name=request.campaign_name, error=world_error,
            )

        if not state.foundry_client or not state.foundry_client.is_connected:
            return CampaignDeployResponse(
                status="error",
                campaign_name=request.campaign_name,
                error="Not connected to FoundryVTT",
            )

        # WARNING: Clicking "Start" multiple times will create duplicates.
        # Use "Continue" after the first deployment to avoid re-deploying.
        # To start fresh, use /api/campaign/restart.
        deployment_result = await _deploy_campaign_to_world(request.campaign_name, state)

        return CampaignDeployResponse(
            status=deployment_result.get("status", "error"),
            campaign_name=request.campaign_name,
            scenes_deployed=len(deployment_result.get("scenes", [])),
            npcs_deployed=len(deployment_result.get("npcs", [])),
            journal_entries_deployed=len(deployment_result.get("journal_entries", [])),
            quest_logs_deployed=len(deployment_result.get("quest_logs", [])),
            loot_tables_deployed=len(deployment_result.get("loot_tables", [])),
        )

    except FileNotFoundError as e:
        return CampaignDeployResponse(
            status="error",
            campaign_name=request.campaign_name,
            error=str(e),
        )
    except Exception as e:
        logger.exception(f"Campaign deployment failed: {request.campaign_name}")
        return CampaignDeployResponse(
            status="error",
            campaign_name=request.campaign_name,
            error=str(e),
        )


@router.post("/api/campaign/regenerate-assets", response_model=CampaignRegenerateAssetsResponse)
async def regenerate_assets_endpoint(
    request: CampaignRegenerateAssetsRequest, state: AppState = Depends(get_app_state)
):
    """Regenerate maps/portraits for an existing campaign without re-running the LLM.

    Generates fresh images with the current (improved) SDXL workflow, persists them
    to the vault, and — when Foundry is connected — uploads each map and attaches it
    as the background of the matching scene (updating existing scenes by name).
    """
    from campaign.orchestrator import CampaignOrchestrator

    try:
        logger.info(f"Regenerating assets for campaign: {request.campaign_name}")
        if request.attach_to_foundry:
            world_error = await _select_campaign_world(request.campaign_name, state)
            if world_error:
                return CampaignRegenerateAssetsResponse(
                    status="error", campaign_name=request.campaign_name, error=world_error,
                )
        orch = CampaignOrchestrator()
        result = await orch.regenerate_assets_for_campaign(
            campaign_name=request.campaign_name,
            foundry_client=state.foundry_client,
            attach_to_foundry=request.attach_to_foundry,
        )
        return CampaignRegenerateAssetsResponse(
            status=result.get("status", "error"),
            campaign_name=request.campaign_name,
            maps_generated=result.get("maps_generated", 0),
            portraits_generated=result.get("portraits_generated", 0),
            scenes_attached=result.get("scenes_attached", 0),
            portraits_attached=result.get("portraits_attached", 0),
            errors=result.get("errors", []),
        )
    except Exception as e:
        logger.exception(f"Asset regeneration failed: {request.campaign_name}")
        return CampaignRegenerateAssetsResponse(
            status="error",
            campaign_name=request.campaign_name,
            error=str(e),
        )


class CampaignRestartRequest(BaseModel):
    """Request to fully restart a campaign from the beginning."""
    campaign_name: str


@router.post("/api/campaign/restart", response_model=dict)
async def restart_campaign_endpoint(request: CampaignRestartRequest, state: AppState = Depends(get_app_state)):
    """Completely restart a campaign from the beginning.

    Wipes all session history (conversations, events, sessions) for the
    campaign, removes its content from the FoundryVTT world, resets the game
    state tracker, and redeploys everything fresh from the vault. Use
    /api/campaign/start afterwards to begin the new first session.
    """
    from campaign.orchestrator import CampaignOrchestrator

    try:
        world_error = await _select_campaign_world(request.campaign_name, state)
        if world_error:
            return JSONResponse(
                status_code=503,
                content=ErrorResponse(status="error", error=world_error, code="FOUNDRY_UNAVAILABLE").model_dump(),
            )
        require_foundry(state)
        logger.info(f"Restarting campaign from scratch: {request.campaign_name}")

        # 1. Stop the AI listener and close any active session
        if state.chat_listener and state.chat_listener._running:
            state.chat_listener._running = False
        active = await state.db.get_active_session()
        if active:
            await state.db.close_session(active)

        # 2. Wipe all session history for this campaign
        sessions_deleted = await state.db.delete_campaign_history(request.campaign_name)

        # 3. Reset persisted game state (scene, combat, contexts)
        if state.state_tracker:
            await state.state_tracker.reset(campaign=request.campaign_name)

        # 4. Remove existing world content
        orch = CampaignOrchestrator()
        teardown = await orch.teardown_campaign(request.campaign_name, state.foundry_client)

        # 5. Redeploy fresh (assets restored, walls placed)
        deployment = await _deploy_campaign_to_world(request.campaign_name, state)

        # Reset the prologue replay flag on the freshly deployed journal so a
        # restarted campaign can replay its introduction from the beginning.
        if state.foundry_client and state.foundry_client.is_connected:
            try:
                from campaign.prologue import reset_prologue_shown
                await reset_prologue_shown(state.foundry_client)
            except Exception as _prologue_reset_error:
                logger.debug(f"[Restart] Could not reset prologue replay flag: {_prologue_reset_error}")

        await broadcast_state_update({
            "type": "campaign_restarted",
            "campaign_name": request.campaign_name,
        })

        return {
            "status": "restarted",
            "campaign_name": request.campaign_name,
            "sessions_deleted": sessions_deleted,
            "teardown": teardown.get("deleted", {}),
            "scenes_deployed": len(deployment.get("scenes", [])),
            "npcs_deployed": len(deployment.get("npcs", [])),
            "enrichment": deployment.get("scene_enrichment", {}),
            "message": "Campaign restarted — use Start Session to begin from the opening scene.",
        }
    except FileNotFoundError as e:
        return JSONResponse(
            status_code=404,
            content=ErrorResponse(status="error", error=str(e), code="CAMPAIGN_NOT_FOUND").model_dump(),
        )
    except Exception as e:
        logger.exception(f"Campaign restart failed: {request.campaign_name}")
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(status="error", error=str(e), code="RESTART_FAILED").model_dump(),
        )


@router.post("/api/campaign/start", response_model=CampaignStartResponse)
async def start_campaign_endpoint(request: CampaignStartRequest, state: AppState = Depends(get_app_state)):

    """Start (or continue) a campaign session.

    If continue_from_last is True, loads the previous session's state.
    Otherwise, creates a fresh session and loads the campaign context.
    """
    try:
        world_error = await _select_campaign_world(request.campaign_name, state)
        if world_error:
            return CampaignStartResponse(
                status="error", session_id="", campaign_name=request.campaign_name, error=world_error,
            )
        require_foundry(state)
        # Get active session
        active_session = await state.db.get_active_session()

        if request.continue_from_last and active_session:
            # Continue from last session
            logger.info(f"Continuing session: {active_session}")
            session_id = active_session
        else:
            # Create new session
            session_id = str(uuid.uuid4())[:8]
            await state.db.create_session(session_id, request.campaign_name)
            logger.info(f"Created new session: {session_id}")

        # Update state tracker
        await state.state_tracker.set_campaign(request.campaign_name)
        await state.state_tracker.set_mode(GameMode.EXPLORATION)
        await state.state_tracker.save()

        # Load campaign vault files into the AI context
        if state.campaign_loader:
            await state.campaign_loader.load(request.campaign_name)
            logger.info(f"Loaded campaign context for '{request.campaign_name}'")
            if state.npc_registry:
                state.campaign_loader.register_vault_npcs(state.npc_registry)

        # Persist the world↔campaign association only on first association. Once
        # a campaign has a world, starting it in another world is an error: an
        # extension must never silently move a campaign to a different world.
        if request.campaign_name and state.foundry_client:
            try:
                from campaign.obsidian_sync import get_campaign_world, link_world_to_campaign
                _wjs = "return {title: game.world?.title ?? '', id: game.world?.id ?? ''};"
                _wres = await state.foundry_client.execute_js(_wjs)
                _world = (_wres.get("result") or {}) if isinstance(_wres, dict) else {}
                _wtitle, _wid = _world.get("title", ""), _world.get("id", "")
                _linked = get_campaign_world(request.campaign_name)
                if _linked and (_linked.get("world_id") or _linked.get("world_name")):
                    if ((_linked.get("world_id") and _wid != _linked["world_id"]) or
                            (not _linked.get("world_id") and _linked.get("world_name") != _wtitle)):
                        raise ApiError(
                            f"Campaign '{request.campaign_name}' is associated with Foundry world "
                            f"'{_linked.get('world_name') or _linked.get('world_id')}', not '{_wtitle or _wid}'.",
                            "CAMPAIGN_WORLD_MISMATCH", 409,
                        )
                elif _wtitle or _wid:
                    link_world_to_campaign(request.campaign_name, _wtitle, _wid)
            except Exception as _le:
                if isinstance(_le, ApiError):
                    raise
                logger.debug(f"[WorldMatch] Could not link world to campaign: {_le}")

        # Refresh active-modules list so new campaign prompt reflects current Foundry setup
        if state.foundry_client and state.llm_manager:
            try:
                _mscan = await state.foundry_client.scan_world()
                _mods = [
                    m.get("title") or m.get("name") or m.get("id")
                    for m in (_mscan.get("modules") or [])
                    if m.get("active") or m.get("enabled")
                ]
                state.llm_manager.set_active_modules([m for m in _mods if m])
            except Exception as _me:
                logger.debug(f"[Modules] Module refresh failed: {_me}")

        # Invalidate cached system prompt so the LLM picks up the new campaign context
        if state.llm_manager and hasattr(state.llm_manager, 'invalidate_system_prompt'):
            state.llm_manager.invalidate_system_prompt()
        if state.chat_listener and hasattr(state.chat_listener, 'reload_system_prompt'):
            await state.chat_listener.reload_system_prompt()
        elif state.chat_listener and hasattr(state.chat_listener, '_build_system_prompt'):
            state.chat_listener._build_system_prompt()

        # Reset message ID for clean conversation
        if state.foundry_client:
            state.foundry_client.reset_message_id()

        # Ensure the world is unpaused and the AI is running before the opening
        # narration fires. A world left paused from a previous session would
        # otherwise trigger our pauseGame hook and suppress the session_start.
        if state.foundry_client:
            try:
                await state.foundry_client.execute_js(
                    "if(game.paused){game.togglePause(false,true);}"
                )
            except Exception as _pe:
                logger.warning(f"Could not unpause Foundry on campaign start: {_pe}")

        # Reset idle timer and fire a session_start opening so the AI sets up
        # the scene and places tokens rather than waiting for the first player message.
        if state.chat_listener:
            if not state.chat_listener._running:
                await state.chat_listener.start()
            state.chat_listener._running = True
            # Actors were just (re)deployed — refresh which Foundry user owns
            # which PC before the opening narration/prompt needs it, rather
            # than relying solely on a later scene-change event to catch it.
            await state.chat_listener._update_player_actors()
            await state.chat_listener.sync_active_scene()
            state.chat_listener._reset_idle_timer()
            spawn(state.chat_listener._process_proactive_action(reason="session_start"))

        # Broadcast session start so dashboard updates
        await broadcast_state_update({
            "type": "session_started",
            "session_id": session_id,
            "campaign_name": request.campaign_name,
        })

        return CampaignStartResponse(
            status="started",
            session_id=session_id,
            campaign_name=request.campaign_name,
            message=f"Session {session_id} started for campaign '{request.campaign_name}'.",
        )
    except Exception as e:
        logger.exception("Failed to start campaign")
        return CampaignStartResponse(
            status="error",
            session_id="",
            campaign_name=request.campaign_name,
            error=str(e),
        )


@router.post("/api/session/end", response_model=SessionEndResponse)
async def end_session_endpoint(request: SessionEndRequest, state: AppState = Depends(get_app_state)):

    """End the current session prematurely.

    This generates a session summary and marks the session as ended.
    Players can use this at any time during gameplay.
    """
    try:
        # Pause the chat listener if running
        if state.chat_listener and state.chat_listener._running:
            state.chat_listener._running = False
            await broadcast_state_update({"type": "ai_paused", "reason": request.reason})

        # Get active session
        session_id = await state.db.get_active_session()
        if not session_id:
            return SessionEndResponse(
                status="no_active_session",
                session_id="",
                campaign_name="",
                message="No active session to end.",
            )

        # Get current state for summary
        state_snapshot = state.state_tracker.state.model_dump() if state.state_tracker else {}

        # Generate a brief summary using LLM if available
        summary_text = ""
        if state.llm_manager:
            try:
                summary_text = await state.llm_manager.generate(
                    user_message=f"Summarize this D&D session ending. Current state: {json.dumps(state_snapshot, default=str)}. Keep it brief (2-3 sentences) and note any important plot points, unresolved quests, or character moments.",
                )
                summary_text = json.dumps(summary_text, default=str)
            except Exception:
                summary_text = json.dumps(state_snapshot, default=str)

        # End the session
        await state.db.close_session(session_id)

        # Broadcast end event
        await broadcast_state_update({
            "type": "session_ended",
            "session_id": session_id,
            "reason": request.reason,
            "summary": summary_text,
        })

        return SessionEndResponse(
            status="ended",
            session_id=session_id,
            campaign_name=state.state_tracker.state.campaign if state.state_tracker else "",
            summary=summary_text,
            message=f"Session {session_id} ended. {request.reason}",
        )
    except Exception as e:
        logger.exception("Failed to end session")
        return SessionEndResponse(
            status="error",
            session_id="",
            campaign_name="",
            error=str(e),
        )


@router.get("/api/campaign/list", response_model=CampaignListResponse)
async def list_campaigns_endpoint(state: AppState = Depends(get_app_state)):

    """List all generated campaigns in the vault."""
    try:
        from campaign.obsidian_sync import list_campaigns
        campaigns = list_campaigns()
        return CampaignListResponse(campaigns=campaigns)
    except Exception as e:
        return CampaignListResponse(
            campaigns=[],
            error=str(e),
        )


@router.get("/api/campaign/get/{campaign_name}")
async def get_campaign_endpoint(campaign_name: str, state: AppState = Depends(get_app_state)):

    """Get a specific campaign's data."""
    try:
        from campaign.obsidian_sync import get_campaign_manifest, get_campaign_folder, resolve_vault_path

        vault = resolve_vault_path(settings.campaign_vault_path)
        folder = get_campaign_folder(vault, campaign_name)

        manifest = get_campaign_manifest(folder)
        if manifest:
            # Also load the campaign JSON
            import json
            campaign_file = folder / "campaign.json"
            if campaign_file.exists():
                with open(campaign_file) as f:
                    data = json.load(f)
                manifest["data"] = data

            # Add computed counts for frontend display
            manifest["npc_count"] = len(manifest.get("npcs", []))
            manifest["location_count"] = len(manifest.get("locations", [])) or len(manifest.get("locations_list", [])) or 0
            manifest["quest_count"] = len(manifest.get("quests", [])) or len(manifest.get("quest_logs", [])) or 0
            manifest["journal_entries"] = len(manifest.get("journal_entries", []))

            return manifest
        return JSONResponse(
            status_code=404,
            content=ErrorResponse(
                status="error",
                error=f"Campaign '{campaign_name}' not found",
                code="CAMPAIGN_NOT_FOUND"
            ).model_dump()
        )
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(
                status="error",
                error=f"Failed to load campaign: {str(e)}",
                code="CAMPAIGN_LOAD_FAILED"
            ).model_dump()
        )


class CampaignDeleteRequest(BaseModel):
    """Request body for deleting a campaign."""
    name: str


@router.post("/api/campaign/delete", response_model=dict)
async def delete_campaign_endpoint(request: CampaignDeleteRequest, state: AppState = Depends(get_app_state)):
    """Delete a campaign from the vault."""
    try:
        from campaign.obsidian_sync import delete_campaign
        deleted = await delete_campaign(request.name)
        return {"status": "deleted" if deleted else "not_found"}
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(
                status="error",
                error=f"Failed to delete campaign: {str(e)}",
                code="CAMPAIGN_DELETE_FAILED"
            ).model_dump()
        )


@router.post("/api/campaign/enrich-scenes")
async def enrich_scenes_endpoint(state: AppState = Depends(get_app_state)):
    """Manually trigger enrichment (place walls, lights, sounds) on deployed scenes."""
    require_foundry(state)
    if not state.campaign_loader or not state.campaign_loader.current_campaign_name:
        raise ApiError("No campaign currently loaded", "NO_CAMPAIGN_LOADED", 400)

    campaign_name = state.campaign_loader.current_campaign_name
    campaign_data = await state.campaign_loader.load_campaign(campaign_name)
    deployment = await CampaignStore(campaign_name).load_deployment()
    if not deployment:
        raise ApiError(f"Deployment state not found for {campaign_name}", "DEPLOYMENT_NOT_FOUND", 404)

    try:
        # Run enrichment
        orchestrator = CampaignOrchestrator()
        result = await orchestrator.enrich_scenes(
            campaign_data=campaign_data,
            foundry_client=state.foundry_client,
            deployment=deployment,
            on_progress=lambda msg, **kw: logger.info(f"[Enrich] {msg}")
        )

        return {
            "status": "ok",
            "message": "Enrichment complete",
            "enriched": result.get("enriched", 0),
            "skipped": result.get("skipped", 0),
            "errors": result.get("errors", [])
        }
    except Exception as e:
        logger.error(f"Enrichment error: {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(
                status="error",
                error=f"Enrichment failed: {str(e)}",
                code="ENRICHMENT_FAILED"
            ).model_dump()
        )


class OptimizeCampaignRequest(BaseModel):
    """Request to analyze and optimize a campaign."""
    campaign_name: str


@router.post("/api/campaign/analyze-and-optimize")
async def analyze_and_optimize_campaign(request: OptimizeCampaignRequest, state: AppState = Depends(get_app_state)):
    """Analyze campaign and generate module-based optimization recommendations."""
    if not request.campaign_name:
        raise ApiError("Campaign name required", "NO_CAMPAIGN_PROVIDED", 400)

    world_error = await _select_campaign_world(request.campaign_name, state)
    if world_error:
        raise ApiError(world_error, "FOUNDRY_UNAVAILABLE", 503)
    require_foundry(state)

    store = CampaignStore(request.campaign_name)
    campaign_data = await store.load()

    try:
        # Analyze locally (fast, no LLM) and APPLY the enhancements directly:
        # walls, doors, lights, sounds, and fog/vision config on deployed
        # scenes. The previous implementation spent 30+ minutes of LLM calls
        # (module discovery, synergy mapping, story enrichment) producing a
        # report that was never applied to Foundry.
        from campaign.analyzer import CampaignAnalyzer
        from campaign.orchestrator import CampaignOrchestrator

        analyzer = CampaignAnalyzer()
        analysis = await analyzer.analyze_campaign(campaign_data)

        orch = CampaignOrchestrator()
        scan = await orch.scan_foundry_world(state.foundry_client)
        active_modules = scan.get("active_modules", {})

        # Apply scene enrichment to everything this campaign has deployed
        enrich_summary = {"enriched": 0, "skipped": 0, "errors": ["not deployed yet — deploy the campaign first"]}
        deployment = await store.load_deployment()
        if deployment:
            enrich_summary = await orch.enrich_scenes(
                campaign_data, state.foundry_client, deployment
            )

        recommendations = [{
            "priority": "high",
            "category": "Applied",
            "count": enrich_summary.get("enriched", 0),
            "action": (
                f"Enriched {enrich_summary.get('enriched', 0)} scene(s) with walls, "
                f"lights, sounds, and fog/vision config ({enrich_summary.get('skipped', 0)} skipped)"
            ),
            "details": enrich_summary.get("errors", [])[:3],
        }]
        for gap in analysis.get("immersion_gaps", [])[:3]:
            recommendations.append({
                "priority": "medium",
                "category": "Immersion Gap",
                "count": 1,
                "action": gap if isinstance(gap, str) else str(gap),
                "details": [],
            })

        return {
            "status": "complete",
            "campaign_name": request.campaign_name,
            "analysis": {
                "scene_count": len(analysis.get("scenes", [])),
                "encounter_count": len(analysis.get("encounters", [])),
                "npc_count": len(analysis.get("npcs", [])),
                "narrative_arcs": len(analysis.get("narrative_arcs", [])),
                "drama_analysis": analysis.get("pacing", {}),
                "immersion_gaps_identified": len(analysis.get("immersion_gaps", [])),
            },
            "modules": {
                "total_installed": len(active_modules),
                "enabled": len(active_modules),
                "modules_list": [
                    {
                        "id": mid,
                        "name": info.get("title", mid),
                        "enabled": True,
                        "capabilities": [],
                        "narrative_uses": [],
                    }
                    for mid, info in active_modules.items()
                ],
            },
            "applied": {
                "scenes_enriched": enrich_summary.get("enriched", 0),
                "scenes_skipped": enrich_summary.get("skipped", 0),
                "errors": enrich_summary.get("errors", []),
            },
            "synergies": {
                "scene_synergies": enrich_summary.get("enriched", 0),
                "encounter_synergies": 0,
                "npc_synergies": 0,
                "immersion_gap_fills": 0,
                "details": {},
            },
            "enhancements": {"applied": True},
            "recommendations": recommendations,
        }

    except Exception as e:
        logger.error(f"Campaign optimization error: {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(
                status="error",
                error=f"Campaign optimization failed: {str(e)}",
                code="OPTIMIZATION_FAILED"
            ).model_dump()
        )


class OptimizeSceneRequest(BaseModel):
    """Request to optimize a newly created scene."""
    scene: dict
    campaign_name: Optional[str] = None


class OptimizeEncounterRequest(BaseModel):
    """Request to optimize a newly created encounter."""
    encounter: dict
    campaign_name: Optional[str] = None


class OptimizeQuestRequest(BaseModel):
    """Request to optimize a newly created quest."""
    quest: dict
    campaign_name: Optional[str] = None


@router.post("/api/campaign/auto-optimize-scene")
async def auto_optimize_scene(request: OptimizeSceneRequest, state: AppState = Depends(get_app_state)):
    """Auto-optimize a newly created scene with module enhancements."""
    if not state.campaign_loader or not state.foundry_client or not state.foundry_client.is_connected:
        return JSONResponse(
            status_code=503,
            content=ErrorResponse(
                status="error",
                error="Foundry not connected or campaign loader not ready",
                code="AUTO_OPTIMIZE_NOT_READY"
            ).model_dump()
        )

    try:
        from campaign.auto_optimizer import AutoOptimizer

        campaign_name = request.campaign_name or state.campaign_loader.current_campaign_name
        if not campaign_name:
            return JSONResponse(
                status_code=400,
                content=ErrorResponse(
                    status="error",
                    error="No campaign name provided or loaded",
                    code="NO_CAMPAIGN"
                ).model_dump()
            )

        campaign_data = await state.campaign_loader.load_campaign(campaign_name)

        optimizer = AutoOptimizer(
            llm_manager=state.llm_manager,
            foundry_client=state.foundry_client
        )
        result = await optimizer.optimize_new_scene(request.scene, campaign_data)

        return {
            "status": "optimized",
            "scene": request.scene.get("name"),
            "enhancements": result,
        }

    except Exception as e:
        logger.error(f"Scene auto-optimization error: {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(
                status="error",
                error=f"Scene auto-optimization failed: {str(e)}",
                code="SCENE_OPTIMIZE_FAILED"
            ).model_dump()
        )


@router.post("/api/campaign/auto-optimize-encounter")
async def auto_optimize_encounter(request: OptimizeEncounterRequest, state: AppState = Depends(get_app_state)):
    """Auto-optimize a newly created encounter with module enhancements."""
    if not state.campaign_loader or not state.foundry_client or not state.foundry_client.is_connected:
        return JSONResponse(
            status_code=503,
            content=ErrorResponse(
                status="error",
                error="Foundry not connected or campaign loader not ready",
                code="AUTO_OPTIMIZE_NOT_READY"
            ).model_dump()
        )

    try:
        from campaign.auto_optimizer import AutoOptimizer

        campaign_name = request.campaign_name or state.campaign_loader.current_campaign_name
        if not campaign_name:
            return JSONResponse(
                status_code=400,
                content=ErrorResponse(
                    status="error",
                    error="No campaign name provided or loaded",
                    code="NO_CAMPAIGN"
                ).model_dump()
            )

        campaign_data = await state.campaign_loader.load_campaign(campaign_name)

        optimizer = AutoOptimizer(
            llm_manager=state.llm_manager,
            foundry_client=state.foundry_client
        )
        result = await optimizer.optimize_new_encounter(request.encounter, campaign_data)

        return {
            "status": "optimized",
            "encounter": request.encounter.get("name"),
            "enhancements": result,
        }

    except Exception as e:
        logger.error(f"Encounter auto-optimization error: {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(
                status="error",
                error=f"Encounter auto-optimization failed: {str(e)}",
                code="ENCOUNTER_OPTIMIZE_FAILED"
            ).model_dump()
        )


@router.post("/api/campaign/auto-optimize-quest")
async def auto_optimize_quest(request: OptimizeQuestRequest, state: AppState = Depends(get_app_state)):
    """Auto-optimize a newly created quest with narrative enhancements."""
    if not state.campaign_loader or not state.foundry_client or not state.foundry_client.is_connected:
        return JSONResponse(
            status_code=503,
            content=ErrorResponse(
                status="error",
                error="Foundry not connected or campaign loader not ready",
                code="AUTO_OPTIMIZE_NOT_READY"
            ).model_dump()
        )

    try:
        from campaign.auto_optimizer import AutoOptimizer

        campaign_name = request.campaign_name or state.campaign_loader.current_campaign_name
        if not campaign_name:
            return JSONResponse(
                status_code=400,
                content=ErrorResponse(
                    status="error",
                    error="No campaign name provided or loaded",
                    code="NO_CAMPAIGN"
                ).model_dump()
            )

        campaign_data = await state.campaign_loader.load_campaign(campaign_name)

        optimizer = AutoOptimizer(
            llm_manager=state.llm_manager,
            foundry_client=state.foundry_client
        )
        result = await optimizer.optimize_new_quest(request.quest, campaign_data)

        return {
            "status": "optimized",
            "quest": request.quest.get("title"),
            "enhancements": result,
        }

    except Exception as e:
        logger.error(f"Quest auto-optimization error: {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(
                status="error",
                error=f"Quest auto-optimization failed: {str(e)}",
                code="QUEST_OPTIMIZE_FAILED"
            ).model_dump()
        )
