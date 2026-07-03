"""NPC endpoints: actor listing, personality parsing, registry, relationships."""

from typing import Optional

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from api.deps import AppState, ErrorResponse, get_app_state

router = APIRouter(tags=["npc"])


@router.get("/api/npcs")
async def list_npcs(state: AppState = Depends(get_app_state)):

    """List all NPC actors in Foundry."""
    if state.foundry_client and state.foundry_client.is_connected:
        try:
            actors = await state.foundry_client.get_actors(world_only=True)
            return {"npcs": actors}
        except Exception as e:
            return JSONResponse(
                status_code=500,
                content=ErrorResponse(
                    status="error",
                    error=f"Failed to fetch NPCs: {str(e)}",
                    code="NPC_FETCH_FAILED"
                ).model_dump()
            )
    return {"npcs": []}


@router.get("/api/npc_context", response_model=dict)
async def get_npc_context_endpoint(state: AppState = Depends(get_app_state)):

    """Get current NPC context for debugging."""
    if state.state_tracker:
        return {"context": state.state_tracker.state.npc_context}
    return {"context": ""}


@router.post("/api/npc/personality")
async def parse_npc_personality(
    npc_id: str, npc_name: str, description: str,
    state: AppState = Depends(get_app_state)
):
    """Parse NPC description and extract personality traits."""
    if not state.personality_engine:
        return JSONResponse(
            status_code=503,
            content={"error": "Personality engine not initialized"}
        )

    personality = state.personality_engine.parse_npc_description(npc_id, npc_name, description)
    return {
        "npc_id": npc_id,
        "npc_name": npc_name,
        "traits": personality.traits,
        "strengths": personality.strengths,
        "flaws": personality.flaws,
        "motivations": personality.motivations,
        "mannerisms": personality.mannerisms,
        "speech_pattern": personality.speech_pattern,
    }


@router.get("/api/npc/context")
async def get_npc_personality_context(npc_id: str, state: AppState = Depends(get_app_state)):
    """Get formatted personality context for an NPC."""
    if not state.npc_registry:
        return {"context": "", "error": "NPC registry not initialized"}

    context = state.npc_registry.get_npc_context(npc_id)
    return {"npc_id": npc_id, "context": context}


@router.post("/api/npc/register")
async def register_npc(
    npc_id: str, npc_name: str, description: str,
    appearance: Optional[str] = None,
    class_name: Optional[str] = None,
    level: Optional[int] = None,
    alignment: Optional[str] = None,
    state: AppState = Depends(get_app_state)
):
    """Register an NPC and parse its personality."""
    if not state.npc_registry or not state.personality_engine:
        return JSONResponse(
            status_code=503,
            content={"error": "NPC systems not initialized"}
        )

    # Register the NPC
    npc_record = state.npc_registry.register_npc(
        npc_id, npc_name, description,
        appearance=appearance,
        class_name=class_name,
        level=level,
        alignment=alignment
    )

    # Parse personality
    personality = state.personality_engine.parse_npc_description(npc_id, npc_name, description)
    state.npc_registry.set_npc_personality(npc_id, personality.traits)

    return {
        "npc_id": npc_id,
        "npc_name": npc_name,
        "registered": True,
        "personality": personality.traits,
    }


@router.post("/api/npc/relationship")
async def set_npc_relationship(
    source_id: str, target_id: str, target_name: str,
    relationship_type: str, strength: float = 0.5,
    state: AppState = Depends(get_app_state)
):
    """Set or update a relationship between NPCs or NPC and PC."""
    if not state.npc_registry:
        return JSONResponse(
            status_code=503,
            content={"error": "NPC registry not initialized"}
        )

    rel = state.npc_registry.add_relationship(
        source_id, target_id, target_name, relationship_type, strength
    )
    return {
        "source_id": source_id,
        "target_id": target_id,
        "relationship_type": relationship_type,
        "strength": rel.strength,
    }


@router.get("/api/npc/relationships")
async def get_npc_relationships(npc_id: str, state: AppState = Depends(get_app_state)):
    """Get all relationships for an NPC."""
    if not state.npc_registry:
        return {"relationships": {}, "error": "NPC registry not initialized"}

    relationships = state.npc_registry.get_npc_relationships(npc_id)
    return {
        "npc_id": npc_id,
        "relationships": {
            target_id: {
                "type": rel.relationship_type,
                "strength": rel.strength,
                "last_interaction": rel.last_interaction,
            }
            for target_id, rel in relationships.items()
        }
    }
