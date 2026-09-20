"""Foundry rejects getFlag/setFlag/unsetFlag for any scope that is not core,
world, the game system, or an *active module id* ("Flag scope X is not valid
or not currently active"). Mocked execute_js can't see that, so a bad scope only
fails live. Read and write our own namespaces through `doc.flags[...]` /
`doc.update({"flags.<ns>...": ...})` instead."""
import re
from pathlib import Path

ENGINE = Path(__file__).resolve().parent.parent
# dnd5e is the game system; bossbar is a module the caller checks is active first.
ALLOWED = {"core", "world", "dnd5e", "bossbar"}


def test_no_flag_api_call_uses_an_engine_namespace():
    bad = []
    for path in ENGINE.rglob("*.py"):
        if any(part in {".venv", ".venv311", "venv", "tests", "htmlcov"} for part in path.parts):
            continue
        for m in re.finditer(r"""[gs]etFlag\(\s*['"]([^'"]+)['"]|unsetFlag\(\s*['"]([^'"]+)['"]""", path.read_text(errors="ignore")):
            scope = m.group(1) or m.group(2)
            if scope not in ALLOWED:
                bad.append(f"{path.relative_to(ENGINE)}: {scope}")
    assert not bad, bad
