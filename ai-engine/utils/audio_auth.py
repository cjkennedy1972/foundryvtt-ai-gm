"""HMAC signing for generated audio URLs."""

import hashlib
import hmac
import time
from urllib.parse import urlencode


def audio_signature(filename: str, expires: int, secret: str) -> str:
    payload = f"{filename}:{expires}".encode()
    return hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()


def signed_audio_url(base_url: str, filename: str, secret: str, ttl: int) -> str:
    expires = int(time.time()) + ttl
    signature = audio_signature(filename, expires, secret)
    return f"{base_url.rstrip('/')}/audio/{filename}?{urlencode({'expires': expires, 'signature': signature})}"


def verify_audio_signature(filename: str, expires: int, signature: str, secret: str, now: int | None = None) -> bool:
    if not secret or expires < int(time.time() if now is None else now):
        return False
    expected = audio_signature(filename, expires, secret)
    return hmac.compare_digest(signature, expected)
