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


def test_stop_playback_initializes():
    """Test that stop_playback can be called without errors."""
    import asyncio

    async def test():
        # This should not raise an error even if no playback is active
        await playback.stop_playback()

    asyncio.run(test())


def test_playback_is_active_reflects_configured_engine():
    """Test that is_active reflects the configured engine."""
    playback.configure(None, None, engine="server")
    assert playback.is_active() is False  # no service, not browser

    playback.configure(object(), None, engine="server")
    assert playback.is_active() is True  # service present

    playback.configure(None, None, engine="browser")
    assert playback.is_active() is True  # browser engine needs no service

    playback.configure(None, None, engine="server")  # reset for other tests
