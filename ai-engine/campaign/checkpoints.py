"""Crash-safe checkpoints for the campaign build pipeline.

Checkpoints deliberately live beside generated assets rather than in the vault:
they describe an in-flight build and must not become campaign canon.
"""

import asyncio
import json
from pathlib import Path
from typing import Any, Dict, Optional

from utils.path_safety import sanitize_filename


class BuildCheckpoint:
    """Persist and restore the last completed build phase atomically."""

    def __init__(self, path: Path):
        self.path = self._sanitize_path(Path(path))

    @staticmethod
    def _sanitize_path(path: Path) -> Path:
        parts = []
        for part in path.parts:
            if part in {"", ".", path.anchor}:
                continue
            parts.append(sanitize_filename(part))
        if path.is_absolute():
            return Path(path.anchor, *parts)
        return Path(*parts)

    async def load(self) -> Optional[Dict[str, Any]]:
        if not self.path.exists():
            return None
        try:
            return await asyncio.to_thread(
                json.loads, await asyncio.to_thread(self.path.read_text, encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError, TypeError):
            return None

    async def save(self, phase: str, **state: Any) -> None:
        payload = {"phase": phase, **state}
        await asyncio.to_thread(self.path.parent.mkdir, parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        await asyncio.to_thread(
            temp.write_text,
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        await asyncio.to_thread(temp.replace, self.path)

    async def clear(self) -> None:
        if self.path.exists():
            await asyncio.to_thread(self.path.unlink)
