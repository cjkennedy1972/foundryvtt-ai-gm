"""A turn's context scales with the scene and the conversation, not the campaign.

Every actor in the world (~10k tokens for 154 NPCs) and every map (~1k for 174)
went out on every turn, and the map's tokens were listed twice. A turn now
carries the characters in play and the maps within reach, held to a budget.

Run:
    cd ai-engine && python -m pytest tests/test_turn_context.py -v
"""

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

from config import settings
from foundry.turn_context import Block, characters_in_play, fit_blocks, is_named, maps_in_reach
from utils.token_counter import estimate_tokens


# ─── who is in play ───────────────────────────────────────────────────────

def test_a_first_name_names_a_character_but_a_title_does_not():
    assert is_named("Kansaldi Fire-Eyes", "we ride after kansaldi at dawn")
    assert is_named("Lord Soth", "lord soth rises")
    assert not is_named("Lord Soth", "the lord of the keep")
    assert not is_named("Mira", "a mirage shimmers")          # whole words only


def test_characters_in_play_are_players_the_map_and_the_named():
    actors = [{"name": f"NPC {i}", "uuid": f"Actor.n{i}"} for i in range(150)]
    actors += [{"name": "Kansaldi Fire-Eyes", "uuid": "Actor.kan"},
               {"name": "Aria", "uuid": "Actor.pc1"}]
    tokens = [{"id": "t1", "name": "NPC 3", "actorUuid": "n3"},        # bare id, as Foundry keys it
              {"id": "t2", "name": "Wolf"}]                             # no actor for it
    in_play = characters_in_play(actors, tokens, {"Aria": "user1"}, "Where is Kansaldi hiding?")
    names = [(a.get("name"), t and t["id"]) for a, t in in_play]
    assert names == [("NPC 3", "t1"), ("Kansaldi Fire-Eyes", None), ("Aria", None), (None, "t2")]


# ─── maps within reach ────────────────────────────────────────────────────

SCENES = [
    {"name": "Village Circle", "foundry_scene_name": "Map 3.1: Vogler", "source_chapter": "Ch3", "act": 1},
    {"name": "Burning Farm", "foundry_scene_name": "Map 3.2: Farmstead", "source_chapter": "Ch3", "act": 1},
    {"name": "Kalaman Gate", "foundry_scene_name": "Map 5.1: Kalaman", "source_chapter": "Ch5", "act": 2},
    {"name": "Harbour", "foundry_scene_name": "Map 5.2: Harbour", "source_chapter": "Ch5", "act": 2},
]
ALL = [s["foundry_scene_name"] for s in SCENES] + [f"Map 9.{i}: Elsewhere {i}" for i in range(170)]


def test_maps_in_reach_are_named_ones_then_the_chapter():
    near, more = maps_in_reach("Map 3.1: Vogler", ALL, SCENES, "we should head to kalaman")
    assert near == ["Map 5.1: Kalaman", "Map 3.2: Farmstead"]
    assert more == len(ALL) - 1 - 2


def test_a_campaign_scene_name_finds_its_foundry_map():
    near, _ = maps_in_reach("Map 3.1: Vogler", ALL, SCENES, "back to the burning farm")
    assert near[0] == "Map 3.2: Farmstead"


def test_a_small_improvised_world_lists_its_maps():
    near, more = maps_in_reach("Cave", ["Cave", "Forest", "Tower"], [], "")
    assert near == ["Forest", "Tower"] and more == 0


# ─── the budget ───────────────────────────────────────────────────────────

def test_the_budget_drops_the_lowest_priority_first_and_never_the_essentials(caplog):
    big = "word " * 400                                        # ~500 tokens
    blocks = [Block(1, "location", big), Block(4, "maps", big), Block(2, "memory", big),
              Block(3, "lore", big), Block(1, "characters", big)]
    with caplog.at_level(logging.INFO):
        text = fit_blocks(blocks, budget_tokens=1600)
    assert text.count("word") == 400 * 3                        # location, characters, memory
    assert "dropped: lore" in caplog.text and "maps" in caplog.text
    assert "[Turn] context ~" in caplog.text                   # size logged every turn


def test_essentials_are_kept_even_over_budget(caplog):
    with caplog.at_level(logging.WARNING):
        text = fit_blocks([Block(1, "characters", "word " * 400)], budget_tokens=100)
    assert text and "over the 100 budget" in caplog.text


# ─── one whole turn at Dragonlance scale ──────────────────────────────────

def test_a_turn_in_a_big_campaign_stays_small_and_keeps_who_matters():
    from foundry.chat_listener import ChatListener
    actors = [{"name": f"Soldier {i}", "uuid": f"Actor.s{i}", "hp": 9, "max_hp": 9} for i in range(154)]
    actors += [{"name": "Kansaldi Fire-Eyes", "uuid": "Actor.kan", "hp": 90, "max_hp": 90},
               {"name": "Aria", "uuid": "Actor.pc1", "hp": 30, "max_hp": 30}]
    tokens = [{"id": f"tok{i}", "name": f"Soldier {i}", "actorUuid": f"s{i}", "x": 100 * i, "y": 0,
               "disposition": -1} for i in range(10)]
    foundry = MagicMock()
    foundry.get_actors = AsyncMock(return_value=actors)
    foundry.get_scene_tokens = AsyncMock(return_value=tokens)
    foundry.get_scene_details = AsyncMock(return_value={})
    foundry.list_scene_names = AsyncMock(return_value=ALL)
    loader = MagicMock()
    loader.campaign_scenes = SCENES
    loader.get_scene_briefing.return_value = "Smoke rises over Vogler."
    loader.get_encounter_context_for_scene.return_value = ""
    tracker = MagicMock()
    tracker.state.current_scene = "Map 3.1: Vogler"
    tracker.state.player_actors = {"Aria": "user1"}
    tracker.get_encounter_context.return_value = ""
    listener = ChatListener(foundry=foundry, llm=MagicMock(), dispatcher=MagicMock(), state_tracker=tracker,
                            db=MagicMock(), campaign_loader=loader)
    listener.llm.conversation_history = [{"role": "assistant", "content": "Kansaldi watches from the ridge."}]

    context = asyncio.run(listener._turn_context("I draw my sword"))

    assert "Soldier 3" in context and "tok3" in context        # on the map
    assert "Kansaldi" in context                                # named last exchange
    assert "Aria" in context                                    # a player character
    assert "Soldier 50" not in context                          # nowhere near
    assert "Elsewhere 7" not in context                         # a far-off map
    assert "Map 3.2: Farmstead" in context                      # a neighbouring one
    assert context.count("tok3") == 1                           # tokens listed once
    assert estimate_tokens(context) < 1500, estimate_tokens(context)
    assert estimate_tokens(context) <= settings.turn_context_max_tokens
