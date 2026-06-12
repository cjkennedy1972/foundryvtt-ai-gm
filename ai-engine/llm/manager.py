"""
LLM Manager — handles communication with the oMLX API.
"""

from typing import List, Dict, Optional, AsyncGenerator
import json
import logging
import re
import httpx

from config import settings
from llm.system_prompts import build_system_prompt
from context.reinforcer import ContextReinforcer

logger = logging.getLogger(__name__)


class LLMManager:
    def __init__(self, campaign_loader=None):
        # Build endpoint URL with ?thinking=false query param (required for oMLX)
        base = settings.llm_base_url.rstrip("/")
        self._endpoint_url = f"{base}/chat/completions?thinking=false"

        self._http = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {settings.llm_api_key}"}
        )
        self.model = settings.model or "mlx-model"
        self._conversation_history: List[Dict] = []
        self._temperature = settings.temperature
        self._ai_tone = settings.ai_tone
        self._campaign_loader = campaign_loader
        self._max_tokens = 8192
        self._max_history_tokens = 60000  # Leave room for system prompt
        self._dynamic_npc_context = ""
        self._dynamic_world_context = ""
        self._dynamic_session_plan = ""
        self._dynamic_dm_reference = ""
        self._dynamic_character_hooks = ""
        # Deduplication of parse-failure chat spam — only report once per window
        self._last_error_time = 0.0
        self._error_suppress_seconds = 30  # suppress duplicate errors within 30s

        # Context reinforcement — prevents drift in long sessions
        self._reinforcer = ContextReinforcer(
            anchor_facts=self._build_anchor_facts(),
            npc_summary="",
            world_summary="",
            summarize_every_n_pairs=settings.context_reinforce_interval or 10,
        )
        self._turn_count = 0  # Track turns for reinforcement

    def _build_anchor_facts(self) -> List[str]:
        """Build the set of immutable anchor facts from campaign loader."""
        facts = []
        if self._campaign_loader:
            world = self._campaign_loader.get_world_context_sync()
            if world:
                # Extract first few key facts from world context
                facts.append(world.split("\n")[0])
            if self._dynamic_npc_context:
                for line in self._dynamic_npc_context.split("\n")[:5]:
                    if line.strip():
                        facts.append(line.strip())
        return facts

    async def update_context(self, npc_data=None, world_data=None):
        """Update the reinforcer's context from dynamic sources."""
        if npc_data:
            self._reinforcer.update_npc_summary(npc_data)
        if world_data:
            self._reinforcer.update_world_summary(world_data)
        # Update anchor facts
        self._reinforcer.anchor_facts = set(self._build_anchor_facts())

    @property
    def conversation_history(self) -> List[Dict]:
        return self._conversation_history

    @property
    def system_prompt(self) -> str:
        """Build and return the system prompt from loaded context."""
        npc_context = ""
        world_context = ""
        if self._campaign_loader:
            npc_context = self._campaign_loader.get_npc_context_sync() or ""
            world_context = self._campaign_loader.get_world_context_sync() or ""
        return build_system_prompt(
            game_state="",
            npc_context=npc_context,
            world_context=world_context,
            custom_tone=settings.ai_tone
        )

    def set_system_prompt(self, prompt: str):
        """Allow the caller to override the system prompt with custom context."""
        self._custom_system_prompt = prompt

    def _estimate_tokens(self, text: str) -> int:
        """Rough token estimate: ~4 chars per token."""
        return len(text) // 4

    def _trim_history(self):
        """Trim conversation history to stay within token limits.

        Walks backwards from the newest message so that when the budget is
        exceeded, the OLDEST messages are dropped and recent context is kept.
        """
        budget = self._max_history_tokens - self._max_tokens
        running_total = self._estimate_tokens(self.system_prompt)
        messages_to_keep = []
        for msg in reversed(self._conversation_history):
            msg_tokens = self._estimate_tokens(msg.get("content", ""))
            if running_total + msg_tokens > budget:
                break
            messages_to_keep.append(msg)
            running_total += msg_tokens
        messages_to_keep.reverse()
        self._conversation_history = messages_to_keep

    async def generate(
        self,
        user_message: str,
        game_state_summary: str = "",
        extra_context: str = ""
    ) -> Dict:
        """
        Send a user message to the LLM and return the parsed action response.

        Args:
            user_message: The player's message or input
            game_state_summary: Current game state for context
            extra_context: Additional context (NPC info, scene details, etc.)

        Returns:
            Dict with 'actions' key containing list of action dicts
        """
        # Build messages
        messages = [
            {"role": "system", "content": self.system_prompt}
        ]

        if game_state_summary:
            messages.append({
                "role": "system",
                "content": f"CURRENT GAME STATE:\n{game_state_summary}"
            })

        if extra_context:
            messages.append({
                "role": "system",
                "content": f"ADDITIONAL CONTEXT:\n{extra_context}"
            })

        # Inject dynamic campaign context if available
        if self._campaign_loader:
            if self._dynamic_npc_context:
                messages.append({
                    "role": "system",
                    "content": f"DYNAMIC NPC CONTEXT:\n{self._dynamic_npc_context}"
                })
            if self._dynamic_session_plan:
                messages.append({
                    "role": "system",
                    "content": f"SESSION PLAN:\n{self._dynamic_session_plan}"
                })

        # Inject context reinforcement anchors to prevent drift
        # This re-anchors the LLM to core facts every N turns
        if self._reinforcer:
            self._turn_count += 1
            if self._turn_count % 3 == 0:  # Reinforce every 3rd call
                # Build a compact state block from the tracker if available
                active_state = {}
                if hasattr(self, '_game_state') and self._game_state:
                    active_state = self._game_state.to_dict() if hasattr(self._game_state, 'to_dict') else self._game_state
                reinforcement = self._reinforcer.get_reinforcement(
                    active_state=active_state,
                    extra_context=extra_context,
                )
                if reinforcement:
                    messages.insert(1, {
                        "role": "system",
                        "content": reinforcement
                    })
                    logger.info(f"[Context] Reinforcement injected (turn #{self._turn_count})")
                    # Record the turn in the reinforcer for summarization
                    self._reinforcer._message_count += 1

        # Add conversation history
        messages.extend(self._conversation_history)

        # Add current user message
        messages.append({"role": "user", "content": user_message})

        # Trim history if needed
        self._trim_history()

        try:
            payload = {
                "model": self.model,
                "messages": messages,
                "temperature": self._temperature,
                "max_tokens": self._max_tokens,
                "top_p": 0.9,
            }
            # Add thinking=false via extra_body (works with some oMLX servers)
            resp = await self._http.post(self._endpoint_url, json=payload, timeout=120)
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"]

            # Extract JSON from response. Qwen3.6 may prepend thinking text before
            # the JSON object. Use balanced-brace counting to find the complete JSON.
            json_str = self._extract_json(content)
            result = json.loads(json_str)

            # Store extracted JSON in conversation history
            self._conversation_history.append({
                "role": "user",
                "content": user_message
            })
            self._conversation_history.append({
                "role": "assistant",
                "content": json_str
            })

            # Trim after adding
            self._trim_history()

            logger.info(f"LLM generated {len(result.get('actions', []))} actions")
            return result

        except Exception as e:
            # Suppress repeated parse-failure spam — only report once per suppression window
            import time as _time
            now = _time.time()
            error_key = type(e).__name__
            if hasattr(self, '_last_error_key') and self._last_error_key == error_key:
                if now - self._last_error_time < self._error_suppress_seconds:
                    logger.debug(f"Suppressed duplicate LLM error: {e}")
                    raise
            self._last_error_key = error_key
            self._last_error_time = now
            logger.error(f"LLM generation failed: {e}")
            raise

    async def generate_stream(
        self,
        user_message: str,
        game_state_summary: str = "",
        extra_context: str = ""
    ) -> AsyncGenerator[str, None]:
        """Stream the LLM response token by token."""
        messages = [
            {"role": "system", "content": self.system_prompt}
        ]

        if game_state_summary:
            messages.append({
                "role": "system",
                "content": f"CURRENT GAME STATE:\n{game_state_summary}"
            })

        if extra_context:
            messages.append({
                "role": "system",
                "content": f"ADDITIONAL CONTEXT:\n{extra_context}"
            })

        messages.extend(self._conversation_history)
        messages.append({"role": "user", "content": user_message})

        self._trim_history()

        try:
            payload = {
                "model": self.model,
                "messages": messages,
                "temperature": self._temperature,
                "max_tokens": self._max_tokens,
                "stream": True,
            }
            async with self._http.stream("POST", self._endpoint_url, json=payload, timeout=300) as resp:
                resp.raise_for_status()
                full_content = ""
                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    data_str = line[6:]  # Strip "data: " prefix
                    if data_str.strip() == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                        delta = chunk["choices"][0]["delta"]
                        if delta.get("content"):
                            full_content += delta["content"]
                            yield delta["content"]
                    except (json.JSONDecodeError, KeyError):
                        continue

            # Store extracted JSON in history (strip thinking text)
            clean_content = self._extract_json(full_content)
            self._conversation_history.append({"role": "user", "content": user_message})
            self._conversation_history.append({"role": "assistant", "content": clean_content})
            self._trim_history()

        except Exception as e:
            logger.error(f"LLM streaming failed: {e}")
            raise

    def _extract_json(self, text: str) -> str:
        """Extract a valid JSON object from the response text.
        
        Qwen3.6 may prepend chain-of-thought reasoning before the JSON,
        and may wrap the JSON in ```json...``` code blocks.
        Uses balanced-brace counting to find the complete JSON block.
        Falls back to the last successfully parseable brace block.
        """
        # First, strip out ```json ... ``` code blocks and extract their content
        import re
        clean = text
        
        # Find all ```json or ```code blocks and extract their content
        blocks = list(re.finditer(r'\x60\x60\x60(?:json|JSON)?\s*\n(.*?)\n\x60\x60\x60', text, re.DOTALL))
        if blocks:
            # Try each block in reverse order, keeping only ones that parse
            for m in reversed(blocks):
                candidate = m.group(1).strip()
                try:
                    json.loads(candidate)
                    return candidate
                except json.JSONDecodeError:
                    pass

        # Fall back: balanced-brace counting in raw text
        brace_blocks = []
        i = 0
        while i < len(clean):
            if clean[i] == '{':
                depth = 0
                start = i
                while i < len(clean):
                    if clean[i] == '{':
                        depth += 1
                    elif clean[i] == '}':
                        depth -= 1
                        if depth == 0:
                            break
                    i += 1
                if depth == 0 and i > start:
                    brace_blocks.append((start, i + 1))
            i += 1
        
        # Try each brace block in reverse order, keeping only ones that parse
        for start, end in reversed(brace_blocks):
            candidate = clean[start:end]
            try:
                json.loads(candidate)
                return candidate
            except json.JSONDecodeError:
                continue
        
        raise ValueError(f"No valid JSON object found in response (len={len(text)})")
