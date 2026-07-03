"""Checks for foundry/scripts.py — named execute_js snippet builders (Phase 5)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from foundry import scripts


def test_get_active_modules_reads_game_modules_directly():
    js = scripts.get_active_modules()
    assert "game.modules.values()" in js
    assert "m.active" in js


def test_sync_combat_combatants_embeds_token_ids_in_order():
    js = scripts.sync_combat_combatants(["tok1", "tok2", "tok3"])
    assert '["tok1", "tok2", "tok3"]' in js or '["tok1","tok2","tok3"]' in js
    assert "Combat.create" in js
    assert "createEmbeddedDocuments('Combatant'" in js
    assert "deleteEmbeddedDocuments('Combatant'" in js
    # Initiative assigned by position so Foundry's own sort matches token_ids order
    assert "n - i" in js


def test_set_combat_turn_embeds_round_and_turn_as_ints():
    js = scripts.set_combat_turn(3, 1)
    assert "round: 3" in js
    assert "turn: 1" in js


def test_set_combat_turn_coerces_non_int_input():
    # Defensive: callers pass real ints, but int() coercion prevents JS
    # injection if that ever changes.
    js = scripts.set_combat_turn(3.0, 1.0)
    assert "round: 3" in js
    assert "turn: 1" in js


def test_end_combat_uses_delete_not_end_combat_method():
    js = scripts.end_combat()
    # combat.endCombat() opens a confirmation dialog that hangs headless
    # sessions (live-verified) — must use .delete() instead.
    assert "combat.delete()" in js
    assert "endCombat" not in js


def test_find_actors_needing_portraits_checks_both_flags():
    js = scripts.find_actors_needing_portraits()
    assert "needs_portrait" in js
    assert "auto_placeholder" in js
    assert "mystery-man" in js


def test_count_scene_placeables_escapes_scene_name():
    js = scripts.count_scene_placeables("The Crypt's Depths")
    assert '"The Crypt\'s Depths"' in js
    assert "s.walls.size" in js and "s.lights.size" in js and "s.sounds.size" in js


def test_teardown_by_flag_covers_all_collections():
    js = scripts.teardown_by_flag()
    for label in ["actors", "journal", "tables", "playlists", "scenes"]:
        assert f'"{label}"' in js


def test_teardown_by_uuid_map_embeds_json_payload():
    js = scripts.teardown_by_uuid_map({"Actor": ["Actor.abc123"], "Scene": ["Scene.xyz789"]})
    assert '"Actor.abc123"' in js
    assert '"Scene.xyz789"' in js
    assert "documentClass.deleteDocuments" in js


def test_get_active_effects_embeds_uuid():
    js = scripts.get_active_effects("Actor.beringar123")
    assert "fromUuid('Actor.beringar123')" in js
    assert "e.disabled" in js


def test_get_initiative_order_reads_combat_turns():
    js = scripts.get_initiative_order()
    assert "game.combat" in js
    assert "t.token?.id" in js
