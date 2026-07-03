"""Checks for the shared upload-path resolution helper (was duplicated 5x)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from campaign.orchestrator import resolve_uploaded_path


def test_prefers_relay_reported_path_and_unquotes_it():
    assert resolve_uploaded_path({"path": "ai-gm-maps/the%20crypt/map.png"}, "fallback") \
        == "ai-gm-maps/the crypt/map.png"


def test_falls_back_when_path_missing_or_response_malformed():
    assert resolve_uploaded_path({"path": ""}, "fallback") == "fallback"
    assert resolve_uploaded_path({}, "fallback") == "fallback"
    assert resolve_uploaded_path(None, "fallback") == "fallback"
    assert resolve_uploaded_path("not-a-dict", "fallback") == "fallback"
