"""The action modules split out of executors.py, and what must stay true.

executors.py was 2,445 lines. Three groups moved out; the dispatch table and
the import surface did not change. These guard the two ways that goes wrong:
a handler lost in the move, and a patch target that still resolves but no
longer affects the code under test.
"""

import ast
import pathlib

import pytest

from actions import executors
from actions.executors_shared import ExecutionError, _extract_token_id, _require

TESTS = pathlib.Path(__file__).resolve().parent

# Helpers whose module changed. Patching them on actions.executors still
# succeeds — the name is re-exported — but affects nothing, so a test doing
# that passes for the wrong reason.
MOVED_HELPERS = {
    "_resolve_sound_src": "actions.media_actions",
    "_resolve_scene_dimensions": "actions.generation_actions",
}


def test_every_action_type_still_resolves_to_a_callable():
    unresolved = sorted(k for k, v in executors.ACTION_HANDLERS.items() if not callable(v))

    assert unresolved == []
    assert len(executors.ACTION_HANDLERS) == 48, "a handler was lost or added in the split"


def test_the_import_surface_survived():
    """Other modules import these from actions.executors by name."""
    for name in ("ExecutionError", "_require", "_extract_token_id",
                 "_is_player_character", "_resolve_token_id",
                 "execute_death_save", "get_death_save_status", "ACTION_HANDLERS"):
        assert hasattr(executors, name), f"actions.executors lost {name}"


def test_no_test_patches_a_moved_helper_on_the_old_module():
    """A patch on the re-export is a silent no-op, not a failure."""
    offenders = []
    for path in sorted(TESTS.glob("test_*.py")):
        text = path.read_text()
        for helper in MOVED_HELPERS:
            if f'patch("actions.executors.{helper}"' in text:
                offenders.append(f"{path.name} -> {helper} (patch {MOVED_HELPERS[helper]} instead)")

    assert offenders == [], offenders


def test_the_split_modules_do_not_import_executors():
    """executors imports them for the dispatch table; the reverse would cycle."""
    offenders = []
    for mod in ("system_actions", "media_actions", "generation_actions", "executors_shared"):
        path = pathlib.Path(__file__).resolve().parent.parent / "actions" / f"{mod}.py"
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "actions.executors":
                offenders.append(f"{mod}:{node.lineno}")
            if isinstance(node, ast.Import):
                if any(a.name == "actions.executors" for a in node.names):
                    offenders.append(f"{mod}:{node.lineno}")

    assert offenders == [], f"circular import back into executors: {offenders}"


def test_resetting_caches_reaches_the_module_that_owns_the_sound_cache():
    """The cache moved with its reader; a reset that missed it would leak
    a previous world's playlist resolutions into the next one."""
    from actions import media_actions

    media_actions._sound_src_cache = {"creak": "sounds/old.ogg"}
    media_actions._sound_src_cache_at = 123.0

    executors.reset_action_caches()

    assert media_actions._sound_src_cache == {}
    assert media_actions._sound_src_cache_at == 0.0


def test_require_still_raises_execution_error():
    with pytest.raises(ExecutionError, match="boom"):
        _require(False, "boom")

    _require(True, "fine")


def test_extract_token_id_handles_the_relay_shapes():
    assert _extract_token_id({"moved": True, "token_id": "t1"}) == "t1"
    assert _extract_token_id({"id": "t2"}) == "t2"
    assert _extract_token_id({"data": [{"_id": "t3"}]}) == "t3"
    assert _extract_token_id({"data": {"_id": "t4"}}) == "t4"
    assert _extract_token_id("not a dict") == ""
