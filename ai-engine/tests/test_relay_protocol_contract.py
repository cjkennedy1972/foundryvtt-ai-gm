#!/usr/bin/env python3
"""Every RPC type FoundryClient sends must be one the relay will accept.

The relay validates the incoming `type` against ws.PendingRequestTypes and
replies {"type":"error","error":"Unknown message type: ..."} for anything
else (go-relay/internal/ws/client_api.go). FoundryClient._send raises on any
reply carrying an "error", so an unlisted type is not a degraded call — it is
an action that can never succeed.

Nothing checked this boundary. Python and Go are separate builds, the relay
is a submodule, and every test in this suite mocks the transport, so a typo
or a renamed endpoint shows up only as an action failing in play. The
`roll_initiative` comment in client.py records an earlier encounter with the
same failure ("The relay has no 'roll-initiative' message type ... instead of
erroring 'Unknown message type'"); four more had survived since.

This parses the Go source in the repo rather than restating the list, so a
relay upgrade that drops an endpoint fails here instead of in a session.

Run:
    cd ai-engine && python -m pytest tests/test_relay_protocol_contract.py -v
"""

import ast
import os
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REPO = Path(__file__).resolve().parents[2]
PENDING_GO = REPO / "relay/go-relay/internal/ws/pending.go"
CLIENT_PY = Path(__file__).resolve().parents[1] / "foundry/client.py"

# Handled before the PendingRequestTypes check in client_api.go, so they are
# valid despite being absent from the map.
HANDLED_BEFORE_VALIDATION = {
    "ping", "subscribe", "unsubscribe",
    "interactive-session-start", "interactive-input", "interactive-session-end",
}


def _relay_accepts() -> set:
    src = PENDING_GO.read_text()
    block = src.split("PendingRequestTypes = map[string]bool{", 1)[1].split("\n}", 1)[0]
    return set(re.findall(r'"([a-z0-9-]+)":\s*true', block))


def _client_sends() -> set:
    tree = ast.parse(CLIENT_PY.read_text())
    sent = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr not in ("_send", "_send_with_retry"):
            continue
        if node.args and isinstance(node.args[0], ast.Constant):
            sent.add(node.args[0].value)
    return sent


def test_the_relay_submodule_is_checked_out_in_ci():
    """Every other test in this file skips without it, quietly and on every
    interpreter. A local checkout may not have the submodule; CI must, or the
    contract it exists to hold is simply unverified."""
    if not os.environ.get("CI"):
        pytest.skip("local checkout; CI is where this has to hold")

    assert PENDING_GO.exists(), (
        f"{PENDING_GO} is missing, so every relay contract test in this file "
        "skipped. The checkout step needs `with: submodules: true`."
    )


@pytest.mark.skipif(not PENDING_GO.exists(), reason="relay submodule not checked out")
def test_the_relay_source_parses():
    """A parse that silently yields nothing would make the real test vacuous."""
    accepted = _relay_accepts()
    assert len(accepted) > 50, f"only parsed {len(accepted)} types out of {PENDING_GO}"
    assert "chat-send" in accepted


def test_the_client_source_parses():
    sent = _client_sends()
    assert len(sent) > 20, f"only found {len(sent)} _send call sites"
    assert "chat-send" in sent


# Types the client sends that the relay rejects. Each is an action that
# always fails, so each needs a decision rather than a rename; naming them
# here rather than tolerating them quietly is what kept them visible until
# they were resolved. Empty is the goal state — adding an entry needs a
# reason as good as the three that were here (see #178, #184).
KNOWN_BROKEN: dict = {}


@pytest.mark.skipif(not PENDING_GO.exists(), reason="relay submodule not checked out")
def test_no_new_rpc_type_the_relay_would_reject():
    unknown = sorted(
        _client_sends() - _relay_accepts() - HANDLED_BEFORE_VALIDATION - set(KNOWN_BROKEN)
    )

    assert unknown == [], (
        "the relay answers these with "
        '{"type":"error","error":"Unknown message type"}, and _send raises on '
        f"any reply carrying an error — so each is an action that always fails: {unknown}"
    )


@pytest.mark.skipif(not PENDING_GO.exists(), reason="relay submodule not checked out")
def test_the_known_broken_list_only_shrinks():
    """A waiver kept after its call site is fixed hides the next regression."""
    still_broken = _client_sends() - _relay_accepts() - HANDLED_BEFORE_VALIDATION

    stale = sorted(set(KNOWN_BROKEN) - still_broken)
    assert stale == [], f"these now work or are gone; drop them from KNOWN_BROKEN: {stale}"
