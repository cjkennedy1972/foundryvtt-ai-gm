#!/usr/bin/env python3
"""
Integration tests for execute_generate_encounter (compendium path).

These lock in the deployment *contract* that the original design broke:
  - the real compendium stat block is imported into the world via
    ensure_monster_actor(), and
  - the token is placed by that world UUID (not by bare name, which fails for
    monsters not already in the world).

Run:
    cd ai-engine && python -m pytest tests/test_compendium_integration.py -v
    OR:
    cd ai-engine && python tests/test_compendium_integration.py
"""

import os
import sys
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from actions import executors
from combat.compendium_generator import Monster, cr_to_xp


def _make_foundry(include_world_npc=False):
    foundry = MagicMock()
    foundry.is_connected = True
    foundry.get_scene_details = AsyncMock(return_value={"width": 1000, "height": 800, "grid": {"size": 100}})
    world = []
    if include_world_npc:
        world = [
            {"name": "Doomed Knight", "cr": 5, "uuid": "Actor.fKusVTOkwnuYIvfs", "size": "med", "environment": "", "source": "world"},
        ]
    # execute_js returns the relay envelope; candidate query payload is under "result".
    foundry.execute_js = AsyncMock(return_value={"result": {
        "compendium": [
            {"name": "Goblin", "cr": 0.125, "uuid": "Compendium.dnd5e.monsters.Actor.gob", "size": "sml", "environment": "", "source": "compendium"},
            {"name": "Ogre", "cr": 2, "uuid": "Compendium.dnd5e.monsters.Actor.ogre", "size": "lg", "environment": "", "source": "compendium"},
        ],
        "world": world,
    }})
    foundry.place_token = AsyncMock(return_value={"id": "token123"})
    foundry.start_encounter = AsyncMock(return_value={"ok": True})
    return foundry


def test_deploy_imports_and_places_by_uuid():
    """Deployment must import via ensure_monster_actor and place by world UUID."""
    foundry = _make_foundry()

    async def _run():
        with patch(
            "campaign.monster_actor.ensure_monster_actor",
            new=AsyncMock(return_value="Actor.worlduuid"),
        ) as mock_ensure:
            result = await executors.execute_generate_encounter(
                party_level=5, party_size=4, difficulty="medium", foundry=foundry
            )
            return result, mock_ensure

    result, mock_ensure = asyncio.run(_run())

    # ensure_monster_actor was used to import the stat block(s)
    assert mock_ensure.await_count >= 1, "must import compendium actors into the world"

    # place_token was called by UUID, never by bare name
    assert foundry.place_token.await_count >= 1
    for call in foundry.place_token.await_args_list:
        assert call.kwargs.get("uuid"), "place_token must be called with a world uuid"

    assert result["deployed_to_foundry"] is True
    assert foundry.start_encounter.await_count == 1


def test_world_npc_placed_by_own_uuid_without_import():
    """A hostile campaign NPC must be placed by its existing world UUID and must
    NOT be re-imported via ensure_monster_actor."""
    foundry = _make_foundry(include_world_npc=True)
    # Force a high deadly budget so the CR-5 world NPC is selected.
    async def _run():
        with patch(
            "campaign.monster_actor.ensure_monster_actor",
            new=AsyncMock(return_value="Actor.imported"),
        ) as mock_ensure:
            result = await executors.execute_generate_encounter(
                party_level=12, party_size=4, difficulty="deadly", foundry=foundry
            )
            return result, mock_ensure

    result, mock_ensure = asyncio.run(_run())

    placed_world = [
        c for c in foundry.place_token.await_args_list
        if c.kwargs.get("uuid") == "Actor.fKusVTOkwnuYIvfs"
    ]
    assert placed_world, "campaign NPC should be placed by its own world UUID"
    # ensure_monster_actor must not be used for the world NPC's UUID
    assert all(
        c.args[1] != "Doomed Knight" if len(c.args) > 1 else True
        for c in mock_ensure.await_args_list
    ), "world NPC must not be re-imported"
    # The encounter should report the campaign NPC among its creatures.
    names = [c["name"] for c in result["encounter"]["creatures"]]
    assert "Doomed Knight" in names


def test_deploy_skips_unresolved_actor():
    """If an actor can't be resolved, skip it rather than placing a broken token."""
    foundry = _make_foundry()

    async def _run():
        with patch(
            "campaign.monster_actor.ensure_monster_actor",
            new=AsyncMock(return_value=None),  # resolution always fails
        ):
            return await executors.execute_generate_encounter(
                party_level=5, party_size=4, difficulty="medium", foundry=foundry
            )

    result = asyncio.run(_run())
    assert foundry.place_token.await_count == 0, "must not place tokens for unresolved actors"
    assert result["deployed_to_foundry"] is False


def test_generate_without_foundry_connection():
    """With no connection, still return encounter data (no deployment)."""
    foundry = _make_foundry()
    foundry.is_connected = False

    result = asyncio.run(
        executors.execute_generate_encounter(
            party_level=5, party_size=4, difficulty="medium", foundry=foundry
        )
    )
    assert result["type"] == "generate_encounter"
    assert "encounter" in result
    assert "deployed_to_foundry" not in result  # deployment block skipped


def test_uses_real_scene_dimensions():
    """Generator must be built from the real scene size, not the 800x600 default."""
    foundry = _make_foundry()
    foundry.get_scene_details = AsyncMock(
        return_value={"width": 2000, "height": 1500, "grid": {"size": 150}}
    )

    async def _run():
        with patch(
            "campaign.monster_actor.ensure_monster_actor",
            new=AsyncMock(return_value="Actor.worlduuid"),
        ):
            return await executors.execute_generate_encounter(
                party_level=8, party_size=4, difficulty="hard", foundry=foundry
            )

    result = asyncio.run(_run())
    # Placements should be able to use the larger canvas (x can exceed the
    # 800-wide default's right edge) and snap to the 150 grid.
    placements = result["encounter"]["placements"]
    assert placements, "should have placements"
    assert all(p["x"] % 150 == 0 and p["y"] % 150 == 0 for p in placements)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"PASS  {fn.__name__}")
    print(f"\nAll {len(fns)} integration tests passed!")
