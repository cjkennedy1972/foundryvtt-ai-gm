#!/usr/bin/env python3
"""
Regression tests for the v1.0 code-review fixes.

Covers:
  #2 combat turn re-anchoring after mid-round combatant removal
  #4 whisper semantics: player→GM whispers processed, player→player skipped
  #6 hostile auto-placement requires an explicit quantity word

Run:
    cd ai-engine && python -m pytest tests/test_review_fixes_v1.py -v
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from foundry.chat_listener import _mention_count, ChatListener
from combat.loop import CombatLoop


# ---------------------------------------------------------------------------
# #6 — hostile auto-placement must require an explicit quantity word.
# ---------------------------------------------------------------------------

def test_hostile_placement_requires_quantity_word():
    # Bare capitalized mentions (no number word) must NOT count when
    # require_quantity=True — these are the phantom-monster cases.
    assert _mention_count("Shadows cling to the walls", "Shadow", require_quantity=True) == 0
    assert _mention_count("The Revenant's curse lingers", "Revenant", require_quantity=True) == 0
    assert _mention_count("Skeletons are depicted in the fresco", "Skeleton", require_quantity=True) == 0


def test_hostile_placement_still_counts_explicit_quantity():
    assert _mention_count("two towering Revenants lunge", "Revenant", require_quantity=True) == 2
    assert _mention_count("a horde of Skeletons rises", "Skeleton", require_quantity=True) == 6
    assert _mention_count("six Skeletons surround you", "Skeleton", require_quantity=True) == 6


def test_friendly_path_unchanged_without_require_quantity():
    # The default (require_quantity=False) behavior is preserved for non-hostile
    # counting — a bare capitalized plural still reads as a group.
    assert _mention_count("Skeletons claw out of the dust", "Skeleton") == 3
    assert _mention_count("The Revenant raises its blade", "Revenant") == 1


# ---------------------------------------------------------------------------
# #4 — whisper semantics in _is_player_message.
# ---------------------------------------------------------------------------

def _listener_for_whisper(gm_ids):
    # Build a bare ChatListener without running __init__ (which needs many
    # collaborators); _is_player_message only touches _gm_user_ids and the
    # sent-message echo list.
    obj = ChatListener.__new__(ChatListener)
    obj._gm_user_ids = set(gm_ids)
    obj._gm_user_names = set()
    obj._ai_controlled_speakers = {"Sage"}
    obj._sent_messages_with_timestamp = []
    obj._sent_messages_lock = asyncio.Lock()
    return obj


def _msg(content, speaker_alias, whisper=None, author_name="Player1"):
    return {
        "content": content,
        "speaker": {"alias": speaker_alias},
        "whisper": whisper or [],
        "author": {"name": author_name, "id": "user-player-1"},
    }


def test_player_whisper_to_gm_is_processed():
    listener = _listener_for_whisper(gm_ids={"gm-user-1"})
    inner = _msg("I sneak toward the door", "Beringar", whisper=["gm-user-1"])
    assert asyncio.run(listener._is_player_message(inner)) is True


def test_player_whisper_to_another_player_is_skipped():
    listener = _listener_for_whisper(gm_ids={"gm-user-1"})
    inner = _msg("psst, look at this", "Beringar", whisper=["user-player-2"])
    assert asyncio.run(listener._is_player_message(inner)) is False


def test_rest_api_module_whisper_echo_is_skipped():
    listener = _listener_for_whisper(gm_ids={"gm-user-1"})
    inner = _msg("narration echo", "", whisper=["gm-user-1"], author_name="REST API Module")
    assert asyncio.run(listener._is_player_message(inner)) is False


# ---------------------------------------------------------------------------
# #2 — combat turn re-anchoring: exercise the REAL CombatLoop._reanchor_turn_index
# against live _turn_order / token lists, not a hand-rolled copy of the logic.
# ---------------------------------------------------------------------------

def _loop_with(turn_order, pcs, npcs, dead_pcs=(), current_index=0):
    """A bare CombatLoop with just the turn-tracking state populated."""
    loop = CombatLoop.__new__(CombatLoop)
    loop._turn_order = list(turn_order)
    loop._pc_tokens = [{"id": i} for i in pcs]
    loop._npc_tokens = [{"id": i} for i in npcs]
    loop._dead_pc_tokens = set(dead_pcs)
    loop._current_turn_index = current_index
    return loop


def _next_token(loop):
    return loop._turn_order[loop._current_turn_index % len(loop._turn_order)]


def test_turn_index_reanchors_when_later_combatant_dies():
    # order t1,t2,t3,t4; t2 (index 1) just acted, t3 died during its turn.
    # NEXT turn must be t4 — not t3's now-vacated slot.
    loop = _loop_with(["t1", "t2", "t3", "t4"], pcs=["t1", "t2", "t4"], npcs=[])
    loop._reanchor_turn_index("t2", acted_pos=1)
    assert loop._turn_order == ["t1", "t2", "t4"]
    assert _next_token(loop) == "t4"


def test_turn_index_reanchors_when_earlier_combatant_dies():
    # t3 (index 2) just acted; t1 (before it) died. NEXT turn must be t4.
    loop = _loop_with(["t1", "t2", "t3", "t4"], pcs=["t2", "t3", "t4"], npcs=[])
    loop._reanchor_turn_index("t3", acted_pos=2)
    assert loop._turn_order == ["t2", "t3", "t4"]
    assert _next_token(loop) == "t4"


def test_turn_index_handles_acted_token_removed():
    # t2 (index 1) killed itself on its turn. Resume at t3, the next survivor.
    loop = _loop_with(["t1", "t2", "t3"], pcs=["t1", "t3"], npcs=[])
    loop._reanchor_turn_index("t2", acted_pos=1)
    assert loop._turn_order == ["t1", "t3"]
    assert _next_token(loop) == "t3"


def test_turn_index_wraps_when_last_combatant_acts():
    # Last actor in the round acts; index lands past the end so the caller's
    # round-boundary check wraps it to the next round.
    loop = _loop_with(["t1", "t2", "t3"], pcs=["t1", "t2", "t3"], npcs=[])
    loop._reanchor_turn_index("t3", acted_pos=2)
    assert loop._current_turn_index == len(loop._turn_order)


def test_dead_pc_awaiting_death_save_stays_in_order():
    # A PC dropped to 0 HP is queued for a death-save turn — it must remain in
    # _turn_order (removing it would skip its save), even though it's no longer
    # in _pc_tokens.
    loop = _loop_with(["t1", "t2", "t3"], pcs=["t1", "t3"], npcs=[], dead_pcs=["t2"])
    loop._reanchor_turn_index("t1", acted_pos=0)
    assert loop._turn_order == ["t1", "t2", "t3"]
    assert _next_token(loop) == "t2"


if __name__ == "__main__":
    test_hostile_placement_requires_quantity_word()
    test_hostile_placement_still_counts_explicit_quantity()
    test_friendly_path_unchanged_without_require_quantity()
    test_player_whisper_to_gm_is_processed()
    test_player_whisper_to_another_player_is_skipped()
    test_rest_api_module_whisper_echo_is_skipped()
    test_turn_index_reanchors_when_later_combatant_dies()
    test_turn_index_reanchors_when_earlier_combatant_dies()
    test_turn_index_handles_acted_token_removed()
    test_turn_index_wraps_when_last_combatant_acts()
    test_dead_pc_awaiting_death_save_stays_in_order()
    print("All v1.0 review-fix regression tests passed.")
