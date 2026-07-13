from utils.audio_auth import audio_signature, signed_audio_url, verify_audio_signature


def test_audio_signature_round_trip():
    signature = audio_signature("narr-abc.wav", 200, "secret")
    assert verify_audio_signature("narr-abc.wav", 200, signature, "secret", now=199)


def test_audio_signature_rejects_expired_or_tampered_values():
    signature = audio_signature("narr-abc.wav", 200, "secret")
    assert not verify_audio_signature("narr-abc.wav", 200, signature, "secret", now=201)
    assert not verify_audio_signature("other.wav", 200, signature, "secret", now=199)


def test_signed_audio_url_contains_expiry_and_signature():
    url = signed_audio_url("http://engine", "narr-abc.wav", "secret", 300)
    assert url.startswith("http://engine/audio/narr-abc.wav?")
    assert "expires=" in url and "signature=" in url
