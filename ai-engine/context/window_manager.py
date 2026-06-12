"""Context Window Manager — manages token-aware conversation history trimming."""

import asyncio
import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class ContextWindowManager:
    """Manages LLM conversation context within token limits."""

    # Rough token estimates per character
    CHAR_TO_TOKEN = 4

    def __init__(
        self,
        max_tokens: int = 50000,
        keep_system: bool = True,
        keep_recent: int = 20,
    ):
        self.max_tokens = max_tokens
        self.keep_system = keep_system
        self.keep_recent = keep_recent
        self._system_prompt: Optional[str] = None
        self._messages: List[Dict[str, Any]] = []
        self._total_tokens = 0

    def set_system_prompt(self, prompt: str):
        """Set the system prompt and count its tokens."""
        self._system_prompt = prompt
        token_count = self._estimate_tokens(prompt)
        self._total_tokens += token_count
        logger.info(f"[Context] System prompt: {token_count} tokens, total: {self._total_tokens}")

    @property
    def messages(self) -> List[Dict[str, Any]]:
        return list(self._messages)

    @property
    def total_tokens(self) -> int:
        return self._total_tokens

    def add_message(self, role: str, content: str):
        """Add a message and count its tokens."""
        token_count = self._estimate_tokens(content)
        self._total_tokens += token_count
        self._messages.append({
            "role": role,
            "content": content,
            "tokens": token_count,
        })

        # Trim if over budget
        self._trim()

    def add_system(self, content: str):
        """Add a system message."""
        if not self.keep_system:
            return
        # Replace existing system if present
        self._messages = [
            m for m in self._messages if m.get("role") != "system"
        ]
        self.add_message("system", content)

    def get_context_messages(self) -> List[Dict[str, Any]]:
        """Return the trimmed message list for LLM API calls."""
        result = []
        if self._system_prompt and self.keep_system:
            result.append({
                "role": "system",
                "content": self._system_prompt,
            })
        result.extend([
            {"role": m["role"], "content": m["content"]}
            for m in self._messages
        ])
        return result

    def clear(self):
        """Clear all messages and reset token count."""
        system_tokens = 0
        if self._system_prompt:
            system_tokens = self._estimate_tokens(self._system_prompt)
        self._messages = []
        self._total_tokens = system_tokens
        logger.info("[Context] Cleared. System: ~6000 tokens")

    def get_compression_snapshot(self) -> str:
        """Return a concise summary of the conversation for context injection."""
        if not self._messages:
            return "No prior conversation history."

        # Take last N messages, summarize them
        recent = self._messages[-self.keep_recent:]
        lines = []
        for m in recent:
            if m["role"] == "user":
                lines.append(f"Player ({m.get('speaker', 'unknown')}): {m['content'][:120]}")
            elif m["role"] == "assistant":
                lines.append(f"GM actions: {m['content'][:120]}")
            else:
                lines.append(f"GM: {m['content'][:120]}")

        return "Recent conversation:\n" + "\n".join(lines[-10:])

    def _estimate_tokens(self, text: str) -> int:
        """Rough token estimate. Qwen uses BPE ~4 chars/token."""
        if not text:
            return 0
        return len(text) // self.CHAR_TO_TOKEN

    def _trim(self):
        """Trim oldest messages to stay within token budget."""
        while self._total_tokens > self.max_tokens and len(self._messages) > 2:
            # Remove oldest non-system message
            removed = None
            for i, m in enumerate(self._messages):
                if m["role"] != "system":
                    removed = i
                    break

            if removed is None:
                # Only system messages left, can't trim further
                break

            msg = self._messages.pop(removed)
            self._total_tokens -= msg.get("tokens", 0)
            logger.info(f"[Context] Trimmed message ({msg['role']}), ~{msg.get('tokens', 0)} tokens removed")
