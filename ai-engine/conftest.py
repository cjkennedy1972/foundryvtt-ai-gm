"""Pytest environment, applied before config is imported.

The suite runs against the shipped defaults, not the developer's .env.
config.Settings reads env_file=os.getenv("AIGM_ENV_FILE", ".env"), so pointing
that at a path which does not exist makes pydantic-settings skip file loading
entirely.

This matters because the difference between a local .env and the defaults hid
a real bug: ALLOW_EXECUTE_JS=true locally masked a shipped default of false
that broke 87 first-party call sites, and CI could not catch it because every
test mocked the layer the gate lived in. A test that only passes with one
developer's configuration is not a test.
"""

import os

os.environ["AIGM_ENV_FILE"] = ".env.pytest-does-not-exist"
os.environ.setdefault("MODEL", "pytest-placeholder")
