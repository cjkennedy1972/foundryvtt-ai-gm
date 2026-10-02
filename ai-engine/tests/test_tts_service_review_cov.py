"""TTSService voice resolution, caching, normalisation and pruning."""
import asyncio
import array
import io
import os
import wave

import httpx
import pytest

from config import settings
from tts import service as tts_service
from tts.service import TTSService, _strip_markdown


def _svc(tmp_path, fmt="wav", **kw):
    return TTSService("http://x/v1/", "k", "kokoro", "narr", tmp_path, "http://engine/", fmt=fmt, **kw)


def _set(monkeypatch, **kw):
    for k, v in kw.items():
        monkeypatch.setattr(settings, k, v)


def test_urls_are_normalised_and_markdown_is_stripped():
    assert _strip_markdown("**Bold** and _it_ [link](http://x) `code`\n# Head\n- item") == "Bold and it link Head item"


def test_voice_map_parsing_ignores_malformed_pairs():
    assert TTSService._parse_voice_map("Deep_Male: am_adam ,bad,:x,y:, plain_male:am_m") == {"deep_male": "am_adam", "plain_male": "am_m"}


def test_voice_resolution_is_a_noop_with_nothing_configured(tmp_path, monkeypatch):
    _set(monkeypatch, tts_allowed_voices="", tts_voice_map="", tts_voice_male="", tts_voice_female="")
    assert _svc(tmp_path)._resolve_voice("whatever") == "whatever"


def test_explicit_map_beats_gender_fallback(tmp_path, monkeypatch):
    _set(monkeypatch, tts_allowed_voices="", tts_voice_map="deep_male:special", tts_voice_male="m", tts_voice_female="f")
    s = _svc(tmp_path)
    assert s._resolve_voice("deep_male") == "special"
    assert s._resolve_voice("warm_female") == "f"
    assert s._resolve_voice("sly_male") == "m"


def test_unknown_archetype_falls_back_to_a_configured_gender_voice_then_narrator(tmp_path, monkeypatch):
    _set(monkeypatch, tts_allowed_voices="", tts_voice_map="", tts_voice_male="", tts_voice_female="f")
    assert _svc(tmp_path)._resolve_voice("mystery") == "f"
    _set(monkeypatch, tts_voice_female="", tts_voice_male="m")
    assert _svc(tmp_path)._resolve_voice("mystery") == "m"


def test_a_voice_already_allowed_is_kept_even_when_gender_maps_exist(tmp_path, monkeypatch):
    _set(monkeypatch, tts_allowed_voices="am_adam,af_bella", tts_voice_map="", tts_voice_male="am_adam", tts_voice_female="af_bella")
    assert _svc(tmp_path)._resolve_voice("af_bella") == "af_bella"


def test_whitelist_prefers_narrator_then_first_allowed(tmp_path, monkeypatch):
    _set(monkeypatch, tts_voice_map="", tts_voice_male="", tts_voice_female="", tts_allowed_voices="a,narr")
    assert _svc(tmp_path)._resolve_voice("bogus") == "narr"
    _set(monkeypatch, tts_allowed_voices="a,b")
    assert _svc(tmp_path)._resolve_voice("bogus") == "a"


def test_narrate_and_speak_post_the_right_payload_and_return_engine_urls(tmp_path, monkeypatch):
    _set(monkeypatch, tts_allowed_voices="", tts_voice_map="", tts_voice_male="", tts_voice_female="")
    s = _svc(tmp_path, fmt="mp3")
    sent = []

    async def post(url, json=None, **_):
        sent.append((url, json))
        return httpx.Response(200, content=b"ID3", request=httpx.Request("POST", url))

    s._client.post = post
    url = asyncio.run(s.narrate("**The** door opens."))
    assert url.startswith("http://engine/audio/narr_") and url.endswith(".mp3")
    assert sent[0][1] == {"model": "kokoro", "input": "The door opens.", "voice": "narr", "response_format": "mp3"}
    assert sent[0][0] == "http://x/v1/audio/speech"
    assert (tmp_path / url.rsplit("/", 1)[1]).read_bytes() == b"ID3"

    npc = asyncio.run(s.speak("Greetings, traveller.", "Bartholomew the Brave"))
    assert "/audio/npc_Bartholomew_" in npc          # prefix truncated to 12 chars, spaces stripped
    asyncio.run(s.close())


def test_the_same_text_and_voice_is_served_from_cache_without_a_request(tmp_path, monkeypatch):
    _set(monkeypatch, tts_allowed_voices="", tts_voice_map="", tts_voice_male="", tts_voice_female="")
    s = _svc(tmp_path, fmt="mp3")
    calls = []

    async def post(url, json=None, **_):
        calls.append(1)
        return httpx.Response(200, content=b"x", request=httpx.Request("POST", url))

    s._client.post = post
    a = asyncio.run(s.narrate("Same line."))
    b = asyncio.run(s.narrate("Same line."))
    assert a == b and len(calls) == 1


def test_empty_after_markdown_stripping_makes_no_request(tmp_path):
    s = _svc(tmp_path)
    s._client.post = lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not post"))
    assert asyncio.run(s.narrate("``` ```")) is None


def test_a_network_error_returns_none_and_writes_nothing(tmp_path, monkeypatch):
    _set(monkeypatch, tts_allowed_voices="", tts_voice_map="", tts_voice_male="", tts_voice_female="")
    s = _svc(tmp_path)

    async def post(*a, **k):
        raise httpx.ConnectError("down")

    s._client.post = post
    assert asyncio.run(s.narrate("Hello there.")) is None
    assert list(tmp_path.iterdir()) == []


def _wav(samples, rate=1000, ch=1):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(ch)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(array.array("h", samples).tobytes())
    return buf.getvalue()


def _read(raw):
    with wave.open(io.BytesIO(raw)) as r:
        a = array.array("h")
        a.frombytes(r.readframes(r.getnframes()))
        return list(a)


def test_non_wav_and_garbage_audio_pass_through_unchanged(tmp_path):
    assert _svc(tmp_path, fmt="mp3")._postprocess_audio(b"abc") == b"abc"
    assert _svc(tmp_path)._postprocess_audio(b"not a wav") == b"not a wav"


def test_quiet_audio_is_boosted_to_the_target_peak_and_silent_padding_trimmed(tmp_path):
    speech = [4000, -4000] * 500                    # 1s of quiet speech at 1kHz
    raw = _wav([0] * 3000 + speech + [0] * 3000)    # 3s of silence either side
    out = _read(_svc(tmp_path)._postprocess_audio(raw))
    assert max(abs(s) for s in out) == pytest.approx(0.89 * 32767, rel=0.01)
    assert len(out) < 2200                          # speech (1000) + ~0.3s pad each side, not 7000


def test_gain_is_capped_so_near_silence_is_not_blown_up(tmp_path):
    out = _read(_svc(tmp_path)._postprocess_audio(_wav([100, -100] * 600)))
    assert max(abs(s) for s in out) == 800          # 8x cap


def test_an_all_silent_clip_is_kept_rather_than_emptied(tmp_path):
    out = _read(_svc(tmp_path)._postprocess_audio(_wav([0] * 500)))
    assert len(out) > 0


def test_loud_audio_is_not_amplified(tmp_path):
    loud = [30000, -30000] * 500
    assert _read(_svc(tmp_path)._postprocess_audio(_wav(loud))) == loud


def test_stereo_is_trimmed_by_frame_not_by_sample(tmp_path):
    out = _read(_svc(tmp_path)._postprocess_audio(_wav([5000] * 2000, rate=1000, ch=2)))
    assert len(out) % 2 == 0


def test_prune_removes_oldest_files_beyond_the_cap(tmp_path):
    s = _svc(tmp_path, fmt="mp3", max_cached_files=2)
    for i, name in enumerate(["a", "b", "c", "d"]):
        p = tmp_path / f"{name}.mp3"
        p.write_bytes(b"x")
        os.utime(p, (1000 + i, 1000 + i))
    (tmp_path / "keep.txt").write_text("not audio")
    s._prune_old_files()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["c.mp3", "d.mp3", "keep.txt"]


def test_prune_survives_an_unlink_failure(tmp_path, monkeypatch):
    s = _svc(tmp_path, fmt="mp3", max_cached_files=0)
    (tmp_path / "a.mp3").write_bytes(b"x")
    monkeypatch.setattr(tts_service.Path, "unlink", lambda self, *a, **k: (_ for _ in ()).throw(OSError("busy")))
    s._prune_old_files()      # must not raise
    monkeypatch.undo()
    assert (tmp_path / "a.mp3").exists()  # the file the failed unlink targeted is untouched


def test_filename_is_filesystem_safe_and_depends_on_voice(tmp_path):
    s = _svc(tmp_path)
    a, b = s._filename("hi", "v1", "npc_a/b..c"), s._filename("hi", "v2", "npc_a/b..c")
    assert a.startswith("npc_abc_") and a.endswith(".wav") and a != b
