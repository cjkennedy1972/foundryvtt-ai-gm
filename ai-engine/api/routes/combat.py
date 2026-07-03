"""Combat control and analysis endpoints: loop start/stop, difficulty, tactics."""

from typing import List

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from api.deps import AppState, ErrorResponse, get_app_state, require_foundry

router = APIRouter(prefix="/api/combat", tags=["combat"])


@router.post("/start", response_model=dict)
async def start_combat_endpoint(state: AppState = Depends(get_app_state)):

    """Start combat loop with tokens from current scene."""
    if not state.combat_loop:
        return JSONResponse(
            status_code=503,
            content=ErrorResponse(
                status="error",
                error="Combat loop not initialized",
                code="COMBAT_NOT_READY"
            ).model_dump()
        )
    if not state.foundry_client:
        return JSONResponse(
            status_code=503,
            content=ErrorResponse(
                status="error",
                error="Not connected to Foundry",
                code="FOUNDRY_NOT_CONNECTED"
            ).model_dump()
        )
    try:
        tokens = await state.foundry_client.get_scene_tokens()
        if not tokens:
            return JSONResponse(
                status_code=400,
                content=ErrorResponse(
                    status="error",
                    error="No tokens found on current scene",
                    code="NO_TOKENS_FOUND"
                ).model_dump()
            )
        await state.combat_loop.start_combat_loop(tokens)
        return {"status": "started", "tokens": len(tokens)}
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(
                status="error",
                error=f"Failed to start combat: {str(e)}",
                code="COMBAT_START_FAILED"
            ).model_dump()
        )


@router.post("/stop", response_model=dict)
async def stop_combat_endpoint(state: AppState = Depends(get_app_state)):

    """Stop the combat loop."""
    if state.combat_loop:
        await state.combat_loop.stop()
        return {"status": "stopped"}
    return {"status": "not running"}


@router.get("/status", response_model=dict)
async def get_combat_status_endpoint(state: AppState = Depends(get_app_state)):

    """Get combat loop status."""
    if state.combat_loop:
        return {
            "running": state.combat_loop.is_running,
            "round": state.combat_loop.current_round,
            "turn": state.combat_loop.current_turn,
            "turn_order": state.combat_loop.turn_order,
        }
    return {"running": False}


@router.get("/snapshot", response_model=dict)
async def get_combat_snapshot_endpoint(state: AppState = Depends(get_app_state)):
    """Return the pre-combat state snapshot saved at the start of the last combat."""
    snapshot = state.state_tracker.get_combat_snapshot()
    if snapshot is None:
        return {"snapshot": None, "message": "No combat snapshot available"}
    return {"snapshot": snapshot}


@router.post("/difficulty/suggest")
async def suggest_encounter_difficulty(
    num_players: int, avg_level: float, monster_crs: List[float],
    state: AppState = Depends(get_app_state)
):
    """Suggest encounter difficulty based on party and monsters."""
    from combat.difficulty import DynamicDifficulty, EncounterProfile, PartyComposition

    difficulty_engine = DynamicDifficulty()
    party = difficulty_engine.get_party_composition(num_players, avg_level)
    encounter = EncounterProfile(
        monster_names=[f"Monster {i}" for i in range(len(monster_crs))],
        monster_crs=monster_crs
    )

    difficulty = difficulty_engine.calculate_difficulty(encounter, party)
    recommendations = difficulty_engine.get_action_recommendations(encounter, party)

    return {
        "difficulty": difficulty.value,
        "estimated_xp": encounter.total_xp,
        "party_power_rating": party.party_power_rating,
        "recommendations": recommendations,
    }


@router.get("/difficulty/suggestions")
async def get_encounter_suggestions(
    num_players: int, avg_level: float, difficulty: str,
    state: AppState = Depends(get_app_state)
):
    """Get encounter suggestions for a party and difficulty level."""
    from combat.difficulty import DynamicDifficulty, EncounterDifficulty

    difficulty_engine = DynamicDifficulty()
    party = difficulty_engine.get_party_composition(num_players, avg_level)

    # Map string to enum
    difficulty_enum = EncounterDifficulty[difficulty.upper()]

    suggestions = difficulty_engine.suggest_encounters(party, difficulty_enum)

    return {
        "party_level": avg_level,
        "difficulty": difficulty,
        "suggestions": suggestions,
    }


@router.post("/tactical/analyze")
async def analyze_tactical_situation(
    actor_id: str,
    state: AppState = Depends(get_app_state)
):
    """Analyze tactical battlefield situation for a token from live scene geometry.

    Enemies/allies come from scene token disposition, not caller-supplied
    lists — the old version computed this over positions that were never
    populated (CombatMechanics instantiated fresh, per-call) and always
    returned empty results. See combat/tactics.py.
    """
    require_foundry(state)
    from actions.executors import _resolve_token_id
    from combat.tactics import build_tactical_snapshot

    token_id = await _resolve_token_id(actor_id, state.foundry_client)
    snapshot = await build_tactical_snapshot(state.foundry_client, token_id)

    return {
        "actor": actor_id,
        "analysis": snapshot or "No enemies visible on the current scene.",
    }


@router.post("/tactical/flanking")
async def check_flanking(
    attacker_id: str, target_id: str,
    state: AppState = Depends(get_app_state)
):
    """Check if attacker is flanking target, using live scene positions."""
    require_foundry(state)
    from actions.executors import _resolve_token_id
    from combat.tactics import fetch_scene_state, flanking_check

    attacker_tok = await _resolve_token_id(attacker_id, state.foundry_client)
    target_tok = await _resolve_token_id(target_id, state.foundry_client)
    scene_state = await fetch_scene_state(state.foundry_client)
    is_flanking = flanking_check(attacker_tok, target_tok, scene_state)

    if is_flanking is None:
        return {
            "attacker": attacker_id,
            "target": target_id,
            "is_flanking": False,
            "benefit": "Could not resolve attacker/target on the current scene",
        }
    return {
        "attacker": attacker_id,
        "target": target_id,
        "is_flanking": is_flanking,
        "benefit": "Gain advantage on attack roll" if is_flanking else "No flanking benefit",
    }
