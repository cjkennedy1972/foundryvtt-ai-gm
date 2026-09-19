#!/usr/bin/env python3
"""A caller reading a key its producer never returns.

Four defects this shape in one pass over this codebase:

  - the generation executors read GeneratedTreasure/GeneratedNPC dataclasses
    with dict .get(), so every call raised into a swallowing except
  - execute_cast_spell read result["success"] after use_spell_slot was changed
    to return {ok, used, remaining}, so a cast with no slots reported success
  - AutoOptimizer read result["synergies"]["scene_enhancements"] where
    optimize_campaign puts counts at that level and the lists one deeper under
    "details", so every optimize-scene call answered with an empty list (#213)

None of them raised. A .get() with a default is silent by construction, which
is what lets this survive in a codebase that otherwise logs everything.

The scan only considers producers whose EVERY return is a dict literal, so the
key set is known exactly rather than guessed, and it skips a producer whose
name is ambiguous across modules or whose result the caller mutates. It
follows chained .get() into nested literals, because that is where #213 lived.

Run:
    cd ai-engine && python -m pytest tests/test_no_dict_key_drift.py -v
"""

import ast
import pathlib

ENGINE = pathlib.Path(__file__).resolve().parent.parent
SKIP = {"node_modules", "__pycache__", "tests", "evals"}


def _dict_keys(node):
    """{key: value_node} for a dict literal, or None if the shape is unknown."""
    if not isinstance(node, ast.Dict):
        return None
    keys = {}
    for k, v in zip(node.keys, node.values):
        if k is None:                      # {**other}: unknown extra keys
            return None
        if isinstance(k, ast.Constant) and isinstance(k.value, str):
            keys[k.value] = v
    return keys


def _sources(root):
    for path in sorted(root.rglob("*.py")):
        if any(p.startswith(".") or p in SKIP or p.endswith("venv") for p in path.parts):
            continue
        try:
            yield path, ast.parse(path.read_text())
        except (SyntaxError, UnicodeDecodeError):
            continue


def _returned_literal(fn, ret):
    """The dict literal `ret` returns, resolving one level of local variable."""
    value = ret.value
    if isinstance(value, ast.Name):
        assigns = [
            a for a in ast.walk(fn)
            if isinstance(a, ast.Assign) and len(a.targets) == 1
            and isinstance(a.targets[0], ast.Name) and a.targets[0].id == value.id
        ]
        mutated = any(
            isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name)
            and t.value.id == value.id
            for n in ast.walk(fn) if isinstance(n, ast.Assign) for t in n.targets
        )
        if len(assigns) != 1 or mutated:
            return None
        value = assigns[0].value
    return _dict_keys(value)


def producers(sources):
    """{function name: {key: value_node}} for functions of a known dict shape.

    A name maps to None when it is ambiguous or its shape cannot be pinned
    down; those are never matched against.
    """
    found = {}
    for _, tree in sources:
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            returns = [n for n in ast.walk(fn) if isinstance(n, ast.Return) and n.value]
            if not returns:
                continue
            merged, ok = {}, True
            for ret in returns:
                keys = _returned_literal(fn, ret)
                if keys is None:
                    ok = False
                    break
                merged.update(keys)
            if not (ok and merged):
                found[fn.name] = None
            elif fn.name in found:
                found[fn.name] = None
            else:
                found[fn.name] = merged
    return found


def _get_chain(node):
    """`x.get("a", {}).get("b")` -> ("x", ["a", "b"])."""
    path, cur = [], node
    while (isinstance(cur, ast.Call) and isinstance(cur.func, ast.Attribute)
           and cur.func.attr == "get" and cur.args
           and isinstance(cur.args[0], ast.Constant)
           and isinstance(cur.args[0].value, str)):
        path.append(cur.args[0].value)
        cur = cur.func.value
    if not path or not isinstance(cur, ast.Name):
        return None
    return cur.id, list(reversed(path))


def drifted_keys(sources):
    known = producers(sources)
    findings = []
    for path, tree in sources:
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            bound = {}
            for a in ast.walk(fn):
                if not (isinstance(a, ast.Assign) and len(a.targets) == 1
                        and isinstance(a.targets[0], ast.Name)):
                    continue
                call = a.value.value if isinstance(a.value, ast.Await) else a.value
                if not isinstance(call, ast.Call):
                    continue
                name = (call.func.attr if isinstance(call.func, ast.Attribute)
                        else getattr(call.func, "id", None))
                if known.get(name):
                    bound[a.targets[0].id] = name
            if not bound:
                continue
            for n in ast.walk(fn):        # a caller that writes into it owns it
                if isinstance(n, ast.Assign):
                    for t in n.targets:
                        if isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name):
                            bound.pop(t.value.id, None)
            for n in ast.walk(fn):
                got = _get_chain(n)
                if not got or got[0] not in bound:
                    continue
                var, keys = got
                here = known[bound[var]]
                for i, key in enumerate(keys):
                    if not isinstance(here, dict):
                        break
                    if key not in here:
                        findings.append(
                            f"{path.name}:{n.lineno} {var} = {bound[var]}(...) has no "
                            f"{'.'.join(keys[:i + 1])!r} — it has {sorted(here)[:6]}"
                        )
                        break
                    here = _dict_keys(here[key])
    return findings


def test_no_caller_reads_a_key_its_producer_never_returns():
    findings = drifted_keys(list(_sources(ENGINE)))

    assert findings == [], "\n  " + "\n  ".join(findings)


def test_the_guard_finds_a_planted_mismatch():
    """A scan that cannot find the thing it looks for is worth nothing."""
    producer = ast.parse(
        "def build():\n"
        "    return {'status': 'ok', 'counts': {'scenes': 2}, 'details': {'scenes': []}}\n"
    )
    caller = ast.parse(
        "def read():\n"
        "    result = build()\n"
        "    good = result.get('details', {}).get('scenes', [])\n"
        "    bad = result.get('counts', {}).get('scene_list', [])\n"
        "    worse = result.get('synergies', {})\n"
    )
    sources = [(pathlib.Path("producer.py"), producer), (pathlib.Path("caller.py"), caller)]

    findings = drifted_keys(sources)

    assert len(findings) == 2, findings
    assert any("'counts.scene_list'" in f for f in findings)
    assert any("'synergies'" in f for f in findings)
    assert not any("details.scenes" in f for f in findings), "the valid read was flagged"


def test_a_producer_of_unknown_shape_is_not_guessed_at():
    """Better silent than wrong: a dict built in a loop tells us nothing."""
    sources = [
        (pathlib.Path("p.py"), ast.parse(
            "def build(items):\n"
            "    out = {}\n"
            "    for i in items:\n"
            "        out[i] = 1\n"
            "    return out\n")),
        (pathlib.Path("c.py"), ast.parse(
            "def read():\n"
            "    result = build([])\n"
            "    return result.get('anything')\n")),
    ]

    assert drifted_keys(sources) == []
