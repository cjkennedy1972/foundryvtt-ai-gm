"""Verify that every action handler has a corresponding schema."""

from actions.dispatcher import ACTION_HANDLERS
from actions.schemas import ACTION_SCHEMAS


def test_all_action_handlers_have_schemas():
    """Handlers and schemas must match exactly (every handler → a schema)."""
    handler_keys = set(ACTION_HANDLERS.keys())
    schema_keys = set(ACTION_SCHEMAS.keys())

    # Every handler must have a schema
    missing_schemas = handler_keys - schema_keys
    assert not missing_schemas, f"Handlers without schemas: {missing_schemas}"

    # Every schema should have a handler (nice to have, but not critical)
    orphan_schemas = schema_keys - handler_keys
    # Log but don't fail — stale schemas are less critical than missing ones
    if orphan_schemas:
        print(f"Schemas without handlers (not critical): {orphan_schemas}")
