"""TTS service — generates narration audio via LocalAI and serves it from FastAPI.

Flow for each narration/NPC speech:
  1. Strip markdown formatting from the text.
  2. POST to LocalAI /v1/audio/speech with the appropriate voice.
  3. Save the MP3 to the audio_dir (served by FastAPI at /audio/<filename>).
  4. Return the public URL so the caller can trigger Foundry AudioHelper.play().
  5. Prune old files so the audio dir doesn't grow unbounded.
"""

import hashlib
import logging
import re
import time
import uuid
from pathlib import Path
from typing import Optional, TYPE_CHECKING

import httpx

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

    async def _generate(self, text: str, voice: str, prefix: str) -> Optional[str]:
        clean_text = _strip_markdown(text)
        if not clean_text:
            return None

        filename = self._filename(clean_text, voice, prefix)
        audio_path = self.audio_dir / filename

        # Serve cached file if the same text+voice was recently requested
        if audio_path.exists():
            return f"{self.engine_base_url}/audio/{filename}"

        try:
            response = await self._client.post(
                f"{self.base_url}/v1/audio/speech",
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
            logger.error(f"[TTS] Request failed: {e}")
            return None

        audio_path.write_bytes(response.content)
        logger.info(f"[TTS] Generated {filename} ({len(response.content)//1024}KB, voice={voice})")
        self._prune_old_files()
        return f"{self.engine_base_url}/audio/{filename}"

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
