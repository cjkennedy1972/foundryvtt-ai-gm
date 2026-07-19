"""
LLM Manager — handles communication with the oMLX API.
"""

import asyncio
import time
from typing import List, Dict, Optional, AsyncGenerator, Any
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
        self._game_state: Any = None
        # Output-token reservation. GM action JSON is short; an oversized value
        # (was 8192) collides with small model context windows — prompt+max_tokens
        # exceeds n_ctx and the server 400s — and inflates the history-trim margin.
        self._max_tokens = settings.llm_max_output_tokens or 2048
        # Total context budget — driven by config so it tracks the model's real
        # window instead of a hardcoded value that could silently overflow it.
        self._max_history_tokens = settings.max_context_tokens or 50000
        self._current_scene = ""
        self._dynamic_npc_context = ""
        self._dynamic_world_context = ""
        self._dynamic_session_plan = ""
        self._dynamic_dm_reference = ""
        self._dynamic_character_hooks = ""
        self._custom_system_prompt: Optional[str] = None
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

        # Cached system prompt — rebuilt only when campaign context changes
        self._system_prompt_cache: Optional[str] = None
        self._active_modules: List[str] = []

        # Concurrency locks to prevent race conditions on shared state
        self._history_lock = asyncio.Lock()  # Protects _conversation_history and _turn_count

        # Rate limiting — serialises LLM calls and enforces a minimum inter-call gap
        self._rate_lock = asyncio.Lock()
        self._last_call_time: float = 0.0

    async def close(self):
        """Close the underlying HTTP client to avoid resource leaks.

        Call during application shutdown to properly close sockets.
        """
        try:
            await self._http.aclose()
        except Exception:
            pass

    def _build_anchor_facts(self) -> List[str]:
        """Retrieve the campaign-lore chunks most relevant to the current
        scene/NPCs via CampaignLoader.search_vault (BM25 over the vault),
        instead of a fixed truncated snippet of the world file.
        """
        if not self._campaign_loader:
            return []
        query = " ".join(filter(None, [self._current_scene, self._dynamic_npc_context[:300]]))
        if not query.strip():
            return []
        return self._campaign_loader.search_vault(query, max_results=5)

    def set_current_scene(self, scene_name: str) -> None:
        """Update the current scene and refresh anchor facts to match."""
        self._current_scene = scene_name or ""
        if self._reinforcer:
            self._reinforcer.anchor_facts = set(self._build_anchor_facts())

    async def update_context(self, npc_data=None, world_data=None):
        """Update the reinforcer's context from dynamic sources."""
        if npc_data:
            self._reinforcer.update_npc_summary(npc_data)
        if world_data:
            self._reinforcer.update_world_summary(world_data)
        # Update anchor facts
        self._reinforcer.anchor_facts = set(self._build_anchor_facts())

    def set_dynamic_npc_context(self, context: str) -> None:
        self._dynamic_npc_context = context
        self._system_prompt_cache = None

    def set_dynamic_world_context(self, context: str) -> None:
        self._dynamic_world_context = context
        self._system_prompt_cache = None

    @property
    def conversation_history(self) -> List[Dict]:
        return list(self._conversation_history)

    @property
    def system_prompt(self) -> str:
        """Build and return the system prompt from loaded context.

        Result is cached after first build. Call invalidate_system_prompt()
        whenever campaign context changes (e.g. new campaign loaded).
        """
        if self._system_prompt_cache is not None:
            return self._system_prompt_cache
        if self._custom_system_prompt is not None:
            self._system_prompt_cache = self._custom_system_prompt
            return self._system_prompt_cache
        npc_context = ""
        world_context = ""
        if self._campaign_loader:
            npc_context = self._campaign_loader.get_npc_context_sync() or ""
            world_context = self._campaign_loader.get_world_context_sync() or ""
        self._system_prompt_cache = build_system_prompt(
            game_state="",
            npc_context=npc_context,
            world_context=world_context,
            custom_tone=settings.ai_tone,
            active_modules=getattr(self, "_active_modules", None),
        )
        return self._system_prompt_cache

    def invalidate_system_prompt(self):
        """Force the system prompt to be rebuilt on the next call."""
        self._system_prompt_cache = None

    def set_active_modules(self, modules: List[str]) -> None:
        """Update the active module list and invalidate the prompt cache."""
        self._active_modules = modules or []
        self._system_prompt_cache = None

    def set_system_prompt(self, prompt: str):
        """Allow the caller to override the system prompt with custom context."""
        self._custom_system_prompt = prompt
        self._system_prompt_cache = prompt

    def set_dynamic_session_plan(self, session_plan: str) -> None:
        self._dynamic_session_plan = session_plan or ""
        self._system_prompt_cache = None

    def set_dynamic_dm_reference(self, dm_reference: str) -> None:
        self._dynamic_dm_reference = dm_reference or ""
        self._system_prompt_cache = None

    def set_dynamic_character_hooks(self, character_hooks: str) -> None:
        self._dynamic_character_hooks = character_hooks or ""
        self._system_prompt_cache = None

    def _build_prompt_messages(
        self,
        user_message: str,
        game_state_summary: str = "",
        extra_context: str = "",
        include_history: bool = True,
        include_reinforcement: bool = True,
    ) -> List[Dict[str, Any]]:
        messages: List[Dict[str, Any]] = [{"role": "system", "content": self.system_prompt}]

        if game_state_summary:
            messages.append({
                "role": "system",
                "content": f"CURRENT GAME STATE:\n{game_state_summary}",
            })

        if extra_context:
            messages.append({
                "role": "system",
                "content": f"ADDITIONAL CONTEXT:\n{extra_context}",
            })

        if include_reinforcement and self._reinforcer:
            self._turn_count += 1
            if self._turn_count % 3 == 0:
                active_state = {}
                if hasattr(self, '_game_state') and self._game_state:
                    active_state = self._game_state.to_dict() if hasattr(self._game_state, 'to_dict') else self._game_state
                reinforcement = self._reinforcer.get_reinforcement(
                    active_state=active_state,
                    extra_context=extra_context,
                )
                if reinforcement:
                    messages.append({"role": "system", "content": reinforcement})
                    logger.info(f"[Context] Reinforcement injected (turn #{self._turn_count})")

        if include_history:
            self._trim_history()
            messages.extend(self._conversation_history)

        messages.append({"role": "user", "content": user_message})
        return messages

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

    async def _acquire_rate_limit(self) -> None:
        """Serialises LLM callers and enforces a minimum inter-call gap."""
        min_interval = settings.llm_min_call_interval
        async with self._rate_lock:
            if min_interval > 0:
                now = time.monotonic()
                wait = min_interval - (now - self._last_call_time)
                if wait > 0:
                    await asyncio.sleep(wait)
            self._last_call_time = time.monotonic()

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
        await self._acquire_rate_limit()
        # _history_lock is held across the full snapshot → HTTP call → write cycle
        # (fixes #1: trim before snapshot; fixes #5: prevents concurrent calls from
        # interleaving history pairs; fixes #8: fallback appends to keep context coherent)
        async with self._history_lock:
            messages = self._build_prompt_messages(
                user_message=user_message,
                game_state_summary=game_state_summary,
                extra_context=extra_context,
            )

            start_time = time.perf_counter()
            # Up to 2 attempts: a local model occasionally emits prose or truncated
            # JSON. Rather than dropping the player's turn silently, retry once with a
            # strict corrective nudge, then fall back to a neutral narration so the
            # table always gets *something* back.
            attempt_messages = messages
            last_parse_error = None
            for attempt in range(2):
                try:
                    payload = {
                        "model": self.model,
                        "messages": attempt_messages,
                        "temperature": self._temperature,
                        "max_tokens": self._max_tokens,
                        "top_p": 0.9,
                    }
                    resp = await self._http.post(self._endpoint_url, json=payload, timeout=120)
                    resp.raise_for_status()
                    data = resp.json()
                    content = data["choices"][0]["message"]["content"]
                except Exception as e:
                    # Network/HTTP/transport failure — suppress duplicate spam then raise.
                    import time as _time
                    now = _time.time()
                    error_key = type(e).__name__
                    if getattr(self, '_last_error_key', None) == error_key and \
                            now - self._last_error_time < self._error_suppress_seconds:
                        logger.debug(f"Suppressed duplicate LLM error: {e}")
                        raise
                    self._last_error_key = error_key
                    self._last_error_time = now
                    # Surface the server's response body — a 400 from a local
                    # OpenAI server almost always says exactly why (e.g. context
                    # length exceeded), which a bare HTTPStatusError hides.
                    body = ""
                    _resp = getattr(e, "response", None)
                    if _resp is not None:
                        try:
                            body = f" | body: {_resp.text[:300]}"
                        except Exception:
                            pass
                    logger.error(f"LLM generation failed: {e}{body}", exc_info=True)
                    raise

                # Extract JSON from response. Qwen3.6 may prepend thinking text before
                # the JSON object. Use balanced-brace counting to find the complete JSON.
                try:
                    json_str = self._extract_json(content)
                    result = json.loads(json_str)
                except (ValueError, json.JSONDecodeError) as e:
                    last_parse_error = e
                    logger.warning(f"LLM response not parseable (attempt {attempt + 1}/2): {e}")
                    # Role "user", not "system": oMLX rejects a system message
                    # that isn't first in the list with 400, which turned this
                    # retry into a hard failure (lost the session opening).
                    attempt_messages = messages + [{
                        "role": "user",
                        "content": (
                            "[SYSTEM] Your previous reply was not valid JSON. Respond with ONLY a single "
                            "JSON object containing an \"actions\" array — no prose, no code fences."
                        ),
                    }]
                    continue

                # Append to history (already holding _history_lock)
                self._conversation_history.append({"role": "user", "content": user_message})
                self._conversation_history.append({"role": "assistant", "content": json_str})
                self._trim_history()

                # Record turn in reinforcer for periodic summarization
                if self._reinforcer:
                    self._reinforcer.record_turn(user_message, json_str)

                elapsed = time.perf_counter() - start_time
                logger.info(
                    f"[LLM] generate completed in {elapsed:.2f}s; "
                    f"actions={len(result.get('actions', []))}"
                )
                return result

            # Both attempts failed — record the exchange so LLM context stays
            # aligned with the DB (caller still saves user_message to DB).
            fallback_text = "The GM pauses a moment, gathering the threads of the tale…"
            self._conversation_history.append({"role": "user", "content": user_message})
            self._conversation_history.append({
                "role": "assistant",
                "content": json.dumps({"actions": [{"type": "narrate", "text": fallback_text}]})
            })
            self._trim_history()
            elapsed = time.perf_counter() - start_time
            logger.error(
                f"LLM produced no parseable JSON after retries in {elapsed:.2f}s: {last_parse_error}"
            )
            return {
                "actions": [{
                    "type": "narrate",
                    "text": fallback_text,
                }]
            }

    async def generate_text(
        self,
        user_message: str,
        system_prompt: str = "You are a helpful AI Game Master assistant. Answer the GM's questions directly and conversationally.",
        context: str = ""
    ) -> str:
        """Send a message and return plain text — no JSON parsing, no tool execution.

        Use for direct GM chat where we want a conversational response, not game actions.
        """
        await self._acquire_rate_limit()
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
        await self._acquire_rate_limit()
        async with self._history_lock:
            messages = self._build_prompt_messages(
                user_message=user_message,
                game_state_summary=game_state_summary,
                extra_context=extra_context,
                include_reinforcement=False,
            )

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

            # Store extracted JSON in history (strip thinking text), guarding the
            # shared history against concurrent access from generate().
            clean_content = self._extract_json(full_content)
            async with self._history_lock:
                self._conversation_history.append({"role": "user", "content": user_message})
                self._conversation_history.append({"role": "assistant", "content": clean_content})
                self._trim_history()

        except Exception as e:
            logger.error(f"LLM streaming failed: {e}", exc_info=True)
            raise

    def _extract_json(self, text: str) -> str:
        """Extract a valid JSON object from the response text.
        
        Qwen3.6 may prepend chain-of-thought reasoning before the JSON,
        and may wrap the JSON in ```json...``` code blocks.
        Uses balanced-brace counting to find the complete JSON block.
        Falls back to the last successfully parseable brace block.
        """
        import re

        # Find every fenced block (```json, ```JSON, or a bare ```) and try
        # each as JSON, most recent first — the language tag is optional so
        # this one pattern covers both labeled and unlabeled fences.
        fence_re = re.compile(r'\x60\x60\x60(?:json|JSON)?\s*\n(.*?)\n\x60\x60\x60', re.DOTALL)
        blocks = list(fence_re.finditer(text))
        for m in reversed(blocks):
            candidate = m.group(1).strip()
            try:
                json.loads(candidate)
                return candidate
            except json.JSONDecodeError:
                pass

        # Fall back: balanced-brace counting on text with the same fenced
        # blocks removed, so that thinking text before a missed/malformed
        # block doesn't corrupt the brace count.
        clean = fence_re.sub('', text)
        
        brace_blocks = []
        i = 0
        n = len(clean)
        while i < n:
            if clean[i] == '{':
                depth = 0
                start = i
                in_string = False
                escaped = False
                while i < n:
                    ch = clean[i]
                    if in_string:
                        if escaped:
                            escaped = False
                        elif ch == '\\':
                            escaped = True
                        elif ch == '"':
                            in_string = False
                    else:
                        if ch == '"':
                            in_string = True
                        elif ch == '{':
                            depth += 1
                        elif ch == '}':
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
