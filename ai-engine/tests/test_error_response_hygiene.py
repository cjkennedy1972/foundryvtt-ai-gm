"""API errors must not hand the caller an exception message.

Routes returned `str(e)` straight to the client (CodeQL py/stack-trace-exposure,
12 sites). An exception message routinely carries absolute paths, SQL
fragments or internal hostnames. The operator still needs to triage, so the
traceback goes to ai-gm.log and the response keeps the exception type.
"""

import ast
import pathlib

import pytest

from api.deps import internal_error

ROUTES = pathlib.Path("api/routes")


def test_no_route_returns_a_raw_exception_message():
    """Guards the whole directory, so a new route cannot reintroduce it."""
    offenders = []
    for path in sorted(ROUTES.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.ExceptHandler) or node.name is None:
                continue
            for call in ast.walk(node):
                if (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                        and call.func.id == "str" and len(call.args) == 1
                        and isinstance(call.args[0], ast.Name)
                        and call.args[0].id == node.name):
                    offenders.append(f"{path}:{call.lineno}")

    assert offenders == [], (
        "raw exception text returned to a client; use api.deps.internal_error "
        f"or interpolate type(e).__name__ instead: {offenders}"
    )


def test_internal_error_keeps_the_type_and_drops_the_message(caplog):
    secret = "/Users/someone/private/path/leaked.db is locked"

    resp = internal_error("Scene update failed", RuntimeError(secret))

    body = resp.body.decode()
    assert resp.status_code == 500
    assert "RuntimeError" in body, "the type is what makes the error triageable"
    assert secret not in body
    assert "ai-gm.log" in body


def test_internal_error_logs_the_full_detail(caplog):
    with caplog.at_level("ERROR"):
        internal_error("Scene update failed", RuntimeError("the real cause"))

    assert "Scene update failed" in caplog.text
    assert "the real cause" in caplog.text, "detail must survive server-side"


def test_internal_error_passes_through_extra_fields():
    """Callers that must keep a response shape (e.g. relationships: []) can."""
    resp = internal_error("Failed", ValueError("x"), relationships=[])

    assert '"relationships":[]' in resp.body.decode().replace(" ", "")
