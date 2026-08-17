"""Immersion feature endpoints (Tier 6): weather, effects, vision, macros, particles, loot."""

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends

from api.deps import AppState, get_app_state

router = APIRouter(prefix="/api/immersion", tags=["immersion"])


@router.post("/weather")
async def set_weather_endpoint(
    weather: str,
    state: AppState = Depends(get_app_state)
):
    """Set weather and atmospheric effects."""
    if not state.ambient_manager:
        return {"error": "Ambient manager not initialized"}

    from immersion.ambient import WeatherType
    try:
        weather_type = WeatherType(weather.lower())
        result = state.ambient_manager.set_weather(weather_type)
        return result
    except ValueError:
        return {"error": f"Unknown weather type: {weather}"}


@router.post("/time")
async def set_time_endpoint(
    time: str,
    state: AppState = Depends(get_app_state)
):
    """Set time of day for atmospheric changes."""
    if not state.ambient_manager:
        return {"error": "Ambient manager not initialized"}

    from immersion.ambient import TimeOfDay
    try:
        time_type = TimeOfDay(time.lower())
        result = state.ambient_manager.set_time(time_type)
        return result
    except ValueError:
        return {"error": f"Unknown time: {time}"}


@router.get("/atmosphere")
async def get_atmosphere_endpoint(state: AppState = Depends(get_app_state)):
    """Get current atmospheric description and modifiers."""
    if not state.ambient_manager:
        return {"error": "Ambient manager not initialized"}

    description = state.ambient_manager.get_atmosphere_description()
    modifiers = state.ambient_manager.get_environmental_modifiers()

    return {
        "description": description,
        "modifiers": modifiers,
    }


@router.post("/token-effect")
async def apply_token_effect_endpoint(
    token_id: str,
    effect_type: str,
    effect_name: str,
    duration: Optional[int] = None,
    state: AppState = Depends(get_app_state)
):
    """Apply visual effects to tokens (conditions, auras, etc)."""
    if not state.effects_manager:
        return {"error": "Effects manager not initialized"}

    if effect_type == "condition":
        result = state.effects_manager.apply_condition_visual(token_id, effect_name, duration)
    elif effect_type == "aura":
        result = state.effects_manager.apply_aura(token_id, effect_name, duration)
    else:
        return {"error": f"Unknown effect type: {effect_type}"}

    return result


@router.get("/token-effects/{token_id}")
async def get_token_effects_endpoint(
    token_id: str,
    state: AppState = Depends(get_app_state)
):
    """Get all active effects for a token."""
    if not state.effects_manager:
        return {"error": "Effects manager not initialized"}

    effects = state.effects_manager.get_token_effects(token_id)
    return {"token_id": token_id, "effects": effects}


@router.post("/vision")
async def update_vision_endpoint(
    token_id: str,
    vision_range: float,
    has_light: bool = False,
    light_radius: Optional[float] = None,
    state: AppState = Depends(get_app_state)
):
    """Update vision and fog of war for a token."""
    if not state.vision_manager:
        return {"error": "Vision manager not initialized"}

    result = state.vision_manager.set_vision_range(token_id, vision_range)

    if has_light and light_radius:
        light_result = state.vision_manager.apply_light_source(token_id, light_radius)
        result["light"] = light_result

    return result


@router.get("/vision-status")
async def get_vision_status_endpoint(state: AppState = Depends(get_app_state)):
    """Get current vision and fog of war status."""
    if not state.vision_manager:
        return {"error": "Vision manager not initialized"}

    status = state.vision_manager.get_vision_status()
    return status


@router.post("/macro/register")
async def register_macro_endpoint(
    macro_id: str,
    name: str,
    description: str,
    action_type: str,
    parameters: Dict[str, Any],
    state: AppState = Depends(get_app_state)
):
    """Register a new GM macro."""
    if not state.macro_manager:
        return {"error": "Macro manager not initialized"}

    result = state.macro_manager.register_macro(
        macro_id, name, description, action_type, parameters
    )
    return result


@router.post("/macro/execute")
async def execute_macro_endpoint(
    macro_id: str,
    overrides: Optional[Dict[str, Any]] = None,
    state: AppState = Depends(get_app_state)
):
    """Execute a registered macro by dispatching the action it wraps."""
    if not state.macro_manager:
        return {"error": "Macro manager not initialized"}
    if not state.action_dispatcher:
        return {"error": "Action dispatcher not initialized"}

    action = state.macro_manager.resolve_macro(macro_id, overrides)
    if action.get("error"):
        return action
    result = await state.action_dispatcher.execute(action)
    return {"macro_id": macro_id, "action_type": action["type"], "result": result}


@router.get("/macros")
async def list_macros_endpoint(state: AppState = Depends(get_app_state)):
    """List all registered macros."""
    if not state.macro_manager:
        return {"error": "Macro manager not initialized"}

    macros = state.macro_manager.list_macros()
    return {"macros": macros}


@router.get("/macro-templates")
async def get_macro_templates_endpoint(state: AppState = Depends(get_app_state)):
    """Get available macro templates."""
    if not state.macro_manager:
        return {"error": "Macro manager not initialized"}

    templates = state.macro_manager.get_macro_templates()
    return {"templates": templates}


@router.post("/particle")
async def create_particle_effect_endpoint(
    effect_id: str,
    name: str,
    effect_type: str,
    x: float,
    y: float,
    color: str = "#ffffff",
    duration: Optional[int] = None,
    intensity: float = 0.7,
    size: str = "medium",
    state: AppState = Depends(get_app_state)
):
    """Create a particle effect at a location."""
    if not state.particle_manager:
        return {"error": "Particle manager not initialized"}

    result = state.particle_manager.create_effect(
        effect_id, name, effect_type, x, y, color, duration, intensity, size
    )
    return result


@router.post("/particle-preset")
async def create_particle_from_preset_endpoint(
    effect_id: str,
    preset_name: str,
    x: float,
    y: float,
    state: AppState = Depends(get_app_state)
):
    """Create a particle effect from a preset."""
    if not state.particle_manager:
        return {"error": "Particle manager not initialized"}

    result = state.particle_manager.create_effect_from_preset(
        effect_id, preset_name, x, y
    )
    return result


@router.get("/particles")
async def get_active_particles_endpoint(state: AppState = Depends(get_app_state)):
    """Get all active particle effects."""
    if not state.particle_manager:
        return {"error": "Particle manager not initialized"}

    effects = state.particle_manager.get_active_effects()
    count = state.particle_manager.get_effect_count()
    return {"active_effects": effects, "count": count}


@router.get("/particle-presets")
async def get_particle_presets_endpoint(state: AppState = Depends(get_app_state)):
    """Get available particle effect presets."""
    if not state.particle_manager:
        return {"error": "Particle manager not initialized"}

    presets = state.particle_manager.list_presets()
    return {"presets": presets}


@router.post("/item-pool")
async def add_item_to_pool_endpoint(
    pool_name: str,
    item_id: str,
    name: str,
    rarity: str,
    value_gp: float,
    weight_lbs: float,
    description: str,
    quantity: int = 1,
    state: AppState = Depends(get_app_state)
):
    """Add an item to a loot pool."""
    if not state.item_manager:
        return {"error": "Item manager not initialized"}

    from immersion.items import LootItem

    item = LootItem(
        item_id=item_id,
        name=name,
        rarity=rarity,
        value_gp=value_gp,
        weight_lbs=weight_lbs,
        description=description,
        quantity=quantity,
    )

    result = state.item_manager.add_item_to_pool(pool_name, item)
    return result


@router.get("/item-pools")
async def list_item_pools_endpoint(state: AppState = Depends(get_app_state)):
    """List all loot pools."""
    if not state.item_manager:
        return {"error": "Item manager not initialized"}

    pools = state.item_manager.list_loot_pools()
    return {"pools": pools}


@router.get("/inventory/{actor_id}")
async def get_actor_inventory_endpoint(
    actor_id: str,
    state: AppState = Depends(get_app_state)
):
    """Get items held by an actor."""
    if not state.item_manager:
        return {"error": "Item manager not initialized"}

    inventory = state.item_manager.get_actor_inventory(actor_id)
    return inventory
