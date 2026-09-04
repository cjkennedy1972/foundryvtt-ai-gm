"""Token usage accounting and enforcement for unattended LLM calls."""

from dataclasses import dataclass
from typing import Awaitable, Callable, Optional


class TokenBudgetExceeded(RuntimeError):
    """Raised before a call that would exceed the configured token budget."""

    def __init__(self, scope: str, used: int, requested: int, budget: int):
        self.scope, self.used, self.requested, self.budget = scope, used, requested, budget
        super().__init__(f"{scope} token budget exhausted ({used + requested:,}/{budget:,} tokens)")


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int
    completion_tokens: int

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class TokenUsage:
    """Coordinates an atomic preflight check and durable usage recording."""

    def __init__(self, db, budget: int = 0,
                 on_exhausted: Optional[Callable[[TokenBudgetExceeded], Awaitable[None]]] = None):
        self.db = db
        self.budget = max(0, int(budget))
        self.on_exhausted = on_exhausted

    async def before_call(self, session_id: Optional[str], campaign: str, requested: int) -> None:
        if not self.budget or not session_id:
            return
        used = await self.db.get_llm_usage_total(session_id=session_id)
        if used + requested > self.budget:
            error = TokenBudgetExceeded("session", used, requested, self.budget)
            if self.on_exhausted:
                await self.on_exhausted(error)
            raise error

    async def budget_available(self, session_id: Optional[str]) -> bool:
        """Return whether any budget remains, without reserving a call."""
        if not self.budget or not session_id:
            return True
        used = await self.db.get_llm_usage_total(session_id=session_id)
        return used < self.budget

    async def record(self, session_id: Optional[str], campaign: str, usage: Usage,
                     model: str, call_type: str = "chat") -> None:
        if session_id:
            await self.db.record_llm_usage(
                session_id, campaign, usage.prompt_tokens, usage.completion_tokens,
                model, call_type,
            )
