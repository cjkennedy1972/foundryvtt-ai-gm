#!/usr/bin/env python3
"""TTS audio went back to unintelligible on Python 3.13.

_postprocess_audio exists because the local TTS model emits peaks near
-19 dBFS — the "unintelligible" report in its own docstring — and pads the
tail with tens of seconds of near-silence. It peak-normalises and trims.

All of it ran through `audioop`, which PEP 594 removed in Python 3.13. The
import is guarded and the function returns its input unchanged when the
module is missing, so on 3.13 and 3.14 the fix silently stops happening and
the audio is quiet again. CI builds this project on 3.11, 3.13 and 3.14.

Peak normalisation and windowed RMS on 16-bit PCM are a few lines of
arithmetic, so there is now one path on every interpreter and no import to
degrade from.

Run:
    cd ai-engine && python -m pytest tests/test_tts_postprocess.py -v
"""

import array
import io
import math
import os
import sys
import tempfile
import wave
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tts.service import TTSService

RATE = 24000


def _wav(samples, channels=1, width=2, rate=RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(array.array("h", samples).tobytes())
    return buf.getvalue()


def _read(raw: bytes):
    with wave.open(io.BytesIO(raw), "rb") as r:
        return array.array("h", r.readframes(r.getnframes())), r.getframerate()


def _tone(n, amplitude, freq=220.0, rate=RATE):
    return [int(amplitude * math.sin(2 * math.pi * freq * i / rate)) for i in range(n)]


def _peak(samples):
    return max(max(samples), -min(samples)) if samples else 0


def _service(fmt="wav"):
    """Through the real constructor, as the original test did — a service
    built with __new__ would not catch __init__ changing what the method
    reads."""
    return TTSService(
        base_url="http://x", api_key="", model="m", narrator_voice="v",
        audio_dir=Path(tempfile.mkdtemp()), engine_base_url="http://e", fmt=fmt,
    )


# ── normalisation ─────────────────────────────────────────────────────────

def test_a_quiet_clip_is_brought_up_to_a_usable_level():
    """-19 dBFS is roughly an amplitude of 3700 out of 32767."""
    quiet = _wav(_tone(RATE, 3700))

    out, _ = _read(_service()._postprocess_audio(quiet))

    assert _peak(out) > 25000, f"peak is still {_peak(out)}"


def test_a_clip_already_at_a_good_level_is_left_alone():
    loud = _wav(_tone(RATE, 29000))

    out, _ = _read(_service()._postprocess_audio(loud))

    assert 28000 < _peak(out) <= 32767


def test_the_gain_is_capped_so_hiss_is_not_amplified():
    """A clip at amplitude 100 must not be multiplied by 290."""
    nearly_silent = _wav(_tone(RATE, 100))

    out, _ = _read(_service()._postprocess_audio(nearly_silent))

    assert _peak(out) <= 100 * 8, f"gain exceeded the 8x cap: peak {_peak(out)}"


def test_normalisation_does_not_wrap_around_into_distortion():
    out, _ = _read(_service()._postprocess_audio(_wav(_tone(RATE, 3700))))
    signs = [1 if s >= 0 else -1 for s in out[:200]]

    assert signs.count(1) > 50 and signs.count(-1) > 50, "samples wrapped sign"


# ── trimming ──────────────────────────────────────────────────────────────

def test_a_long_silent_tail_is_trimmed():
    speech = _tone(RATE, 12000)
    padded = _wav(speech + [0] * (RATE * 10))

    out, rate = _read(_service()._postprocess_audio(padded))

    assert len(out) / rate < 2.0, f"{len(out) / rate:.1f}s of audio survived"


def test_trimming_keeps_a_short_lead_so_speech_is_not_clipped():
    speech = _tone(RATE, 12000)
    padded = _wav([0] * RATE + speech + [0] * RATE)

    out, rate = _read(_service()._postprocess_audio(padded))

    assert len(out) / rate > 1.0, "the speech itself was cut"


def test_an_entirely_silent_clip_is_trimmed_not_emptied():
    """There is nothing to keep, so it comes back as the 0.3s pad rather than
    a zero-length file Foundry would fail to play. Unchanged behaviour."""
    silence = _wav([0] * RATE)

    out, rate = _read(_service()._postprocess_audio(silence))

    assert 0 < len(out) <= RATE
    assert _peak(out) == 0


# ── inputs it must not touch ──────────────────────────────────────────────

def test_a_non_wav_format_is_returned_as_is():
    """Valid WAV bytes, so only the format check can stop it being decoded,
    re-encoded and returned as something else."""
    svc = _service(fmt="mp3")
    raw = _wav(_tone(RATE, 3700) + [0] * (RATE * 5))

    assert svc._postprocess_audio(raw) == raw


def test_eight_bit_audio_is_returned_as_is():
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(1); w.setframerate(RATE)
        w.writeframes(bytes(RATE))
    raw = buf.getvalue()

    assert _service()._postprocess_audio(raw) == raw


def test_bytes_that_are_not_a_wav_are_returned_as_is():
    raw = b"\x00\x01\x02 definitely not riff"

    assert _service()._postprocess_audio(raw) == raw


def test_stereo_is_handled():
    frames = []
    for s in _tone(RATE, 4000):
        frames.extend([s, s])
    out, _ = _read(_service()._postprocess_audio(_wav(frames, channels=2)))

    assert _peak(out) > 25000


def test_a_stereo_silent_tail_is_trimmed_by_frames_not_samples():
    """Window positions are frames. Counting samples instead halves the
    apparent length and leaves half the padding in place."""
    frames = []
    for s in _tone(RATE, 12000):
        frames.extend([s, s])
    frames.extend([0] * (RATE * 10 * 2))

    out, rate = _read(_service()._postprocess_audio(_wav(frames, channels=2)))

    seconds = len(out) / 2 / rate
    assert seconds < 2.0, f"{seconds:.1f}s survived in a stereo clip"


# ── and none of it depends on a module 3.13 removed ───────────────────────

def test_normalisation_still_happens_without_audioop(monkeypatch):
    """What 3.13 and 3.14 actually run. With the module gone, the old
    implementation returned every clip untouched."""
    import tts.service as svc_mod

    monkeypatch.setattr(svc_mod, "audioop", None, raising=False)
    quiet = _wav(_tone(RATE, 3700))

    out, _ = _read(_service()._postprocess_audio(quiet))

    assert _peak(out) > 25000, "the clip came back at its original level"


def test_trimming_still_happens_without_audioop(monkeypatch):
    import tts.service as svc_mod

    monkeypatch.setattr(svc_mod, "audioop", None, raising=False)
    padded = _wav(_tone(RATE, 12000) + [0] * (RATE * 10))

    out, rate = _read(_service()._postprocess_audio(padded))

    assert len(out) / rate < 2.0



def test_this_file_does_not_skip_itself_on_the_versions_that_were_broken():
    """The original module skipped itself when audioop was missing, so on
    3.13 and 3.14 — the two interpreters where the post-processing had
    silently stopped — the tests that would have caught it opted out.

    Checked by AST: this docstring names the call, and a substring search
    would match itself."""
    import ast
    import pathlib

    tree = ast.parse(pathlib.Path(__file__).read_text())
    calls = {
        node.func.attr for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }

    assert "importorskip" not in calls


def test_the_service_does_not_import_audioop():
    """PEP 594 removed it in 3.13. CI builds this on 3.13 and 3.14, where the
    guarded import left every clip unprocessed."""
    import ast
    import pathlib

    src = pathlib.Path(__file__).resolve().parent.parent / "tts" / "service.py"
    tree = ast.parse(src.read_text())
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in getattr(node, "names", [])
    }

    assert "audioop" not in imported
