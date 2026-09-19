#!/usr/bin/env python3
"""A corrupt relay credentials file bricked pairing with no way out.

The setup wizard covered in #190 rests entirely on RelayManager, which the
route tests mock. _load_credentials is the piece the whole first run turns
on: it holds the relay admin login and, once paired, the api_key the Foundry
module was paired under.

It parsed the file with a bare json.loads. A truncated or hand-edited file
raised JSONDecodeError straight through GET /api/setup/pairing-code, which
answers 500 with the exception type and nothing else — so the wizard could
not show a pairing code, and nothing in the UI could repair it.

Regenerating blindly would be worse: it would discard the api_key the world
was paired under. So a file that parses but has lost its key must be left
alone, and only an unparseable one is replaced, after keeping a copy.

Round-tripping, password rules and 0600 permissions are covered in
test_relay_manager.py; this is the recovery path.

Run:
    cd ai-engine && python -m pytest tests/test_relay_credentials_recovery.py -v
"""

import json
import os
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from relay_proc.manager import RelayManager


@pytest.fixture
def manager(tmp_path):
    m = RelayManager()
    m.data_dir = tmp_path
    m._credentials_path = tmp_path / "aigm-credentials.json"
    return m


def test_a_healthy_file_is_returned_unchanged(manager):
    saved = {"email": "gm@example.com", "password": "Secret123", "api_key": "paired-key"}
    manager._credentials_path.write_text(json.dumps(saved))

    assert manager._load_credentials() == saved


def test_a_corrupt_file_does_not_raise(manager):
    """This reached GET /api/setup/pairing-code as an opaque 500."""
    manager._credentials_path.write_text('{"email": "gm@exam')

    creds = manager._load_credentials()

    assert creds.get("email") and creds.get("password")


def test_a_corrupt_file_is_kept_before_being_replaced(manager):
    """The api_key in it may be the only record of how the world was paired."""
    manager._credentials_path.write_text('{"api_key": "the-only-copy"')

    manager._load_credentials()

    backups = list(manager.data_dir.glob("aigm-credentials.json.corrupt*"))
    assert backups, "the unreadable file was destroyed"
    assert "the-only-copy" in backups[0].read_text()


def test_the_replacement_is_not_world_readable(manager):
    manager._credentials_path.write_text("}{")

    manager._load_credentials()

    mode = stat.S_IMODE(manager._credentials_path.stat().st_mode)
    assert mode == 0o600, oct(mode)


def test_a_file_that_parses_but_has_no_api_key_is_left_alone(manager):
    """Pre-pairing this is the normal state; regenerating would change the
    password the relay dashboard is expecting."""
    saved = {"email": "gm@example.com", "password": "Secret123"}
    manager._credentials_path.write_text(json.dumps(saved))

    assert manager._load_credentials() == saved
    assert list(manager.data_dir.glob("*.corrupt*")) == []


def test_a_json_scalar_is_treated_as_corrupt(manager):
    """Valid JSON, wrong shape: .get() on it would raise later instead."""
    manager._credentials_path.write_text("42")

    creds = manager._load_credentials()

    assert isinstance(creds, dict)
    assert creds.get("password")


def test_the_recovered_file_is_readable_next_time(manager):
    manager._credentials_path.write_text("not json at all")

    first = manager._load_credentials()
    second = manager._load_credentials()

    assert first == second, "pairing would change on every read"
