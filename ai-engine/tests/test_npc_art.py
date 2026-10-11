import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from campaign.art import is_generic_art
from campaign.orchestrator import CampaignOrchestrator


def run(c):
    return asyncio.run(c)


@pytest.mark.parametrize("path,generic", [
    ("", True), (None, True), ("   ", True),
    ("icons/svg/mystery-man.svg", True), ("icons/svg/item-bag.svg", True),
    ("systems/dnd5e/icons/svg/mystery-man.png", True),
    ("ddb-images/other/monster/avatar/humanoid/2014-mm-humanoid.jpeg", True),
    ("tokenizer/npc-images/humanoid-2014-mmworld.ddb-x.webp?v=1", True),
    ("systems/dnd5e/icons/classes/knight.webp", True),
    ("ai-gm-portraits/camp/elara.png", False), ("worlds/umbraeth/art/gorn.webp", False),
])
def test_generic_art_is_recognised(path, generic):
    assert is_generic_art(path) is generic


def foundry(compendium=None, art_missing=None):
    """A Foundry stand-in. compendium: the art a compendium lookup returns (None for no match).
    art_missing: the names the art check reports."""
    f = MagicMock()
    f.create_entity = AsyncMock(return_value={"data": {"uuid": "Actor.new"}})

    async def execute_js(code, **kw):
        if "game.packs" in code:
            return {"result": compendium}
        if "generic(" in code:
            return {"result": art_missing or []}
        return {"result": True}

    f.execute_js = AsyncMock(side_effect=execute_js)
    return f


def deployment():
    return {"npcs": [], "encounter_actors": []}


def created_data(f):
    return f.create_entity.await_args.args[1]


def test_an_npc_with_a_campaign_portrait_keeps_it_and_skips_the_compendium():
    f, d = foundry(compendium={"img": "icons/x.png", "tokenImg": "icons/x.png"}), deployment()
    run(CampaignOrchestrator()._deploy_npcs({"npcs": [{"name": "Elara", "portrait_src": "ai-gm-portraits/e.png"}]}, f, d, {}))

    data = created_data(f)
    assert data["img"] == "ai-gm-portraits/e.png"
    assert data["prototypeToken"]["texture"]["src"] == "ai-gm-portraits/e.png"
    assert not any("game.packs" in c.args[0] for c in f.execute_js.await_args_list)


def test_an_npc_without_a_portrait_takes_usable_compendium_art():
    f, d = foundry(compendium={"uuid": "Actor.c", "img": "worlds/bestiary/thorne.webp", "tokenImg": "worlds/bestiary/thorne-token.webp"}), deployment()
    run(CampaignOrchestrator()._deploy_npcs({"npcs": [{"name": "Captain Thorne"}]}, f, d, {}))

    data = created_data(f)
    assert data["img"] == "worlds/bestiary/thorne.webp"
    assert data["prototypeToken"]["texture"]["src"] == "worlds/bestiary/thorne-token.webp"
    assert "needs_portrait" not in data["flags"].get("ai-gm", {})


def test_compendium_art_that_is_only_an_icon_is_not_used():
    f, d = foundry(compendium={"uuid": "Actor.c", "img": "icons/svg/mystery-man.svg", "tokenImg": "icons/svg/mystery-man.svg"}), deployment()
    run(CampaignOrchestrator()._deploy_npcs({"npcs": [{"name": "Captain Thorne"}]}, f, d, {}))

    data = created_data(f)
    assert "img" not in data
    assert data["flags"]["ai-gm"]["needs_portrait"] is True


def test_an_npc_with_no_art_anywhere_is_flagged_for_generation():
    f, d = foundry(compendium=None), deployment()
    run(CampaignOrchestrator()._deploy_npcs({"npcs": [{"name": "Nobody"}]}, f, d, {}))

    assert created_data(f)["flags"]["ai-gm"]["needs_portrait"] is True


def test_a_failed_compendium_lookup_does_not_stop_the_npc():
    f, d = foundry(), deployment()
    f.execute_js = AsyncMock(side_effect=RuntimeError("page gone"))
    run(CampaignOrchestrator()._deploy_npcs({"npcs": [{"name": "Nobody"}]}, f, d, {}))

    assert d["npcs"][0]["status"] == "created"
    assert created_data(f)["flags"]["ai-gm"]["needs_portrait"] is True


def test_the_deploy_reports_npcs_still_on_generic_art():
    o = CampaignOrchestrator()
    o._generate_placeholder_portraits = AsyncMock(return_value={"generated": 0})
    f = foundry(compendium=None, art_missing=["Nobody"])
    out = run(o.deploy_to_foundry({"campaign": {"name": "C"}, "npcs": [{"name": "Nobody"}]}, f, {}, {}))

    assert out["art_missing"] == ["Nobody"]


def test_the_report_is_none_when_the_check_fails():
    o = CampaignOrchestrator()
    o._generate_placeholder_portraits = AsyncMock(return_value={"generated": 0})
    f = foundry(compendium=None)

    async def broken(code, **kw):
        if "generic(" in code:
            raise RuntimeError("page gone")
        return {"result": None}

    f.execute_js = AsyncMock(side_effect=broken)
    out = run(o.deploy_to_foundry({"campaign": {"name": "C"}, "npcs": [{"name": "Nobody"}]}, f, {}, {}))

    assert out["art_missing"] is None


def test_a_compendium_stock_avatar_is_not_used_as_the_npc_portrait():
    # The source book's Knight and Mage carry the shared humanoid avatar; that is not their portrait.
    stock = "ddb-images/other/monster/avatar/humanoid/2014-mm-humanoid.jpeg"
    f, d = foundry(compendium={"uuid": "Actor.c", "img": stock, "tokenImg": stock}), deployment()
    run(CampaignOrchestrator()._deploy_npcs({"npcs": [{"name": "Knight"}]}, f, d, {}))

    data = created_data(f)
    assert "img" not in data
    assert data["flags"]["ai-gm"]["needs_portrait"] is True


def test_npcs_created_on_generic_art_are_flagged_for_generation_but_linked_ones_are_not():
    o = CampaignOrchestrator()
    o._generate_placeholder_portraits = AsyncMock(return_value={"generated": 0})
    f = foundry(compendium=None)
    run(o.deploy_to_foundry({"campaign": {"name": "C"}, "npcs": [{"name": "Nobody"}, {"name": "Old", "existing_uuid": "Actor.old"}]}, f, {}, {}))

    flag_calls = [c.args[0] for c in f.execute_js.await_args_list if "needs_portrait': true" in c.args[0]]
    assert len(flag_calls) == 1
    assert '"Nobody"' in flag_calls[0] and '"Old"' not in flag_calls[0]
