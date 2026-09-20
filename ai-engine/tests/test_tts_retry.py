"""LocalAI's ROCm TTS backend throws a transient 500 on the first request after a
voice change; the same request succeeds when repeated, so one retry saves the line."""
import asyncio
import httpx
from tts.service import TTSService


def _service(tmp_path, responses):
    svc = TTSService("http://x/v1", "k", "kokoro", "bm_fable", tmp_path, "http://engine", fmt="wav")
    calls = []

    async def post(url, json=None, **_):
        calls.append(url)
        code, body = responses[min(len(calls) - 1, len(responses) - 1)]
        return httpx.Response(code, content=body, request=httpx.Request("POST", url))

    svc._client.post = post
    svc._postprocess_audio = lambda b: b
    return svc, calls


def test_a_transient_500_is_retried_once(tmp_path):
    svc, calls = _service(tmp_path, [(500, b"miopenStatusUnknownError"), (200, b"RIFFaudio")])

    url = asyncio.run(svc._generate("Hello there, mortals.", "bm_fable", "npc"))

    assert url and len(calls) == 2


def test_a_persistent_500_gives_up_after_one_retry(tmp_path):
    svc, calls = _service(tmp_path, [(500, b"boom")])

    assert asyncio.run(svc._generate("Hello there, mortals.", "bm_fable", "npc")) is None
    assert len(calls) == 2


def test_a_client_error_is_not_retried(tmp_path):
    svc, calls = _service(tmp_path, [(400, b"bad voice")])

    assert asyncio.run(svc._generate("Hello there, mortals.", "bm_fable", "npc")) is None
    assert len(calls) == 1
