#!/usr/bin/env python3
"""A test that cannot fail is worse than no test: it reports coverage it
does not have.

Three of these turned up in a scan of the suite. Two were named for an
invariant they never checked — one built a recorder for exactly the evidence
it needed, then dropped it on the floor and said so in a comment. The third
described a done-callback and never looked at the set the callback drains.
The behaviour they described was correct, so nobody noticed.

I hit the same shape repeatedly while writing the fixes in this repo: an
assertion on a source substring that matched an import line, a secrets check
that passed because the test env had an empty key, a scaling assertion that
passed by chance because two means overlapped. The suite is only as good as
its ability to go red.

Run:
    cd ai-engine && python -m pytest tests/test_no_toothless_tests.py -v
"""

import ast
import pathlib

TESTS = pathlib.Path(__file__).parent

# Calls that raise on failure, so the call itself is the assertion.
RAISING_CALLS = {"model_validate", "model_validate_json"}


def _functions(tree):
    return {
        f.name: f for f in ast.walk(tree)
        if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _assertions(fn, funcs, seen=None):
    """Count assertions in `fn`, following calls to helpers in the same file.

    Most wrappers here are `def test_x(): asyncio.run(_scenario_x())`, and the
    assertions live in the helper. Without following the call every one of
    them reads as toothless.
    """
    seen = seen or set()
    if fn.name in seen:
        return 0
    seen.add(fn.name)

    n = 0
    for node in ast.walk(fn):
        if isinstance(node, ast.Assert):
            n += 1
        elif isinstance(node, ast.With):
            n += sum("raises" in ast.unparse(i.context_expr) for i in node.items)
        elif isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Attribute):
                # mock's assert_called_once, unittest's assertEqual, pytest.raises
                if f.attr.startswith("assert") or f.attr == "raises" or f.attr in RAISING_CALLS:
                    n += 1
            elif isinstance(f, ast.Name):
                if f.id in funcs:
                    n += _assertions(funcs[f.id], funcs, seen)
                elif f.id == "fail":
                    n += 1
            for arg in node.args:   # asyncio.run(_scenario())
                if isinstance(arg, ast.Call) and isinstance(arg.func, ast.Name) and arg.func.id in funcs:
                    n += _assertions(funcs[arg.func.id], funcs, seen)
    return n


def _always_true(test):
    """Statically true regardless of what the code under test does."""
    if isinstance(test, ast.Constant) and bool(test.value):
        return True
    if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.Or):
        return any(isinstance(v, ast.Constant) and bool(v.value) for v in test.values)
    if (isinstance(test, ast.Compare) and len(test.ops) == 1
            and isinstance(test.ops[0], (ast.Eq, ast.Is))
            and ast.unparse(test.left) == ast.unparse(test.comparators[0])
            # two identical CALLS is a determinism check, not a tautology
            and not any(isinstance(n, ast.Call) for n in ast.walk(test))):
        return True
    return False


def _parsed():
    for path in sorted(TESTS.rglob("test_*.py")):
        try:
            yield path, ast.parse(path.read_text())
        except SyntaxError:
            continue


def test_every_test_asserts_something():
    toothless = []
    for path, tree in _parsed():
        funcs = _functions(tree)
        for fn in funcs.values():
            if fn.name.startswith("test") and _assertions(fn, funcs) == 0:
                toothless.append(f"{path.name}:{fn.lineno} {fn.name}")

    assert toothless == [], (
        "these tests pass no matter what the code does:\n  " + "\n  ".join(toothless)
    )


def test_no_assertion_is_true_by_construction():
    vacuous = [
        f"{path.name}:{node.lineno} assert {ast.unparse(node.test)[:70]}"
        for path, tree in _parsed()
        for node in ast.walk(tree)
        if isinstance(node, ast.Assert) and _always_true(node.test)
    ]

    assert vacuous == [], (
        "these assertions cannot fail:\n  " + "\n  ".join(vacuous)
    )


def test_the_guard_can_still_see_a_toothless_test():
    """A scan that cannot find the thing it looks for is itself toothless."""
    tree = ast.parse("def test_nothing():\n    x = compute()\n")
    funcs = _functions(tree)

    assert _assertions(funcs["test_nothing"], funcs) == 0
    assert _always_true(ast.parse("x == 1 or True", mode="eval").body)
    assert not _always_true(ast.parse("x == 1", mode="eval").body)
