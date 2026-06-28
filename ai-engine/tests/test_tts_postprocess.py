#!/usr/bin/env python3
"""
Regression test: TTS WAV post-processing normalizes level and trims silence.

The local TTS model emitted very quiet audio (peak ~0.11 full scale = barely
audible) padded with tens of seconds of trailing silence — the 'unintelligible'
report. _postprocess_audio must boost the peak and trim the silent tail.

Run:
    cd ai-engine && python -m pytest tests/test_tts_postprocess.py -v
"""

import io
import math
import os
import struct
import sys
import wave

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

audioop = pytest.importorskip("audioop")
from tts.service import TTSService  # noqa: E402


def _make_wav(seconds_speech: float, seconds_silence: float, peak: float, fr: int = 24000) -> bytes:
    """Quiet 440Hz tone for `seconds_speech`, then silence for `seconds_silence`."""
    samples = []
    for i in range(int(fr * seconds_speech)):
        samples.append(int(peak * 32767 * math.sin(2 * math.pi * 440 * i / fr)))
    samples += [0] * int(fr * seconds_silence)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(fr)
        w.writeframes(b"".join(struct.pack("<h", s) for s in samples))
    return buf.getvalue()


def _svc(tmp):
    return TTSService(
        base_url="http://x", api_key="", model="m", narrator_voice="v",
        audio_dir=tmp, engine_base_url="http://e", fmt="wav",
    )


def _peak_and_dur(raw):
    with wave.open(io.BytesIO(raw), "rb") as r:
        fr, n = r.getframerate(), r.getnframes()
        frames = r.readframes(n)
    return audioop.max(frames, 2) / 32767, n / fr


def test_quiet_padded_audio_is_normalized_and_trimmed(tmp_path):
    svc = _svc(tmp_path)
    raw = _make_wav(seconds_speech=5.0, seconds_silence=40.0, peak=0.11)
    in_peak, in_dur = _peak_and_dur(raw)
    out = svc._postprocess_audio(raw)
    out_peak, out_dur = _peak_and_dur(out)

    assert in_peak < 0.2 and in_dur > 40          # quiet + padded going in
    assert out_peak > 0.8                          # normalized loud
    assert out_dur < 7.0                           # silent tail trimmed (~5s + pad)


def test_nonwav_passthrough(tmp_path):
    svc = _svc(tmp_path)
    svc.fmt = "mp3"
    raw = b"\xff\xfb not a wav"
    assert svc._postprocess_audio(raw) is raw


if __name__ == "__main__":
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        from pathlib import Path
        test_quiet_padded_audio_is_normalized_and_trimmed(Path(d))
        print("PASS  quiet padded audio normalized + trimmed")
        test_nonwav_passthrough(Path(d))
        print("PASS  non-wav passthrough")
    print("All TTS post-process tests passed.")
