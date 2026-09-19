#!/usr/bin/env python3
"""Calls to methods that do not exist on the object being called.

Two real defects in this review were this shape, and both failed silently
because the call sites sit inside `except Exception` blocks that log at
debug:

  GameLoop._get_npc_context called NPCRegistry.get_context(). No such
  method. Every actor raised AttributeError into a logger.debug, so the
  entire Tier 3 personality block produced nothing, for the life of the
  project (#177).

  CombatLoop._process_npc_turn read _rec.personality_traits and
  _rec.combat_style off an NPCRecord that has neither, through
  getattr(..., None), so the combat prompt only ever carried "Background:"
  (#180).

Python cannot catch either without types, and both modules are heavily
exercised by tests that mock the collaborator — a MagicMock answers any
attribute, so mocking is what hides this.

This resolves the long-lived singletons to their real classes and checks
every method called on them. Restricted to attributes whose type is
unambiguous; a name that could be more than one class is not worth a guess.

Run:
    cd ai-engine && python -m pytest tests/test_no_attribute_drift.py -v
"""

import ast
import importlib
import inspect
import os
import pathlib
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ENGINE = pathlib.Path(__file__).resolve().parents[1]

# attribute name -> the class it always holds. Only unambiguous ones.
SINGLETONS = {
    "foundry": "foundry.client:FoundryClient",
    "foundry_client": "foundry.client:FoundryClient",
    "llm": "llm.manager:LLMManager",
    "llm_manager": "llm.manager:LLMManager",
    "db": "persistence.db:Database",
    "state_tracker": "state.tracker:GameStateTracker",
    "_state_tracker": "state.tracker:GameStateTracker",
    "dispatcher": "actions.dispatcher:ActionDispatcher",
    "action_dispatcher": "actions.dispatcher:ActionDispatcher",
    "npc_registry": "npc.registry:NPCRegistry",
    "_npc_registry": "npc.registry:NPCRegistry",
    "campaign_loader": "context.loader:CampaignLoader",
    "_campaign_loader": "context.loader:CampaignLoader",
    "combat_loop": "combat.loop:CombatLoop",
    "relay_manager": "relay_proc.manager:RelayManager",
    "scene_awareness": "scene.awareness:SceneAwareness",
    "personality_engine": "npc.personality:PersonalityEngine",
    "_personality_engine": "npc.personality:PersonalityEngine",
    "reinforcement_mgr": "context.reinforcement_manager:ContextReinforcementManager",
    "_reinforcement_mgr": "context.reinforcement_manager:ContextReinforcementManager",
}

# Names reached dynamically on purpose. Empty is the goal state; an entry
# needs a reason, and test_the_allowlist_is_empty below has to be relaxed
# deliberately to add one.
ALLOWED: set = set()

SKIP_DIRS = ("venv", "tests", "admin-panel", "site-packages", "node_modules")


def _members(spec):
    module, cls = spec.split(":")
    klass = getattr(importlib.import_module(module), cls)
    return {name for name, _ in inspect.getmembers(klass)}


@pytest.fixture(scope="module")
def known():
    return {attr: (spec.split(":")[1], _members(spec)) for attr, spec in SINGLETONS.items()}


def _sources():
    for path in sorted(ENGINE.rglob("*.py")):
        if any(any(s in part for s in SKIP_DIRS) for part in path.parts):
            continue
        try:
            yield path, ast.parse(path.read_text())
        except (SyntaxError, UnicodeDecodeError):
            continue


def _calls_on_singletons(tree):
    """(lineno, attr_name, method_name) for self.<attr>.<method>() and state.<attr>.<method>()."""
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        owner = node.func.value
        if not (isinstance(owner, ast.Attribute) and isinstance(owner.value, ast.Name)):
            continue
        if owner.value.id not in ("self", "state", "app_state"):
            continue
        yield node.lineno, owner.attr, node.func.attr


def test_every_method_called_on_a_known_singleton_exists(known):
    missing = []
    for path, tree in _sources():
        for lineno, attr, method in _calls_on_singletons(tree):
            if attr not in known or method in ALLOWED:
                continue
            cls, members = known[attr]
            if method not in members:
                missing.append(f"{path.relative_to(ENGINE)}:{lineno} {attr}.{method}() — {cls} has no such member")

    assert missing == [], "\n" + "\n".join(missing)


def test_the_scan_reaches_the_code_it_claims_to(known):
    """A scan that resolves nothing passes silently."""
    seen = sum(
        1
        for _, tree in _sources()
        for _, attr, _ in _calls_on_singletons(tree)
        if attr in known
    )
    assert seen > 200, f"only {seen} resolvable calls found — the scan is not reaching the engine"


def test_every_singleton_named_here_still_resolves():
    """A renamed class would silently drop its attribute from the check."""
    for attr, spec in SINGLETONS.items():
        module, cls = spec.split(":")
        assert hasattr(importlib.import_module(module), cls), f"{attr}: {spec} is gone"


def test_the_allowlist_is_empty():
    """Nothing currently needs an exemption, and an entry that outlives its
    call site hides the next drift behind the same name."""
    assert ALLOWED == set(), f"exemptions in force: {sorted(ALLOWED)}"
