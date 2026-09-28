"""Enriching an existing campaign from a new source: merge rules, delta parsing,
live reload, and the whole flow against a temporary vault with a stub LLM."""

import asyncio
import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from campaign.enrichment import (
    append_section, drop_conflicting_lines, enrich_entity, load_sources, merge_entities, parse_delta_response, source_id,
)
from campaign.orchestrator import CampaignOrchestrator
from context.loader import CampaignLoader


# ─── sources ──────────────────────────────────────────────────────────────


def test_load_sources_reads_md_and_txt_and_skips_the_rest(tmp_path):
    (tmp_path / "Krynn Wiki.md").write_text("The Cataclysm.\n\nThe Kingpriest fell.")
    (tmp_path / "notes.txt").write_text("Solamnia is a knightly realm.")
    (tmp_path / "empty.md").write_text("")
    (tmp_path / ".hidden.md").write_text("secret")
    (tmp_path / "map.png").write_bytes(b"png")

    sources = load_sources(str(tmp_path))

    assert sorted(s["title"] for s in sources) == ["Krynn Wiki", "notes"]
    assert {s["id"] for s in sources} == {"krynn wiki", "notes"}
    assert all(s["pages"] for s in sources)


def test_source_id_ignores_extension_and_case():
    assert source_id("Tales of the Lance.PDF") == source_id("tales of the lance.md")


# ─── entity merge: existing wins ──────────────────────────────────────────


def test_enrich_entity_fills_gaps_unions_lists_and_keeps_existing_values():
    existing = {"name": "Kansaldi", "role": "boss", "alignment": "lawful evil", "personality": "", "goals": ["rule"]}
    incoming = {"name": "Kansaldi", "role": "Sorcerer", "alignment": "lawful good", "personality": "Proud",
                "goals": ["rule", "survive"], "portrait_file": "x.png"}

    merged, conflicts = enrich_entity(existing, incoming, "wiki")

    assert merged["personality"] == "Proud"                 # gap filled
    assert merged["goals"] == ["rule", "survive"]           # union, order kept
    assert merged["alignment"] == "lawful evil"             # existing wins ...
    assert conflicts == [{"entity": "Kansaldi", "field": "alignment",
                          "existing": "lawful evil", "claim": "lawful good"}]   # ... and is flagged
    assert merged["role"] == "boss"                         # a different job title is no conflict
    assert merged["sources"] == ["wiki"]
    assert "portrait_file" not in merged                    # bookkeeping is not lore
    assert existing["personality"] == "" and "sources" not in existing   # inputs untouched


def test_a_different_description_is_kept_as_a_source_note_not_a_conflict():
    merged, conflicts = enrich_entity(
        {"name": "Haven", "description": "A trade town on the river"},
        {"name": "Haven", "description": "Refugees crowd its walls"}, "wiki")
    assert conflicts == []
    assert merged["description"] == "A trade town on the river"
    assert merged["source_notes"] == ["description: Refugees crowd its walls (from wiki)"]


def test_alignment_conflicts_only_when_the_two_actually_oppose():
    def clash(have, claim):
        return bool(enrich_entity({"name": "X", "alignment": have}, {"name": "X", "alignment": claim}, "s")[1])

    assert not clash("LE", "Evil") and not clash("LG", "Good") and not clash("lawful evil", "LE")
    assert clash("LE", "Good") and clash("lawful evil", "chaotic evil") and clash("LG", "CE")


def test_a_fuller_description_of_the_same_fact_is_not_a_conflict():
    _, conflicts = enrich_entity(
        {"name": "Haven", "description": "A trade town"},
        {"name": "Haven", "description": "A trade town on the river"}, "wiki")
    assert conflicts == []


def test_merge_entities_adds_enriches_and_matches_variants():
    existing = [{"name": "The Solamnic Knights", "goals": ["honor"]}, {"name": "Kansaldi Fire-Eyes"}]
    incoming = [
        {"name": "Solamnic Knights", "goals": ["duty"]},        # same after normalising
        {"name": "Kansaldi", "role": "Sorcerer"},               # variant, via alias
        {"name": "Knights of Neraka", "goals": ["conquest"]},   # new
    ]

    items, stats, conflicts = merge_entities(existing, incoming, "wiki", aliases={"Kansaldi": "Kansaldi Fire-Eyes"})

    assert [i["name"] for i in items] == ["The Solamnic Knights", "Kansaldi Fire-Eyes", "Knights of Neraka"]
    assert items[0]["goals"] == ["honor", "duty"]
    assert items[1]["role"] == "Sorcerer"
    assert items[2]["sources"] == ["wiki"]
    assert stats == {"added": 1, "enriched": 2} and conflicts == []


# ─── world / history delta ────────────────────────────────────────────────


def test_delta_parses_additions_and_conflicts_and_append_keeps_existing_text():
    reply = ("===WORLD===\nThe Blood Sea is a maelstrom.\n===HISTORY===\nNone\n"
             "===CONFLICTS===\n- Paladine is dead | Paladine is the god of good\n===END===")

    world, history, conflicts = parse_delta_response(reply)

    assert world == "The Blood Sea is a maelstrom." and history == ""
    assert conflicts == [{"claim": "Paladine is dead", "existing": "Paladine is the god of good"}]

    existing = "# Worldbuilding\n\nKrynn is a world.\n"
    combined = append_section(existing, world, "Atlas")
    assert combined.startswith(existing)
    assert combined.endswith("## From Atlas\n\nThe Blood Sea is a maelstrom.\n")
    assert append_section(existing, history, "Atlas") == existing      # nothing to add


def test_lines_carrying_a_flagged_claim_are_not_appended():
    conflicts = [{"claim": "Dargaard Keep is in the realm of Sancrist", "existing": "It is in Nightlund"},
                 {"claim": "Kansaldi is a mild-mannered healer who serves the temple of Mishakal", "existing": ""}]
    added = ("- **Gilean** — God of neutrality; keeper of the Tome of the Balance.\n"
             "- **Dargaard Keep** — Haunted fortress in the realm of Sancrist; guarded by undead knights and spectres.\n"
             "- **Temple of Mishakal** — Temple where Kansaldi serves.\n"
             "- **Paladine** — Platinum Dragon, god of good.")

    kept = drop_conflicting_lines(added, conflicts)

    assert kept.splitlines() == ["- **Gilean** — God of neutrality; keeper of the Tome of the Balance.",
                                 "- **Paladine** — Platinum Dragon, god of good."]
    assert drop_conflicting_lines(added, []) == added          # nothing flagged, nothing dropped


def test_delta_without_markers_adds_nothing():
    assert parse_delta_response("Sure! Here is some lore.") == ("", "", [])


# ─── loader ───────────────────────────────────────────────────────────────


def _vault(tmp_path, name="Test Camp"):
    from campaign.obsidian_sync import get_campaign_folder
    vault = tmp_path / "vault"
    folder = get_campaign_folder(vault, name)
    folder.mkdir(parents=True)
    return vault, folder


def test_loader_reload_sees_new_lore_and_prefers_the_root_worldbuilding_note(tmp_path):
    vault, folder = _vault(tmp_path)
    (folder / "Worldbuilding.md").write_text("ROOT world lore")
    loader = CampaignLoader(vault_path=str(vault))
    asyncio.run(loader.load("Test Camp"))

    (folder / "Lore").mkdir()
    (folder / "Lore" / "A World Apart.md").write_text("A shadowing note")   # sorts before the root file
    assert "Lore/A World Apart" not in loader._data                         # cached until reload

    asyncio.run(loader.reload())

    assert "Lore/A World Apart" in loader._data
    assert "ROOT world lore" in loader.get_world_context_sync()
    assert "shadowing" not in loader.get_world_context_sync()


# ─── whole flow ───────────────────────────────────────────────────────────


def _stub_llm():
    """Answers each kind of call the enrichment makes, keyed on its system prompt."""
    def reply(system):
        if "extending the world lore" in system:
            return ("===WORLD===\nThe Blood Sea of Istar is a maelstrom.\n===HISTORY===\nNone\n"
                    "===CONFLICTS===\nKansaldi is a cleric | Kansaldi is a sorcerer\n===END===")
        if "Extract the lore entities" in system:
            return json.dumps({"npcs": [{"name": "Kansaldi", "role": "Cleric", "personality": "Proud", "alignment": "lawful good"},
                                        {"name": "Lord Soth", "role": "Death Knight"}],
                               "locations": [], "factions": [], "artifacts": []})
        if "matching newly-generated" in system:
            return "{}"
        return "## World/History\n- The Blood Sea of Istar is a maelstrom."

    async def post(url, headers=None, json=None, timeout=None):
        resp = MagicMock(status_code=200, text="")
        resp.json.return_value = {"choices": [{"message": {"content": reply(json["messages"][0]["content"])}}]}
        return resp

    client = MagicMock()
    client.post = AsyncMock(side_effect=post)
    return client


def test_enrich_campaign_end_to_end(tmp_path):
    vault, folder = _vault(tmp_path)
    original = {"campaign": {"name": "Test Camp"},
                "npcs": [{"name": "Kansaldi", "role": "Sorcerer", "alignment": "lawful evil"}],
                "locations": [], "factions": [], "artifacts": [], "scenes": [{"name": "Untouched Scene"}]}
    (folder / "campaign.json").write_text(json.dumps(original))
    (folder / "Worldbuilding.md").write_text("# Worldbuilding\n\nKrynn is a world.\n")
    src = tmp_path / "src"
    src.mkdir()
    (src / "Atlas of Krynn.md").write_text("The Blood Sea of Istar is a maelstrom. Lord Soth rules Dargaard Keep.")
    conflicts = []

    async def sink(claim, rationale, existing):
        conflicts.append((claim, rationale, existing))

    def run(force=False):
        return asyncio.run(CampaignOrchestrator().enrich_campaign(
            "Test Camp", _stub_llm(), source_path=str(src), vault_path=str(vault),
            force=force, conflict_sink=sink))

    result = run()

    assert result["status"] == "ok" and [s["id"] for s in result["sources"]] == ["atlas of krynn"]
    data = json.loads((folder / "campaign.json").read_text())
    kansaldi = next(n for n in data["npcs"] if n["name"] == "Kansaldi")
    assert kansaldi["role"] == "Sorcerer" and kansaldi["personality"] == "Proud"    # existing wins, gap filled
    assert next(n for n in data["npcs"] if n["name"] == "Lord Soth")["sources"] == ["atlas of krynn"]
    assert data["scenes"] == original["scenes"]                                      # not lore, untouched
    assert data["sources"][0]["id"] == "atlas of krynn"
    world = (folder / "Worldbuilding.md").read_text()
    assert world.startswith("# Worldbuilding\n\nKrynn is a world.\n")
    assert "## From Atlas of Krynn" in world and "Blood Sea of Istar" in world
    assert not (folder / "History.md").exists()                                     # nothing to add
    assert (folder / "Lore" / "Sources" / "atlas of krynn" / "01.md").exists()
    assert (folder / "NPCs").is_dir() and any((folder / "NPCs").iterdir())
    assert len(conflicts) == 2 and any("Kansaldi is a cleric" == c[0] for c in conflicts)   # world claim
    assert any(c[2] == "lawful evil" for c in conflicts)                                     # alignment clash

    again = run()                                     # same source again: nothing happens
    assert again["skipped"] == ["atlas of krynn"] and again["sources"] == []
    assert len(json.loads((folder / "campaign.json").read_text())["npcs"]) == 2

    assert run(force=True)["sources"]                 # force redoes it, without duplicating entities
    assert len(json.loads((folder / "campaign.json").read_text())["npcs"]) == 2


def test_enrich_campaign_reports_a_missing_campaign_and_an_empty_source(tmp_path):
    vault, folder = _vault(tmp_path)
    orch = CampaignOrchestrator()
    missing = asyncio.run(orch.enrich_campaign("Nope", MagicMock(), source_path=str(tmp_path), vault_path=str(vault)))
    assert missing["status"] == "error" and "not found" in missing["error"]

    (folder / "campaign.json").write_text(json.dumps({"campaign": {"name": "Test Camp"}}))
    empty = asyncio.run(orch.enrich_campaign("Test Camp", MagicMock(), source_path=str(tmp_path / "vault"), vault_path=str(vault)))
    assert empty["status"] == "error" and "No readable sources" in empty["error"]
