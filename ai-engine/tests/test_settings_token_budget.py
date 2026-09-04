"""Regression coverage for runtime token-budget settings updates."""

from types import SimpleNamespace

import pytest

from api.routes.session import GMSettings, update_settings
from config import settings


@pytest.mark.asyncio
async def test_settings_update_without_budget_preserves_live_budget():
    """Unrelated settings changes must not restore the startup budget."""
    original_model = settings.model
    original_budget = settings.llm_token_budget
    try:
        settings.model = "startup-model"
        settings.llm_token_budget = 100_000
        token_usage = SimpleNamespace(budget=1_234)
        state = SimpleNamespace(
            llm_manager=None,
            foundry_client=None,
            token_usage=token_usage,
        )

        request = GMSettings(model="runtime-model")
        await update_settings(request, state)

        assert settings.llm_token_budget == 100_000
        assert token_usage.budget == 1_234
    finally:
        settings.model = original_model
        settings.llm_token_budget = original_budget


@pytest.mark.asyncio
async def test_settings_update_accepts_explicit_zero_budget():
    """Zero is an explicit value that disables the budget cap."""
    original_budget = settings.llm_token_budget
    try:
        settings.llm_token_budget = 100_000
        token_usage = SimpleNamespace(budget=1_234)
        state = SimpleNamespace(
            llm_manager=None,
            foundry_client=None,
            token_usage=token_usage,
        )

        await update_settings(GMSettings(llm_token_budget=0), state)

        assert settings.llm_token_budget == 0
        assert token_usage.budget == 0
    finally:
        settings.llm_token_budget = original_budget
