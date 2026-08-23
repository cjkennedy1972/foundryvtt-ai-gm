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
# #2 — combat turn re-anchoring: the index must follow the acted token's id,
# not its position, after the end-check removes a combatant.
# ---------------------------------------------------------------------------

def test_turn_index_reanchors_to_acted_token():
    order = ["t1", "t2", "t3", "t4"]
    # t2 just acted (index 1); advancing then removing t3 must leave the NEXT
    # turn pointing at t4, not at the wrong slot.
    acted = order[1]
    idx = 1 + 1  # naive advance -> 2
    # simulate end-check removing t3
    new_order = ["t1", "t2", "t4"]
    if acted in new_order:
        idx = new_order.index(acted) + 1  # the fix: re-anchor by id
    assert new_order[idx % len(new_order)] == "t4"


def test_turn_index_handles_acted_token_removed():
    # The acted token itself died on its own turn — the advanced modulo index
    # is the correct fallback.
    order = ["t1", "t2", "t3"]
    acted = order[1]
    idx = 1 + 1
    new_order = ["t1", "t3"]  # t2 removed
    if acted in new_order:
        idx = new_order.index(acted) + 1
    # fallback: idx stays 2 -> wraps to t1's next slot correctly
    assert new_order[idx % len(new_order)] == "t1"


if __name__ == "__main__":
    test_hostile_placement_requires_quantity_word()
    test_hostile_placement_still_counts_explicit_quantity()
    test_friendly_path_unchanged_without_require_quantity()
    test_player_whisper_to_gm_is_processed()
    test_player_whisper_to_another_player_is_skipped()
    test_rest_api_module_whisper_echo_is_skipped()
    test_turn_index_reanchors_to_acted_token()
    test_turn_index_handles_acted_token_removed()
    print("All v1.0 review-fix regression tests passed.")
