"""TTS service — generates narration audio via LocalAI and serves it from FastAPI.

Flow for each narration/NPC speech:
  1. Strip markdown formatting from the text.
  2. POST to LocalAI /v1/audio/speech with the appropriate voice.
  3. Save the MP3 to the audio_dir (served by FastAPI at /audio/<filename>).
  4. Return the public URL so the caller can trigger Foundry AudioHelper.play().
  5. Prune old files so the audio dir doesn't grow unbounded.
"""

import hashlib
import io
import logging
import re
import time
import uuid
import wave
from pathlib import Path
from typing import Optional, TYPE_CHECKING

try:
    import audioop  # stdlib in 3.11; removed in 3.13 — degrade gracefully
except ImportError:  # pragma: no cover
    audioop = None

import httpx

from config import settings

if TYPE_CHECKING:
    from npc.registry import NPCRecord

from tts.voice_assigner import VoiceAssigner

logger = logging.getLogger(__name__)

_MARKDOWN_RE = re.compile(
    r"\*{1,3}(.+?)\*{1,3}"   # *italic*, **bold**, ***both***
    r"|_{1,3}(.+?)_{1,3}"     # _italic_, __bold__
    r"|`{1,3}[^`]*`{1,3}"     # inline code / code blocks
    r"|\[([^\]]*)\]\([^)]*\)" # [text](url)
    r"|^#{1,6}\s+"             # headings
    r"|^[-*+]\s+"              # list bullets
    r"|^>\s+",                 # blockquotes
    re.MULTILINE,
)


def _strip_markdown(text: str) -> str:
    """Remove markdown formatting so TTS reads clean prose."""
    cleaned = _MARKDOWN_RE.sub(lambda m: m.group(1) or m.group(2) or m.group(3) or "", text)
    return " ".join(cleaned.split())  # normalise whitespace


class TTSService:
    """Async TTS client backed by a LocalAI /v1/audio/speech endpoint."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        narrator_voice: str,
        audio_dir: Path,
        engine_base_url: str,
        fmt: str = "mp3",
        max_cached_files: int = 50,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.narrator_voice = narrator_voice
        self.audio_dir = Path(audio_dir)
        self.audio_dir.mkdir(parents=True, exist_ok=True)
        self.engine_base_url = engine_base_url.rstrip("/")
        self.fmt = fmt
        self.max_cached_files = max_cached_files
        self.voice_assigner = VoiceAssigner()
        self._client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=60.0,
        )

    async def narrate(self, text: str) -> Optional[str]:
        """Generate TTS for GM narrator. Returns a public URL or None on error."""
        return await self._generate(text, self.narrator_voice, prefix="narr")

    async def speak(
        self,
        text: str,
        npc_name: str,
        npc_record: Optional["NPCRecord"] = None,
    ) -> Optional[str]:
        """Generate TTS for an NPC. Voice is assigned by VoiceAssigner."""
        voice = self.voice_assigner.get_voice(npc_name, npc_record)
        return await self._generate(text, voice, prefix=f"npc_{npc_name[:12]}")

    async def close(self):
        await self._client.aclose()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    # VoiceAssigner archetype voices grouped by gender (see tts/voice_assigner.py).
    _ARCHETYPE_GENDER = {
        "fable": "male",
        "deep_male": "male", "gruff_male": "male", "sage_male": "male",
        "reverent_male": "male", "hearty_male": "male", "sly_male": "male",
        "plain_male": "male", "noble_male": "male",
        "mystic_female": "female", "warm_female": "female", "fierce_female": "female",
        "light_female": "female", "sly_female": "female", "noble_female": "female",
        "plain_female": "female",
    }

    @staticmethod
    def _parse_voice_map(spec: str) -> dict:
        """Parse "archetype:voice,archetype:voice" into a dict."""
        out = {}
        for pair in spec.split(","):
            if ":" in pair:
                k, v = pair.split(":", 1)
                k, v = k.strip().lower(), v.strip()
                if k and v:
                    out[k] = v
        return out

    def _resolve_voice(self, voice: str) -> str:
        """Map archetype voices to the model's real voices and enforce a
        whitelist, so models with only a few fixed voices never get an unknown
        name (which would 500). No-op when no mapping/whitelist is configured.

        Precedence: explicit per-archetype map (tts_voice_map) → gender map
        (tts_voice_male/female) → whitelist fallback.
        """
        allowed = [v.strip() for v in settings.tts_allowed_voices.split(",") if v.strip()]
        vmap = self._parse_voice_map(settings.tts_voice_map)

        # 1) Explicit archetype -> model-voice map (most granular).
        if vmap:
            mapped = vmap.get((voice or "").lower())
            if mapped:
                voice = mapped

        # 2) Gender fallback.
        male = settings.tts_voice_male
        female = settings.tts_voice_female
        if (male or female) and (not allowed or voice not in allowed):
            gender = self._ARCHETYPE_GENDER.get((voice or "").lower())
            if gender == "male" and male:
                voice = male
            elif gender == "female" and female:
                voice = female
            elif not allowed or voice not in allowed:
                voice = male or female or self.narrator_voice

        if allowed and voice not in allowed:
            voice = self.narrator_voice if self.narrator_voice in allowed else allowed[0]
        return voice

    async def _generate(self, text: str, voice: str, prefix: str) -> Optional[str]:
        clean_text = _strip_markdown(text)
        if not clean_text:
            return None

        voice = self._resolve_voice(voice)
        filename = self._filename(clean_text, voice, prefix)
        audio_path = self.audio_dir / filename

        # Serve cached file if the same text+voice was recently requested
        if audio_path.exists():
            return f"{self.engine_base_url}/audio/{filename}"

        try:
            response = await self._client.post(
                f"{self.base_url}/audio/speech",
                json={
                    "model": self.model,
                    "input": clean_text,
                    "voice": voice,
                    "response_format": self.fmt,
                },
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            logger.error(f"[TTS] HTTP {e.response.status_code} from LocalAI: {e.response.text[:200]}")
            return None
        except Exception as e:
            logger.error(f"[TTS] Request failed: {e}", exc_info=True)
            return None

        audio_bytes = self._postprocess_audio(response.content)
        audio_path.write_bytes(audio_bytes)
        logger.info(f"[TTS] Generated {filename} ({len(audio_bytes)//1024}KB, voice={voice})")
        self._prune_old_files()
        return f"{self.engine_base_url}/audio/{filename}"

    def _postprocess_audio(self, raw: bytes) -> bytes:
        """Peak-normalize and trim silent padding from TTS WAV output.

        The local TTS model emits very low-amplitude audio (peaks near -19 dBFS,
        barely audible once played in Foundry — the 'unintelligible' report) and
        sometimes pads the tail with tens of seconds of near-silence (which both
        freezes the GM for the silent tail and bloats the file). Boost the peak
        to a comfortable level and trim leading/trailing near-silence.
        Best-effort: returns the input unchanged on any error, for non-wav
        formats, or when audioop is unavailable.
        """
        if audioop is None or self.fmt != "wav":
            return raw
        try:
            with wave.open(io.BytesIO(raw), "rb") as r:
                ch, sw, fr = r.getnchannels(), r.getsampwidth(), r.getframerate()
                frames = r.readframes(r.getnframes())
            if sw != 2 or not frames:
                return raw
            # Peak-normalize to ~0.89 full scale; cap the gain so a near-silent
            # clip isn't blown up into loud hiss.
            peak = audioop.max(frames, sw)
            if peak > 0:
                factor = min(8.0, (0.89 * 32767) / peak)
                if factor > 1.05:
                    frames = audioop.mul(frames, sw, factor)
            # Trim leading/trailing near-silence in 100ms windows.
            width = sw * ch
            fpw = max(1, int(fr * 0.1))
            total = len(frames) // width
            sil = 490  # ~0.015 full scale, post-normalization

            def win_rms(fi: int) -> int:
                seg = frames[fi * width:(fi + fpw) * width]
                return audioop.rms(seg, sw) if len(seg) >= width else 0

            start = 0
            while start < total and win_rms(start) < sil:
                start += fpw
            end = total
            while end > start and win_rms(max(0, end - fpw)) < sil:
                end -= fpw
            pad = int(fr * 0.3)  # keep a short lead/tail so speech isn't clipped
            start = max(0, start - pad)
            end = min(total, end + pad)
            trimmed = frames[start * width:end * width] or frames

            buf = io.BytesIO()
            with wave.open(buf, "wb") as w:
                w.setnchannels(ch)
                w.setsampwidth(sw)
                w.setframerate(fr)
                w.writeframes(trimmed)
            return buf.getvalue()
        except Exception as e:
            logger.debug(f"[TTS] audio post-process skipped: {e}")
            return raw

    def _filename(self, text: str, voice: str, prefix: str) -> str:
        """Stable, filesystem-safe filename based on text + voice hash."""
        digest = hashlib.sha1(f"{voice}:{text}".encode()).hexdigest()[:12]
        safe_prefix = re.sub(r"[^a-zA-Z0-9_]", "", prefix)
        return f"{safe_prefix}_{digest}.{self.fmt}"

    def _prune_old_files(self):
        """Delete oldest audio files when the cache exceeds max_cached_files."""
        files = sorted(self.audio_dir.glob(f"*.{self.fmt}"), key=lambda p: p.stat().st_mtime)
        for old in files[: max(0, len(files) - self.max_cached_files)]:
            try:
                old.unlink()
            except OSError:
                pass
