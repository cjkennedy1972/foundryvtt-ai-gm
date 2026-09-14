#!/usr/bin/env python3
"""Mock-based unit tests for campaign/monster_actor.ensure_monster_actor.

Covers the full 3-tier resolution strategy:
1. World actor lookup (fast path, case-insensitive name match)
2. Compendium search + import (preserves stat block/portrait; ai-gm flag)
3. Placeholder fallback (with or without compendium art)

All Foundry I/O is mocked — no relay, no Foundry, no network.

Run:
    cd ai-engine && python -m pytest tests/test_monster_actor.py -v
"""

import asyncio
import os
import sys
from unittest.mock import AsyncMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from campaign.monster_actor import ensure_monster_actor


def make_foundry(**overrides):
    """Build an AsyncMock Foundry client with sensible per-method overrides."""
    f = AsyncMock()
    f.get_actors = AsyncMock(return_value=[])
    f._send = AsyncMock(return_value={})
    f.execute_js = AsyncMock(return_value={"result": {}})
    for name, value in overrides.items():
        setattr(f, name, value)
    return f


def test_world_actor_fast_path_returns_existing_uuid():
    """A matching world actor short-circuits before any compendium call."""
    f = make_foundry(
        get_actors=AsyncMock(return_value=[
            {"name": "Other Guy", "uuid": "Actor.other"},
            {"name": "Goblin", "uuid": "Actor.gob123"},
        ])
    )
    result = asyncio.run(ensure_monster_actor(f, "goblin"))
    assert result == "Actor.gob123"
    f._send.assert_not_awaited()
    f.execute_js.assert_not_awaited()


def test_world_actor_lookup_case_insensitive():
    f = make_foundry(get_actors=AsyncMock(return_value=[{"name": "GRYFFON", "uuid": "Actor.gryffon"}]))
    assert asyncio.run(ensure_monster_actor(f, "gryffon")) == "Actor.gryffon"


def test_world_lookup_failure_falls_through_to_compendium():
    """A world-lookup RPC failure must not abort — compendium search still runs."""
    f = make_foundry(
        get_actors=AsyncMock(side_effect=RuntimeError("relay down")),
        _send=AsyncMock(return_value={
            "results": [
                {"name": "Ogre", "uuid": "Compendium.actors.ogre", "documentType": "Actor",
                 "package": "dnd5e", "img": "icons/ogre.png"},
            ]
        }),
        execute_js=AsyncMock(return_value={"result": {"uuid": "Actor.imported"}}),
    )
    assert asyncio.run(ensure_monster_actor(f, "Ogre")) == "Actor.imported"
    f.execute_js.assert_awaited_once()
    # The import JS must carry the ai-gm teardown flag.
    js = f.execute_js.await_args.args[0]
    assert "ai-gm" in js and "imported_monster: true" in js


def test_compendium_import_success_stamps_source_uuid():
    f = make_foundry(
        _send=AsyncMock(return_value={
            "results": [
                {"name": "Goblin", "uuid": "Compendium.actors.gob", "documentType": "Actor",
                 "package": "dnd5e"},
                # Same-named compendium Item must not be picked.
                {"name": "Goblin", "uuid": "Compendium.items.gobkit", "documentType": "Item",
                 "package": "dnd5e"},
            ]
        }),
        execute_js=AsyncMock(return_value={"result": {"uuid": "Actor.newgob"}}),
    )
    assert asyncio.run(ensure_monster_actor(f, "Goblin")) == "Actor.newgob"
    js = f.execute_js.await_args.args[0]
    assert "Compendium.actors.gob" in js


def test_compendium_import_failure_fetches_art_for_placeholder():
    """Import JS fails -> fetch img/tokenImg from the entry -> placeholder with art."""
    f = make_foundry(
        _send=AsyncMock(side_effect=[
            {"results": [
                {"name": "Troll", "uuid": "Compendium.actors.troll", "documentType": "Actor",
                 "package": "dnd5e", "img": "icons/search/troll.png"},
            ]},
            {"data": {"uuid": "Actor.placeholder"}},
        ]),
        execute_js=AsyncMock(side_effect=[
            {"result": {"error": "import failed"}},   # import attempt
            {"result": {"img": "icons/portrait/troll.png", "tokenImg": "icons/token/troll.png"}},
        ]),
    )
    result = asyncio.run(ensure_monster_actor(f, "Troll"))
    assert result == "Actor.placeholder"
    # Second _send call must be the placeholder create with the fetched art.
    create_call = f._send.await_args_list[1]
    assert create_call.args[0] == "create"
    data = create_call.kwargs["data"]
    assert data["img"] == "icons/portrait/troll.png"
    assert data["prototypeToken"]["texture"]["src"] == "icons/token/troll.png"
    # Art was found, so no portrait-generation flag.
    assert "needs_portrait" not in data["flags"]["ai-gm"]


def test_placeholder_without_any_art_flags_needs_portrait():
    """World lookup empty, search empty, no art -> flagged placeholder."""
    f = make_foundry()
    f._send = AsyncMock(side_effect=[
        {"results": []},          # search: nothing
        {"data": {"uuid": "Actor.ph2"}},  # create placeholder
    ])
    assert asyncio.run(ensure_monster_actor(f, "Mystery Creature")) == "Actor.ph2"
    create_call = f._send.await_args_list[1]
    data = create_call.kwargs["data"]
    assert data["name"] == "Mystery Creature"
    assert data["type"] == "npc"
    assert data["system"]["attributes"]["hp"] == {"value": 10, "max": 10, "formula": ""}
    assert "img" not in data
    flags = data["flags"]["ai-gm"]
    assert flags["needs_portrait"] is True
    assert flags["auto_placeholder"] is True
    assert flags["encounter_monster"] is True


def test_placeholder_carries_cr_in_biography():
    f = make_foundry()
    f._send = AsyncMock(side_effect=[
        {"results": []},
        {"data": {"_id": "abc123"}},  # _id fallback when uuid missing
    ])
    assert asyncio.run(ensure_monster_actor(f, "Skeleton", cr=0.25)) == "abc123"
    data = f._send.await_args_list[1].kwargs["data"]
    assert "CR 0.25" in data["system"]["details"]["biography"]["value"]
    assert data["system"]["details"]["cr"] == 0.25


def test_search_results_dict_shape_unwrapped():
    """Relay sometimes nests search results under a dict; that must unwrap too."""
    f = make_foundry(
        _send=AsyncMock(side_effect=[
            {"data": {"results": [
                {"name": "Wolf", "uuid": "Compendium.actors.wolf", "documentType": "Actor",
                 "package": "dnd5e"},
            ]}},
            {"data": {"uuid": "Actor.wolf"}},
        ]),
        execute_js=AsyncMock(return_value={"result": {"error": "nope"}}),
    )
    # search hits (dict shape), import fails, art fetch also fails -> placeholder
    f.execute_js = AsyncMock(side_effect=[RuntimeError("x"), RuntimeError("y")])
    assert asyncio.run(ensure_monster_actor(f, "Wolf")) == "Actor.wolf"


def test_full_outage_returns_none():
    """Everything fails (world lookup, search, create) -> None, no raise."""
    f = make_foundry()
    f.get_actors = AsyncMock(side_effect=RuntimeError("down"))
    f._send = AsyncMock(side_effect=RuntimeError("down"))
    f.execute_js = AsyncMock(side_effect=RuntimeError("down"))
    assert asyncio.run(ensure_monster_actor(f, "Anything")) is None
