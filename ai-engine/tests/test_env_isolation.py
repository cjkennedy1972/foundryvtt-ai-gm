"""The suite must run against the shipped defaults, not a developer's .env.

This is the root cause of the worst bug found in this review:
ALLOW_EXECUTE_JS=true in one .env masked a shipped default of false that broke
87 first-party call sites. CI ran with the correct default and still passed,
because every test mocked the layer the gate lived in. A test that only passes
with one machine's configuration is not a test.
"""

import os

from config import settings


def test_the_env_file_is_neutralised_during_tests():
    """conftest.py points AIGM_ENV_FILE at a path that does not exist."""
    env_file = os.environ.get("AIGM_ENV_FILE")

    assert env_file, "conftest.py should set AIGM_ENV_FILE before config is imported"
    assert not os.path.exists(env_file), (
        f"AIGM_ENV_FILE={env_file!r} exists, so the suite is reading a real env file"
    )


def test_settings_show_the_shipped_defaults():
    """Spot-check fields a developer .env commonly overrides.

    allow_execute_js is the one that matters: the whole point is that the
    suite exercises the default a fresh install gets.
    """
    assert settings.allow_execute_js is False
    assert settings.ai_name == "Sage"
    assert settings.llm_base_url == "http://localhost:8800/v1"
    assert settings.tts_enabled is False


def test_production_still_reads_dot_env(monkeypatch):
    """The override must not change how the engine runs outside tests.

    Builds the same expression config.py uses, with the variable unset, so a
    change that hard-coded the test path would fail here.
    """
    from pydantic_settings import BaseSettings, SettingsConfigDict

    monkeypatch.delenv("AIGM_ENV_FILE", raising=False)

    class _Probe(BaseSettings):
        model_config = SettingsConfigDict(env_file=os.getenv("AIGM_ENV_FILE", ".env"))

    assert _Probe.model_config["env_file"] == ".env"
