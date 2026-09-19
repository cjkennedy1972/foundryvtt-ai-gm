#!/usr/bin/env python3
"""Every action's schema and its handler have to agree on the arguments.

test_action_handler_schemas.py checks that the two tables have the same
keys. Nothing checked that a handler can be *called* with what its schema
produces, which is the drift that keeps surfacing in this review:

  use_spell_slot changed its return shape in #178 and execute_cast_spell
  kept reading the old one (#195).

  use_action was advertised to the model with no working endpoint behind it
  (#184).

  evals/harness.py's LLM doubles kept an older generate() signature and
  broke a test only because combat happened to call them (#182).

Three properties, over all 47 actions at once:

  the schema validates a minimal payload
  the handler accepts every field the schema can send
  every argument the handler requires is a field the schema can express

That last one is the sharpest: a handler with a required parameter the
schema has no field for can never be called successfully by the model, and
nothing else in the suite would say so.

Run:
    cd ai-engine && python -m pytest tests/test_action_contract_smoke.py -v
"""

import inspect
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from actions.dispatcher import ACTION_HANDLERS
from actions.schemas import ACTION_SCHEMAS
from tests.conftest import minimal_action_payload

# Arguments the dispatcher supplies, not the model. Kept explicit rather than
# inferred so a new one has to be added deliberately.
INJECTED = {
    "self", "app_state", "foundry", "source", "state_tracker", "llm",
    "dispatcher", "db", "campaign_loader", "npc_registry", "combat_loop",
    "tts", "kwargs",
}

ACTIONS = sorted(ACTION_SCHEMAS)


def _signature(action_type):
    return inspect.signature(ACTION_HANDLERS[action_type])


def _schema_fields(action_type):
    return set(ACTION_SCHEMAS[action_type].model_fields) - {"type", "source"}


@pytest.mark.parametrize("action_type", ACTIONS)
def test_the_schema_validates_its_own_minimal_payload(action_type):
    """A schema whose required fields cannot be filled is unusable."""
    payload = minimal_action_payload(action_type)
    # The dispatcher strips the discriminator before validating; the schemas
    # are extra="forbid" and do not declare a "type" field.
    payload.pop("type", None)

    ACTION_SCHEMAS[action_type].model_validate(payload)


@pytest.mark.parametrize("action_type", ACTIONS)
def test_the_handler_accepts_every_field_the_schema_can_send(action_type):
    signature = _signature(action_type)
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in signature.parameters.values()):
        pytest.skip("handler takes **kwargs")

    unaccepted = sorted(_schema_fields(action_type) - set(signature.parameters))

    assert unaccepted == [], (
        f"{action_type}: the model can send {unaccepted}, and the handler "
        "has no parameter for them"
    )


@pytest.mark.parametrize("action_type", ACTIONS)
def test_every_argument_the_handler_requires_is_one_the_schema_can_express(action_type):
    """Otherwise the model cannot call it successfully, ever."""
    signature = _signature(action_type)
    required = {
        name for name, p in signature.parameters.items()
        if p.default is inspect.Parameter.empty
        and name not in INJECTED
        and p.kind not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
    }

    unfillable = sorted(required - _schema_fields(action_type))

    assert unfillable == [], (
        f"{action_type}: the handler requires {unfillable}, which the schema "
        "has no field for"
    )


@pytest.mark.parametrize("action_type", ACTIONS)
def test_the_handler_is_awaitable(action_type):
    """The dispatcher awaits every handler."""
    assert inspect.iscoroutinefunction(ACTION_HANDLERS[action_type]), action_type


def test_the_two_tables_still_describe_the_same_set():
    assert set(ACTION_SCHEMAS) == set(ACTION_HANDLERS)


def test_there_are_actions_to_check():
    """A parametrised suite over an empty list passes silently."""
    assert len(ACTIONS) > 40, f"only {len(ACTIONS)} actions found"
