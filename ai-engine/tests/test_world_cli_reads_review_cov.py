"""Behavioural coverage for the World CLI read adapters the main suite leaves untested."""
import pytest

from foundry import world_cli_reads as reads
from tests.test_world_cli_reads import FakeCLI, SCENES


@pytest.mark.asyncio
async def test_scene_id_is_exact_name_only_and_none_when_absent():
    cli = FakeCLI(**{"scene.list": {"scenes": [{"id": "s1", "name": "Hall Annex"}, {"id": "s2", "name": "Hall"}], "hasMore": False}})
    assert await reads.scene_id(cli, "Hall") == "s2"
    assert await reads.scene_id(cli, "hall") is None


@pytest.mark.asyncio
async def test_scene_details_is_none_for_an_unknown_scene():
    assert await reads.scene_details(FakeCLI(**{"scene.list": SCENES}), "Nowhere") is None


@pytest.mark.asyncio
async def test_canvas_documents_is_none_when_no_scene_is_active():
    cli = FakeCLI(**{"scene.list": {"scenes": [{"id": "s1", "name": "Hall", "active": False}], "hasMore": False}})
    assert await reads.canvas_documents(cli, "walls") is None


@pytest.mark.asyncio
async def test_active_combat_id_picks_the_active_one():
    rows = {"combats": [{"id": "c1", "active": False}, {"id": "c2", "active": True}], "hasMore": False}
    assert await reads.active_combat_id(FakeCLI(**{"combat.list": rows})) == "c2"
    assert await reads.active_combat_id(FakeCLI(**{"combat.list": {"combats": [{"id": "c1"}], "hasMore": False}})) is None


@pytest.mark.asyncio
async def test_playlists_are_batched_by_50_and_default_missing_fields():
    ids = [{"id": f"p{i}"} for i in range(60)]
    seen = []

    def many(params):
        seen.append(len(params["ids"]))
        return {"playlists": [{"id": i, "name": i, "sounds": [{"id": "s", "name": "Rain", "path": "r.mp3"}]} for i in params["ids"]]}

    cli = FakeCLI(**{"playlist.list": {"playlists": ids, "hasMore": False}, "playlist.get-many": many})
    out = await reads.playlists(cli)
    assert seen == [50, 10] and len(out) == 60
    assert out[0]["playing"] is False and out[0]["description"] == ""
    assert out[0]["sounds"] == [{"id": "s", "name": "Rain", "path": "r.mp3", "playing": False, "repeat": False, "volume": None}]


@pytest.mark.asyncio
async def test_scene_match_falls_through_exact_case_the_prefix_then_containment():
    cli = FakeCLI(**{"scene.list": {"scenes": [{"id": "a", "name": "The Rusty Dagger"}, {"id": "b", "name": "Crypt of Doom"}], "hasMore": False}})
    assert await reads.scene_match(cli, "The Rusty Dagger") == {"id": "a", "name": "The Rusty Dagger"}
    assert (await reads.scene_match(cli, "CRYPT OF DOOM"))["id"] == "b"
    assert (await reads.scene_match(cli, "Rusty Dagger"))["id"] == "a"      # leading "The" ignored
    assert (await reads.scene_match(cli, "the crypt"))["id"] == "b"          # containment
    assert await reads.scene_match(cli, "Dragon Lair") is None


def _pc_cli(users, on_scene):
    actors = {"h1": {"actor": {"name": "Hero", "prototypeToken": {"_id": "x", "actorId": "h1", "texture": {"src": "h.png"}, "disposition": 1}}},
              "h2": {"actor": {"name": "Mage", "prototypeToken": {}}}}
    return FakeCLI(**{
        "user.list": {"users": users, "hasMore": False},
        "scene.get": {"scene": {"width": 1000, "height": 600, "grid": {"size": 50}}},
        "scene.token.list": {"tokens": [{"actorId": a} for a in on_scene], "hasMore": False},
        "actor.get": lambda p: actors[p["actorId"]],
    })


@pytest.mark.asyncio
async def test_pc_token_data_lays_missing_pcs_in_a_row_from_the_centre():
    users = [{"id": "gm", "isGM": True, "character": "g1"}, {"id": "u1", "character": {"id": "h1"}},
             {"id": "u2", "character": "h2"}, {"id": "u3", "character": "h2"}, {"id": "u4", "character": None}]
    out = await reads.pc_token_data(_pc_cli(users, on_scene=[]), "s1")
    assert [(t["name"], t["actorId"], t["x"], t["y"]) for t in out] == [("Hero", "h1", 500, 300), ("Mage", "h2", 550, 300)]
    assert out[0]["hidden"] is False and out[0]["disposition"] == 1 and out[0]["texture"] == {"src": "h.png"}
    assert "_id" not in out[0]


@pytest.mark.asyncio
async def test_pc_token_data_skips_pcs_already_on_the_scene_and_gm_characters():
    users = [{"id": "gm", "isGM": True, "character": "h1"}, {"id": "u2", "character": "h2"}]
    assert await reads.pc_token_data(_pc_cli(users, on_scene=["h2"]), "s1") == []


@pytest.mark.asyncio
async def test_token_rows_and_actor_image_prefers_prototype_art_over_portrait():
    cli = FakeCLI(**{"scene.token.list": {"tokens": [{"id": "t1"}], "hasMore": False},
                     "actor.get": lambda p: {"actor": {"img": "portrait.png", "prototypeToken": {"texture": {"src": "tok.png"}}}} if p["actorId"] == "a" else {"actor": {"img": "portrait.png"}}})
    assert await reads.token_rows(cli, "s1") == [{"id": "t1"}]
    assert await reads.actor_image(cli, "a") == "tok.png"
    assert await reads.actor_image(cli, "b") == "portrait.png"


@pytest.mark.asyncio
async def test_actor_dispositions_match_by_overlap_and_use_minus_one_when_unset():
    listing = {"actors": [{"id": "1", "name": "Goblin Boss"}, {"id": "2", "name": "Elf"}, {"id": "3", "name": "Cat"}, {"id": "4"}], "hasMore": False}
    full = {"1": {"name": "Goblin Boss", "prototypeToken": {"disposition": -1}}, "2": {"name": "Elf", "prototypeToken": {}}}
    cli = FakeCLI(**{"actor.list": listing, "actor.get-many": lambda p: {"actors": [full[i] for i in p["ids"]]}})
    out = await reads.actor_dispositions(cli, ["goblin", "ELF"])
    assert out == {"Goblin Boss": -1, "Elf": -1}
    asked = [c for c in cli.calls if c[0] == "actor.get-many"][0][1]["ids"]
    assert asked == ["1", "2"]                      # Cat and the unnamed actor are never fetched


@pytest.mark.asyncio
async def test_effect_for_status_returns_the_first_effect_carrying_the_status():
    rows = {"effects": [{"id": "e1", "statuses": ["prone"]}, {"id": "e2"}, {"id": "e3", "statuses": ["poisoned", "x"]}], "hasMore": False}
    cli = FakeCLI(**{"actor.effect.list": rows})
    assert await reads.effect_for_status(cli, "a", "poisoned") == "e3"
    assert await reads.effect_for_status(cli, "a", "blinded") is None
