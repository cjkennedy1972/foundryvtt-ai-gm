"""generate_map: battlemap style, grid/padding on the created scene, one retry."""

import tempfile
from unittest.mock import AsyncMock, MagicMock

import pytest

from actions.executors import MAP_GRID_PX, execute_generate_map
from actions.schemas import GenerateMapAction
from campaign.map_generator import MapGenerator


def _png():
    f = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    f.write(b"PNG")
    f.close()
    return f.name


def _env(results):
    app = MagicMock()
    app.map_output_dir = "/tmp/ai-gm-maps"
    app.map_generator = MagicMock()
    app.map_generator.generate_map = AsyncMock(side_effect=results)
    fc = MagicMock()
    fc.upload_file = AsyncMock(return_value={"path": "worlds/maps/m.png"})
    fc.create_entity = AsyncMock(return_value={"data": {"_id": "Scene.1"}})
    fc.set_active_scene = AsyncMock()
    return app, fc


def test_battlemap_is_the_default_style_and_has_its_own_prompt_and_negative():
    assert GenerateMapAction(prompt="a tavern floor", scene_name="Tavern").style == "battlemap"
    assert "top-down" in MapGenerator._STYLE_PREFIXES["battlemap"]
    for banned in ("grid lines", "text", "border", "characters", "black void"):
        assert banned in MapGenerator._BATTLEMAP_NEGATIVE
    # The other styles still ask for a visible grid / parchment; battlemap must not.
    assert "grid" not in MapGenerator._STYLE_PREFIXES["battlemap"].replace("grid-", "")


@pytest.mark.asyncio
@pytest.mark.parametrize("size", ["small", "medium", "large"])
async def test_scene_grid_divides_the_image_and_padding_is_zero(size):
    app, fc = _env([{"status": "success", "output_file": _png()}])
    result = await execute_generate_map(prompt="a crypt", scene_name="Crypt", size=size, app_state=app, foundry=fc)
    scene = fc.create_entity.call_args.args[1]
    assert result["success"] is True
    assert scene["width"] % MAP_GRID_PX == 0 and scene["height"] % MAP_GRID_PX == 0
    assert scene["grid"] == {"size": MAP_GRID_PX, "padding": 0} and scene["padding"] == 0


@pytest.mark.asyncio
async def test_a_failed_generation_is_retried_once():
    app, fc = _env([{"status": "failed", "error": "timeout"}, {"status": "success", "output_file": _png()}])
    result = await execute_generate_map(prompt="a crypt", scene_name="Crypt", app_state=app, foundry=fc)
    assert result["success"] is True
    assert app.map_generator.generate_map.await_count == 2
    assert app.map_generator.generate_map.call_args.kwargs["style"] == "battlemap"


@pytest.mark.asyncio
async def test_an_unreachable_backend_is_not_retried():
    app, fc = _env([{"status": "error", "error": "ComfyUI backend is not available", "provider": "none"}])
    result = await execute_generate_map(prompt="a crypt", scene_name="Crypt", app_state=app, foundry=fc)
    assert "error" in result
    assert app.map_generator.generate_map.await_count == 1


@pytest.mark.asyncio
async def test_generate_map_picks_the_styles_negative_prompt():
    gen = MapGenerator.__new__(MapGenerator)
    gen.health_check = AsyncMock(return_value={"comfyui": True})
    gen.generate_map_comfyui = AsyncMock(return_value={"status": "success"})
    await gen.generate_map("a crypt", output_dir=None, style="battlemap")
    assert gen.generate_map_comfyui.call_args.kwargs["negative_prompt"] == MapGenerator._BATTLEMAP_NEGATIVE
    await gen.generate_map("a crypt", output_dir=None, style="dungeon")
    assert gen.generate_map_comfyui.call_args.kwargs["negative_prompt"] != MapGenerator._BATTLEMAP_NEGATIVE
