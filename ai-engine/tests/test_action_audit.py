"""Tests for the action audit trail (actions/audit.py + dispatcher integration).

Replaces the former approval-gate tests. The AI-GM runs unattended, so the
requirement is not "can a GM approve this" but "did the action actually run,
and is there an accurate record of it".

Run:
    cd ai-engine && python -m pytest tests/test_action_audit.py -v
"""

import logging

import pytest

from actions.audit import (
    CONSEQUENTIAL_ACTIONS,
    audit_record,
    is_consequential,
    summarize_params,
)
from actions.dispatcher import ActionDispatcher
from actions.executors import ACTION_HANDLERS


class TestConsequentialSet:
    """The classification must track the real action registry.

    The approval-era version of this set had drifted to nine action names that
    did not exist as handlers (grant_item, heal, damage, level_up, …), which
    silently classified nothing. This test is the guard against a repeat.
    """

    def test_every_consequential_name_is_a_real_action(self):
        unknown = CONSEQUENTIAL_ACTIONS - set(ACTION_HANDLERS)
        assert not unknown, f"CONSEQUENTIAL_ACTIONS names with no handler: {sorted(unknown)}"

    def test_hp_changes_are_classified_consequential(self):
        # update_hp is the action that actually deals damage and heals; the
        # approval-era set omitted it entirely.
        assert is_consequential("update_hp")

    def test_narration_is_not_consequential(self):
        assert not is_consequential("narrate")
        assert not is_consequential("speak")

    def test_unknown_action_is_not_consequential(self):
        assert not is_consequential("no_such_action")


class TestParamSummary:
    def test_drops_injected_infrastructure(self):
        summary = summarize_params({"actor_uuid": "Actor.x", "foundry": object(), "app_state": object()})
        assert "Actor.x" in summary
        assert "foundry" not in summary
        assert "app_state" not in summary

    def test_bounded_length(self):
        summary = summarize_params({"code": "x" * 5000})
        assert len(summary) <= 420
        assert summary.endswith("…")

    def test_unserializable_value_does_not_raise(self):
        class Weird:
            def __repr__(self):
                return "<weird>"

        summary = summarize_params({"thing": Weird()})
        assert "weird" in summary


class TestAuditRecord:
    def test_consequential_success_logs_info(self, caplog):
        with caplog.at_level(logging.INFO, logger="actions.audit"):
            record = audit_record("update_hp", {"damage": 5}, {"success": True})
        assert record["consequential"] is True
        assert "update_hp" in caplog.text
        assert "damage" in caplog.text

    def test_consequential_failure_logs_warning(self, caplog):
        with caplog.at_level(logging.INFO, logger="actions.audit"):
            audit_record("update_hp", {"damage": 5}, {"success": False, "error": "boom"})
        assert any(r.levelno == logging.WARNING for r in caplog.records)
        assert "boom" in caplog.text

    def test_flavor_action_stays_out_of_info_log(self, caplog):
        with caplog.at_level(logging.INFO, logger="actions.audit"):
            record = audit_record("narrate", {"text": "The door creaks."}, {"success": True})
        assert record["consequential"] is False
        assert caplog.text == ""


class TestDispatcherAudits:
    """Consequential actions must EXECUTE (not queue) and be recorded."""

    @pytest.mark.asyncio
    async def test_consequential_action_executes_and_is_stamped(self, monkeypatch):
        calls = []

        async def fake_update_hp(**kwargs):
            calls.append(kwargs)
            return {"type": "update_hp", "success": True}

        monkeypatch.setitem(ACTION_HANDLERS, "update_hp", fake_update_hp)

        dispatcher = ActionDispatcher(foundry_client=None)
        result = await dispatcher.execute(
            {"type": "update_hp", "actor_uuid": "Actor.goblin", "damage": 4}
        )

        # The action ran — the approval gate used to return queued_for_approval
        # here and never call the handler at all.
        assert len(calls) == 1
        assert result["success"] is True
        assert "queued_for_approval" not in result
        assert result["_audit"]["consequential"] is True
        assert "damage" in result["_audit"]["params"]

    @pytest.mark.asyncio
    async def test_failed_action_is_still_audited(self, monkeypatch):
        async def boom(**kwargs):
            raise RuntimeError("relay down")

        monkeypatch.setitem(ACTION_HANDLERS, "update_hp", boom)

        dispatcher = ActionDispatcher(foundry_client=None)
        result = await dispatcher.execute({"type": "update_hp", "actor_uuid": "Actor.goblin", "damage": 4})

        assert result["success"] is False
        assert result["_audit"]["consequential"] is True

    @pytest.mark.asyncio
    async def test_flavor_action_is_audited_too(self, monkeypatch):
        async def fake_narrate(**kwargs):
            return {"type": "narrate", "success": True}

        monkeypatch.setitem(ACTION_HANDLERS, "narrate", fake_narrate)

        dispatcher = ActionDispatcher(foundry_client=None)
        result = await dispatcher.execute({"type": "narrate", "text": "A cold wind rises."})

        assert result["_audit"]["consequential"] is False


class TestDurableTrail:
    """The dispatcher's `_audit` stamp must reach the persisted event log."""

    def test_action_resolved_event_carries_the_audit_fields(self, tmp_path):
        import asyncio
        import json
        from unittest.mock import AsyncMock, MagicMock

        from events.types import ACTION_RESOLVED
        from foundry.chat_listener import ChatListener
        from persistence.db import Database

        async def run():
            db = Database(str(tmp_path / "audit.db"))
            await db.init()
            await db.create_session("s1", campaign="Test Campaign")

            listener = ChatListener(
                foundry=MagicMock(),
                llm=MagicMock(),
                dispatcher=MagicMock(),
                state_tracker=MagicMock(),
                db=db,
            )
            listener._maybe_trigger_npc_agents = AsyncMock()

            # Shaped exactly as ActionDispatcher.execute returns it.
            await listener._record_action_resolved_events([{
                "type": "update_hp",
                "success": True,
                "_audit": {"consequential": True, "params": '{"damage": 8}'},
            }])

            events = await db.get_events_full("s1")
            resolved = [e for e in events if e["type"] == ACTION_RESOLVED]
            assert len(resolved) == 1
            payload = resolved[0]["payload"]
            if isinstance(payload, str):
                payload = json.loads(payload)
            assert payload["action_type"] == "update_hp"
            assert payload["consequential"] is True
            assert "damage" in payload["params"]
            await db.close()

        asyncio.run(run())
