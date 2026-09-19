#!/usr/bin/env python3
"""evals/harness.py's doubles have to describe the classes they stand in for.

The eval harness is how this project checks the GM behaves. If a double
drifts from the real class, the evals validate a fiction — and two
incidents in this review came from exactly that:

  Adding include_history/persist_history to LLMManager.generate() in #182
  broke a harness test, because both LLM doubles kept the old signature.

  test_concentration.py stubbed use_spell_slot as {"ok": True}, a shape the
  client stopped returning in #178. That stale stub is why the suite stayed
  green through the bug fixed in #195.

A double is allowed to implement less than the real class — that is what
makes it a double. It is not allowed to implement something the real class
does not have, or to take different parameters for a shared method.

Run:
    cd ai-engine && python -m pytest tests/test_harness_doubles_match_reality.py -v
"""

import importlib
import inspect
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from evals import harness

DOUBLES = {
    "MockFoundryClient": "foundry.client:FoundryClient",
    "MockDatabase": "persistence.db:Database",
    "MockStateTracker": "state.tracker:GameStateTracker",
    "MockNPCRegistry": "npc.registry:NPCRegistry",
    "ScriptedLLM": "llm.manager:LLMManager",
    "RecordingLLM": "llm.manager:LLMManager",
}

# Inspection helpers the harness adds for tests to read. Not part of any
# production contract, so they are allowed to have no counterpart.
HARNESS_ONLY = {"calls_of", "emit"}


def _real(spec):
    module, cls = spec.split(":")
    return getattr(importlib.import_module(module), cls)


def _public_methods(klass):
    return {
        name for name in dir(klass)
        if not name.startswith("_") and callable(getattr(klass, name, None))
    }


@pytest.mark.parametrize("double_name,spec", sorted(DOUBLES.items()))
def test_a_double_implements_nothing_the_real_class_lacks(double_name, spec):
    """A method only the double has is one production can never call, so a
    scenario using it passes against an API that does not exist."""
    double, real = getattr(harness, double_name), _real(spec)

    phantom = sorted(
        _public_methods(double) - set(dir(real)) - HARNESS_ONLY
    )

    assert phantom == [], (
        f"{double_name} offers {phantom}, which {spec.split(':')[1]} does not have"
    )


@pytest.mark.parametrize("double_name,spec", sorted(DOUBLES.items()))
def test_a_shared_method_takes_the_same_parameters(double_name, spec):
    """A double that takes `name` where the real class takes `npc_name`
    works until someone calls it by keyword."""
    double, real = getattr(harness, double_name), _real(spec)
    mismatches = []

    for name in sorted(_public_methods(double) - HARNESS_ONLY):
        if not hasattr(real, name):
            continue
        try:
            d_sig = inspect.signature(getattr(double, name))
            r_sig = inspect.signature(getattr(real, name))
        except (TypeError, ValueError):
            continue
        if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in d_sig.parameters.values()):
            continue
        missing = [p for p in r_sig.parameters if p not in d_sig.parameters and p != "self"]
        if missing:
            mismatches.append(f"{name}(): real takes {missing}, the double does not")

    assert mismatches == [], f"{double_name}\n  " + "\n  ".join(mismatches)


@pytest.mark.parametrize("double_name,spec", sorted(DOUBLES.items()))
def test_an_async_method_is_async_on_both(double_name, spec):
    """Awaiting a sync double, or calling an async one bare, fails only at
    the call site the eval happens to exercise."""
    double, real = getattr(harness, double_name), _real(spec)
    wrong = []

    for name in sorted(_public_methods(double) - HARNESS_ONLY):
        real_attr = getattr(real, name, None)
        if real_attr is None or isinstance(real_attr, property):
            continue
        if inspect.iscoroutinefunction(real_attr) != inspect.iscoroutinefunction(
            getattr(double, name)
        ):
            wrong.append(name)

    assert wrong == [], f"{double_name}: async mismatch on {wrong}"


def test_every_double_named_here_still_exists():
    for double_name, spec in DOUBLES.items():
        assert hasattr(harness, double_name), f"{double_name} is gone from the harness"
        _real(spec)


def test_the_doubles_actually_implement_something():
    """An emptied double satisfies every check above. Three is the current
    floor — RecordingLLM is a thin wrapper around a real manager."""
    for double_name in DOUBLES:
        count = len(_public_methods(getattr(harness, double_name)))
        assert count >= 3, f"{double_name} implements only {count} method(s)"
