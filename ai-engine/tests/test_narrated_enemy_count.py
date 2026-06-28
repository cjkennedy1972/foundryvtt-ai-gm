#!/usr/bin/env python3
"""
Regression test: enemy quantity parsing from narration.

The GM narrates foes appearing ('two towering Revenants', 'a horde of
Skeletons') without placing them. _mention_count drives how many tokens the
reconciler drops, and must not be fooled by common-noun uses ('into the
shadows' must not spawn the 'Shadow' monster).

Run:
    cd ai-engine && python -m pytest tests/test_narrated_enemy_count.py -v
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from foundry.chat_listener import _mention_count


def test_explicit_number():
    assert _mention_count("two towering Revenants lunge forward", "Revenant") == 2
    assert _mention_count("a horde of Skeletons rises", "Skeleton") == 6
    assert _mention_count("six Skeletons surround you", "Skeleton") == 6


def test_capitalized_plural_defaults_to_group():
    assert _mention_count("Skeletons claw out of the dust", "Skeleton") == 3


def test_capitalized_singular():
    assert _mention_count("The Revenant raises its blade", "Revenant") == 1


def test_lowercase_common_noun_is_ignored():
    # 'shadows'/'skeletons' as common nouns must NOT spawn monsters.
    assert _mention_count("he retreats into the shadows", "Shadow") == 0
    assert _mention_count("the old skeletons of a campfire", "Skeleton") == 0


def test_absent():
    assert _mention_count("a quiet, empty hall", "Revenant") == 0


if __name__ == "__main__":
    test_explicit_number()
    test_capitalized_plural_defaults_to_group()
    test_capitalized_singular()
    test_lowercase_common_noun_is_ignored()
    test_absent()
    print("All narrated-enemy-count tests passed.")
