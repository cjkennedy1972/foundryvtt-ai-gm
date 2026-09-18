"""Tests for TTS sentence-by-sentence synthesis and barge-in functionality."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tts import playback


def test_sentence_splitting_simple():
    """Test basic sentence splitting."""
    text = "The goblin sneers. He draws his sword. The battle begins!"
    sentences = playback._split_sentences(text)
    assert len(sentences) == 3
    assert sentences[0] == "The goblin sneers."
    assert sentences[1] == "He draws his sword."
    assert sentences[2] == "The battle begins!"


def test_sentence_splitting_single_sentence():
    """Test splitting of text with only one sentence."""
    text = "The dragon looms before you."
    sentences = playback._split_sentences(text)
    assert len(sentences) == 1
    assert sentences[0] == "The dragon looms before you."


def test_sentence_splitting_empty():
    """Test splitting of empty text."""
    sentences = playback._split_sentences("")
    assert sentences == []


def test_sentence_splitting_question_exclamation():
    """Test splitting with various punctuation."""
    text = "Are you ready? Yes! The adventure begins."
    sentences = playback._split_sentences(text)
    assert len(sentences) == 3
    assert sentences[0] == "Are you ready?"
    assert sentences[1] == "Yes!"
    assert sentences[2] == "The adventure begins."


def test_sentence_splitting_lowercase_after_period():
    """Test that lowercase after period doesn't create a split."""
    text = "The goblin sneers. then he draws his sword. The battle begins."
    sentences = playback._split_sentences(text)
    # Should not split after "sneers." because the next token starts lowercase.
    assert len(sentences) == 2
    assert sentences[0] == "The goblin sneers. then he draws his sword."
    assert sentences[1] == "The battle begins."


def test_stop_playback_clears_the_active_task():
    """Safe with nothing playing, and it must actually cancel what is."""
    import asyncio

    async def scenario():
        async def never_finishes():
            await asyncio.sleep(3600)

        task = asyncio.create_task(never_finishes())
        playback._active_playback_task = task
        await playback.stop_playback()
        return task

    task = asyncio.run(scenario())

    assert playback._active_playback_task is None
    assert task.cancelled() or task.done()


def test_stop_playback_is_safe_with_nothing_playing():
    import asyncio

    playback._active_playback_task = None
    asyncio.run(playback.stop_playback())

    assert playback._active_playback_task is None


def test_playback_is_active_reflects_configured_engine():
    """Test that is_active reflects the configured engine."""
    playback.configure(None, None, engine="server")
    assert playback.is_active() is False  # no service, not browser

    playback.configure(object(), None, engine="server")
    assert playback.is_active() is True  # service present

    playback.configure(None, None, engine="browser")
    assert playback.is_active() is True  # browser engine needs no service

    playback.configure(None, None, engine="server")  # reset for other tests
