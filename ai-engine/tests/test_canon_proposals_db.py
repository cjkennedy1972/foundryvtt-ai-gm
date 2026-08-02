"""Tests for the canon_proposals table and its CRUD methods — the review
queue that AI-proposed canon facts sit in before a GM approves/rejects them.
"""

import asyncio

from persistence.db import Database


def test_create_and_fetch_pending_proposal(tmp_path):
    async def run():
        db = Database(str(tmp_path / "test.db"))
        await db.init()

        proposal_id = await db.create_canon_proposal(
            session_id="s1",
            campaign="Test Campaign",
            fact="The king was secretly a doppelganger.",
            confidence="high",
            rationale="Confirmed by the party in the final scene.",
        )
        assert proposal_id > 0

        pending = await db.get_pending_canon_proposals()
        assert len(pending) == 1
        assert pending[0]["fact"] == "The king was secretly a doppelganger."
        assert pending[0]["confidence"] == "high"
        assert pending[0]["status"] == "pending"
        assert pending[0]["reviewed_at"] is None

        await db.close()

    asyncio.run(run())


def test_approve_proposal_with_edited_text_overwrites_fact(tmp_path):
    async def run():
        db = Database(str(tmp_path / "test.db"))
        await db.init()

        proposal_id = await db.create_canon_proposal(
            session_id="s1", campaign="Test Campaign",
            fact="draft wording", confidence="medium", rationale="r",
        )

        await db.approve_canon_proposal(proposal_id, final_text="GM-edited final wording")

        proposal = await db.get_canon_proposal(proposal_id)
        assert proposal["status"] == "approved"
        assert proposal["fact"] == "GM-edited final wording"
        assert proposal["reviewed_at"] is not None
        assert await db.get_pending_canon_proposals() == []

        await db.close()

    asyncio.run(run())


def test_approve_proposal_without_edit_keeps_original_fact(tmp_path):
    async def run():
        db = Database(str(tmp_path / "test.db"))
        await db.init()

        proposal_id = await db.create_canon_proposal(
            session_id="s1", campaign="Test Campaign",
            fact="unedited fact", confidence="low", rationale="r",
        )
        await db.approve_canon_proposal(proposal_id)

        proposal = await db.get_canon_proposal(proposal_id)
        assert proposal["fact"] == "unedited fact"
        assert proposal["status"] == "approved"

        await db.close()

    asyncio.run(run())


def test_reject_proposal_removes_it_from_pending(tmp_path):
    async def run():
        db = Database(str(tmp_path / "test.db"))
        await db.init()

        proposal_id = await db.create_canon_proposal(
            session_id="s1", campaign="Test Campaign",
            fact="a contradiction", confidence="low", rationale="r",
            contradiction_note="conflicts with: the king is alive",
        )
        await db.reject_canon_proposal(proposal_id)

        proposal = await db.get_canon_proposal(proposal_id)
        assert proposal["status"] == "rejected"
        assert proposal["contradiction_note"] == "conflicts with: the king is alive"
        assert await db.get_pending_canon_proposals() == []

        await db.close()

    asyncio.run(run())


def test_delete_campaign_history_clears_canon_proposals():
    """Campaign restart must not leave orphaned pending canon proposals behind."""
    async def run():
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(f"{tmp}/test.db")
            await db.init()

            await db.create_session("sess-a", "Campaign A")
            await db.create_canon_proposal(
                session_id="sess-a", campaign="Campaign A",
                fact="fact for A", confidence="high", rationale="r",
            )
            await db.create_session("sess-b", "Campaign B")
            await db.create_canon_proposal(
                session_id="sess-b", campaign="Campaign B",
                fact="fact for B", confidence="high", rationale="r",
            )

            await db.delete_campaign_history("Campaign A")

            remaining = await db.get_pending_canon_proposals()
            assert len(remaining) == 1
            assert remaining[0]["campaign"] == "Campaign B"

            await db.close()

    asyncio.run(run())


def test_approve_returns_false_when_already_approved(tmp_path):
    """Regression test: approve_canon_proposal used to run an unconditional
    UPDATE with no status guard, so the same proposal could be approved
    (and its fact written to the vault) twice. The WHERE status='pending'
    compare-and-swap must make a second call a no-op that reports failure."""
    async def run():
        db = Database(str(tmp_path / "test.db"))
        await db.init()

        proposal_id = await db.create_canon_proposal(
            session_id="s1", campaign="Test Campaign",
            fact="a fact", confidence="high", rationale="r",
        )

        first = await db.approve_canon_proposal(proposal_id)
        second = await db.approve_canon_proposal(proposal_id)

        assert first is True
        assert second is False

        await db.close()

    asyncio.run(run())


def test_reject_returns_false_when_already_rejected(tmp_path):
    async def run():
        db = Database(str(tmp_path / "test.db"))
        await db.init()

        proposal_id = await db.create_canon_proposal(
            session_id="s1", campaign="Test Campaign",
            fact="a fact", confidence="high", rationale="r",
        )

        first = await db.reject_canon_proposal(proposal_id)
        second = await db.reject_canon_proposal(proposal_id)

        assert first is True
        assert second is False

        await db.close()

    asyncio.run(run())


def test_reject_returns_false_when_already_approved(tmp_path):
    """The compare-and-swap guards against crossing status transitions too
    — an approved proposal can't be rejected out from under the GM."""
    async def run():
        db = Database(str(tmp_path / "test.db"))
        await db.init()

        proposal_id = await db.create_canon_proposal(
            session_id="s1", campaign="Test Campaign",
            fact="a fact", confidence="high", rationale="r",
        )

        await db.approve_canon_proposal(proposal_id)
        result = await db.reject_canon_proposal(proposal_id)

        assert result is False
        proposal = await db.get_canon_proposal(proposal_id)
        assert proposal["status"] == "approved"

        await db.close()

    asyncio.run(run())


def test_revert_canon_proposal_to_pending(tmp_path):
    """Regression test: a vault-write failure after a successful approval
    claim used to leave the proposal permanently 'approved' with the fact
    never actually written. revert_canon_proposal_to_pending puts it back
    in the review queue so it can be retried."""
    async def run():
        db = Database(str(tmp_path / "test.db"))
        await db.init()

        proposal_id = await db.create_canon_proposal(
            session_id="s1", campaign="Test Campaign",
            fact="a fact", confidence="high", rationale="r",
        )
        await db.approve_canon_proposal(proposal_id)
        assert await db.get_pending_canon_proposals() == []

        await db.revert_canon_proposal_to_pending(proposal_id)

        pending = await db.get_pending_canon_proposals()
        assert len(pending) == 1
        assert pending[0]["id"] == proposal_id
        assert pending[0]["reviewed_at"] is None

        await db.close()

    asyncio.run(run())
