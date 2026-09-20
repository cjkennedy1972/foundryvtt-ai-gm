#!/usr/bin/env python3
"""The GM could not apply a condition to anything.

Against a live Foundry v13 world, four of the client's actor RPCs came back
with an error from the REST module:

    add_effect(prone)          FAIL Foundry error [add-effect]: uuid is required
    remove_effect(prone)       FAIL Foundry error [remove-effect]: uuid is required
    apply_condition(poisoned)  FAIL Foundry error [add-effect]: uuid is required
    decrease_attribute hp      FAIL Foundry error [decrease]: UUID or selected is required
    increase_attribute hp      FAIL Foundry error [increase]: UUID or selected is required

The module reads `data.uuid` (effects.ts, entity.ts). The client sent
`actor_uuid` for the effect calls and `actorUuid` for the attribute ones.
Neither is read, so every condition the GM applied — poisoned, prone,
stunned, frightened — silently failed, and so did every HP change made by
attribute path.

The parameter name is NOT uniform across the relay's surface, which is why
this is per-endpoint rather than a rename: short-rest, long-rest and
break-concentration really do take `actorUuid`, confirmed against the same
live world, and are left alone.

test_relay_protocol_contract cannot catch this. "add-effect" is a message
type the relay genuinely accepts; the drift is one level down, in the
parameters.

Run:
    cd ai-engine && python -m pytest tests/test_effect_rpc_params.py -v
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from foundry.client import FoundryClient

UUID = "Actor.AY2EL1oqkk2K7ZHj"


def _client():
    sent = []

    async def _send(msg_type, **kwargs):
        sent.append((msg_type, kwargs))
        return {"data": {}}

    c = FoundryClient()
    c._send = _send
    c._sent = sent
    return c


def _call(fn):
    c = _client()
    asyncio.run(fn(c))
    return c._sent[-1]


# ── the module reads data.uuid ────────────────────────────────────────────

def test_add_effect_names_the_actor_uuid():
    msg, kw = _call(lambda c: c.add_effect(UUID, "prone"))

    assert msg == "add-effect"
    assert kw["uuid"] == UUID
    assert "actor_uuid" not in kw, "the module does not read actor_uuid"


def test_remove_effect_names_the_actor_uuid():
    msg, kw = _call(lambda c: c.remove_effect(UUID, "prone"))

    assert msg == "remove-effect"
    assert kw["uuid"] == UUID
    assert "actor_uuid" not in kw


def test_apply_condition_goes_through_add_effect():
    """It carried its own copy of the payload, with its own spelling, and
    kept failing after add_effect was fixed."""
    msg, kw = _call(lambda c: c.apply_condition(UUID, "Poisoned"))

    assert msg == "add-effect"
    assert kw["uuid"] == UUID
    assert kw["statusId"] == "poisoned", "the status id should be lowercased"


def test_apply_condition_passes_a_duration_through():
    msg, kw = _call(lambda c: c.apply_condition(UUID, "poisoned", "1 minute"))

    assert kw["duration"] == "1 minute"


def test_decrease_attribute_names_the_actor_uuid():
    msg, kw = _call(
        lambda c: c.decrease_attribute("system.attributes.hp.value", 5, UUID)
    )

    assert msg == "decrease"
    assert kw["uuid"] == UUID
    assert "actorUuid" not in kw
    assert kw["attribute"] == "system.attributes.hp.value" and kw["amount"] == 5


def test_increase_attribute_names_the_actor_uuid():
    msg, kw = _call(
        lambda c: c.increase_attribute("system.attributes.hp.value", 5, UUID)
    )

    assert msg == "increase"
    assert kw["uuid"] == UUID
    assert "actorUuid" not in kw


# ── and the endpoints that really do take actorUuid ───────────────────────

def test_the_rest_endpoints_still_use_actor_uuid():
    """Not a blanket rename. These were confirmed working against the same
    live world with actorUuid, and the module echoes it back."""
    for fn, msg in (
        (lambda c: c.request_short_rest(UUID), "short-rest"),
        (lambda c: c.request_long_rest(UUID), "long-rest"),
        (lambda c: c.break_concentration(UUID), "break-concentration"),
    ):
        sent_type, kw = _call(fn)
        assert sent_type == msg
        assert kw["actorUuid"] == UUID, f"{msg} takes actorUuid"
