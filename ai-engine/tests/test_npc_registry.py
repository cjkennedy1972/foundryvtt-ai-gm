"""Tests for npc.registry — NPC records, relationships, and prompt-context
rendering."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from npc.registry import NPCRegistry


def test_register_and_get():
    reg = NPCRegistry()
    rec = reg.register_npc("n1", "Mara", "A knight", class_name="Fighter", level=5, alignment="LG")
    assert reg.get_npc("n1") is rec
    assert reg.get_npc("missing") is None


def test_get_by_name_case_insensitive():
    reg = NPCRegistry()
    reg.register_npc("n1", "Mara", "A knight")
    assert reg.get_npc_by_name("mara").npc_id == "n1"
    assert reg.get_npc_by_name("nobody") is None


def test_set_personality_requires_existing_npc():
    reg = NPCRegistry()
    assert reg.set_npc_personality("missing", {"temperament": ["calm"]}) is False
    reg.register_npc("n1", "Mara", "A knight")
    assert reg.set_npc_personality("n1", {"temperament": ["calm"]}) is True
    assert reg.get_npc("n1").personality == {"temperament": ["calm"]}


def test_add_and_get_relationship_updates_record():
    reg = NPCRegistry()
    reg.register_npc("n1", "Mara", "A knight")
    reg.add_relationship("n1", "n2", "Kael", "ally", strength=0.8)
    rel = reg.get_relationship("n1", "n2")
    assert rel.relationship_type == "ally"
    assert reg.get_npc_relationships("n1")["n2"] is rel
    assert reg.get_npc_relationships("missing") == {}


def test_update_relationship_clamps_strength():
    reg = NPCRegistry()
    reg.register_npc("n1", "Mara", "A knight")
    reg.add_relationship("n1", "n2", "Kael", "ally", strength=0.9)
    assert reg.update_relationship("n1", "n2", 0.5, "saved his life") is True
    assert reg.get_relationship("n1", "n2").strength == 1.0  # clamped
    assert reg.get_relationship("n1", "n2").last_interaction == "saved his life"
    # Unknown relationship can't be updated
    assert reg.update_relationship("n1", "zzz", 0.1) is False


def test_context_renders_fields_and_strong_relationships():
    reg = NPCRegistry()
    reg.register_npc("n1", "Mara", "A knight", appearance="scarred", class_name="Fighter", level=5, alignment="LG")
    reg.set_npc_personality("n1", {"temperament": ["calm"]})
    reg.add_relationship("n1", "n2", "Kael", "ally", strength=0.9)  # strong -> shown
    reg.add_relationship("n1", "n3", "Rook", "rival", strength=0.3)  # weak -> hidden
    ctx = reg.get_npc_context("n1")
    assert "Mara" in ctx and "Fighter (Level 5)" in ctx and "LG" in ctx
    assert "scarred" in ctx and "calm" in ctx
    assert "strongly ally with Kael" in ctx
    assert "Rook" not in ctx


def test_context_empty_for_unknown():
    assert NPCRegistry().get_npc_context("nope") == ""


def test_list_and_clear():
    reg = NPCRegistry()
    reg.register_npc("n1", "Mara", "x")
    reg.register_npc("n2", "Kael", "y")
    assert len(reg.list_npcs()) == 2
    reg.clear()
    assert reg.list_npcs() == []
