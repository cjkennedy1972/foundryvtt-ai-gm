"""
LLM Manager — handles communication with the oMLX API.
"""

import asyncio
from typing import List, Dict, Optional, AsyncGenerator
import json
import logging
import re
import httpx

from config import settings
from llm.system_prompts import build_system_prompt
from context.reinforcer import ContextReinforcer
from utils.token_counter import estimate_tokens, estimate_message_tokens, trim_messages_to_budget

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

        # Concurrency locks to prevent race conditions on shared state
        self._history_lock = asyncio.Lock()  # Protects _conversation_history and _turn_count

    async def close(self):
        """Close the underlying HTTP client to avoid resource leaks.

        Call during application shutdown to properly close sockets.
        """
        try:
            await self._http.aclose()
        except Exception:
            pass

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

    def _trim_history(self):
        """Trim conversation history to stay within token limits.

        Uses the centralized token counter to estimate tokens consistently.
        Keeps the most recent messages within the available budget.

        Available budget = max_context - max_output - system_prompt - 500 (safety margin)
        """
        # Calculate available budget for conversation history
        system_prompt_tokens = estimate_tokens(self.system_prompt) + 50  # 50 for framing
        budget = self._max_history_tokens - self._max_tokens - system_prompt_tokens - 500

        if budget <= 0:
            # If system prompt alone exceeds budget, keep last 2 messages only
            if len(self._conversation_history) > 2:
                self._conversation_history = self._conversation_history[-2:]
            return

        # Trim conversation history to fit budget
        # This uses the centralized trim_messages_to_budget function
        self._conversation_history = trim_messages_to_budget(
            self._conversation_history,
            budget,
            always_keep_system=False
        )

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
        async with self._history_lock:
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

            # Add conversation history (protected by lock)
            messages.extend(self._conversation_history)

            # Trim history if needed (protected by lock)
            self._trim_history()

        # Add current user message (outside lock - no shared state)
        messages.append({"role": "user", "content": user_message})

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

            # Store extracted JSON in conversation history (protected by lock)
            async with self._history_lock:
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

            # Record turn in reinforcer for periodic summarization
            if self._reinforcer:
                self._reinforcer.record_turn(user_message, json_str)

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

    async def generate_text(
        self,
        user_message: str,
        system_prompt: str = "You are a helpful AI Game Master assistant. Answer the GM's questions directly and conversationally.",
        context: str = ""
    ) -> str:
        """Send a message and return plain text — no JSON parsing, no tool execution.

        Use for direct GM chat where we want a conversational response, not game actions.
        """
        messages = [{"role": "system", "content": system_prompt}]
        if context:
            messages.append({"role": "system", "content": context})
        messages.append({"role": "user", "content": user_message})

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self._temperature,
            "max_tokens": self._max_tokens,
            "top_p": 0.9,
        }
        resp = await self._http.post(self._endpoint_url, json=payload, timeout=120)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()

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
