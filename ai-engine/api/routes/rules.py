"""Rules reference endpoints: SRD search, spells, conditions, DCs."""

from fastapi import APIRouter, Depends

from api.deps import AppState, get_app_state

router = APIRouter(tags=["rules"])


@router.get("/api/srd/search")
async def search_srd(query: str, max_results: int = 3, state: AppState = Depends(get_app_state)):

    """Search the SRD for rules reference."""
    if state.campaign_loader:
        results = await state.campaign_loader.search_srd(query, max_results)
        return {"results": results}
    return {"results": ""}


@router.get("/api/rules/spell")
async def get_spell(name: str, state: AppState = Depends(get_app_state)):
    """Look up a spell by name."""
    from rules.engine import RulesEngine
    engine = RulesEngine()
    spell = engine.get_spell(name)
    if spell:
        return {"spell": spell, "found": True}
    return {"spell": None, "found": False}


@router.get("/api/rules/spells")
async def search_spells(query: str, state: AppState = Depends(get_app_state)):
    """Search spells by name or keyword."""
    from rules.engine import RulesEngine
    engine = RulesEngine()
    results = engine.search_spells(query)
    return {"spells": results}


@router.get("/api/rules/condition")
async def get_condition(name: str, state: AppState = Depends(get_app_state)):
    """Look up a condition by name."""
    from rules.engine import RulesEngine
    engine = RulesEngine()
    description = engine.get_condition(name)
    if description:
        return {"condition": name, "description": description, "found": True}
    return {"condition": name, "description": None, "found": False}


@router.get("/api/rules/dc")
async def get_dc(difficulty: str, state: AppState = Depends(get_app_state)):
    """Get a suggested DC for a skill check."""
    from rules.engine import RulesEngine
    engine = RulesEngine()
    dc = engine.suggest_dc(difficulty)
    return {"difficulty": difficulty, "dc": dc}


@router.get("/api/rules/reference")
async def get_rules_reference(state: AppState = Depends(get_app_state)):
    """Get a summary of available rules."""
    from rules.engine import RulesEngine
    engine = RulesEngine()
    summary = engine.reference_summary()
    return {"rules": summary}
