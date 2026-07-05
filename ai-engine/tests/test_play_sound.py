"""Regression test: play_sound must resolve a semantic name to a real audio
src, and skip gracefully (not fail) when nothing matches.

Root cause this guards against: the LLM emits semantic names ("low_growl")
but the relay's play-sound needs a real `src` file path — every call was
sending `name=` and failing with "src is required", which also drove a
wasted corrective LLM retry each beat.
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

import actions.executors as ex
from actions.executors import execute_play_sound


def _reset_cache():
    ex._sound_src_cache = {}
    ex._sound_src_cache_at = 0.0


def _foundry_with_sounds(sounds):
    foundry = MagicMock()
    foundry.get_playlists = AsyncMock(return_value=[{"name": "Ambience", "sounds": sounds}])
    foundry.play_sound = AsyncMock(return_value={"success": True})
    return foundry


def test_play_sound_resolves_exact_name_to_src():
    _reset_cache()
    foundry = _foundry_with_sounds([{"name": "Low Growl", "path": "sounds/growl.ogg"}])

    result = asyncio.run(execute_play_sound("low_growl", foundry=foundry))

    foundry.play_sound.assert_awaited_once()
    assert foundry.play_sound.await_args.args[0] == "sounds/growl.ogg"
    assert result["src"] == "sounds/growl.ogg"


def test_play_sound_loose_matches_on_shared_word():
    _reset_cache()
    foundry = _foundry_with_sounds([{"name": "Creaking Door", "path": "sounds/creak.ogg"}])

    result = asyncio.run(execute_play_sound("creaking_wood", foundry=foundry))

    assert result.get("src") == "sounds/creak.ogg"


def test_play_sound_skips_cleanly_when_no_match():
    _reset_cache()
    foundry = _foundry_with_sounds([{"name": "Harp", "path": "sounds/harp.ogg"}])

    result = asyncio.run(execute_play_sound("explosion", foundry=foundry))

    # No doomed relay call, and NOT a failure (must not trigger the retry loop).
    foundry.play_sound.assert_not_awaited()
    assert result["skipped"] is True
    assert result["success"] is True
    assert "error" not in result
