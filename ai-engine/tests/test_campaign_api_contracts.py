"""Campaign API request/response contracts and validation.

Tests verify Pydantic model validation, request parsing, and response structure
for all campaign endpoints without full HTTP/mocking complexity.
"""

import pytest

from api.routes.campaign import (
    CampaignCreate, CampaignScanRequest, CampaignBuildRequest,
    CampaignExtendRequest, CampaignTeardownRequest, CampaignDeployRequest,
    CampaignRegenerateAssetsRequest, CampaignStartRequest, CampaignRestartRequest,
    SessionEndRequest, CampaignDeleteRequest, CampaignImportRequest,
    CampaignScanResponse, CampaignBuildResponse, CampaignExtendResponse,
    CampaignTeardownResponse, CampaignDeployResponse, CampaignRegenerateAssetsResponse,
    CampaignStartResponse, SessionEndResponse, CampaignListResponse,
)


class TestCampaignCreateValidation:
    """Verify CampaignCreate request model."""

    def test_campaign_create_requires_name(self):
        """CampaignCreate requires 'name' field."""
        # Valid request
        req = CampaignCreate(name="My Campaign")
        assert req.name == "My Campaign"

        # Invalid request (missing name)
        with pytest.raises(ValueError):
            CampaignCreate()

    def test_campaign_create_optional_fields(self):
        """CampaignCreate has optional fields."""
        req = CampaignCreate(name="Camp", description="A cool campaign", vault_files=["file1.md"])

        assert req.vault_files == ["file1.md"]
        assert req.description == "A cool campaign"


class TestCampaignScanRequestValidation:
    """Verify CampaignScanRequest contract."""

    def test_scan_request_validates(self):
        """CampaignScanRequest accepts world_name."""
        req = CampaignScanRequest(world_name="The Forgotten Realms")
        assert req.world_name == "The Forgotten Realms"

        # world_name is optional
        req2 = CampaignScanRequest()
        assert req2.world_name is None


class TestCampaignBuildRequestValidation:
    """Verify CampaignBuildRequest contract."""

    def test_build_request_requires_name(self):
        """CampaignBuildRequest requires name."""
        req = CampaignBuildRequest(name="New Campaign")
        assert req.name == "New Campaign"

    def test_build_request_has_defaults(self):
        """CampaignBuildRequest has sensible defaults."""
        req = CampaignBuildRequest(name="Camp")

        assert req.level_range == "1-5"
        assert req.generate_prologue is True
        assert req.vault_files == []


class TestCampaignStartRequestValidation:
    """Verify CampaignStartRequest contract."""

    def test_start_request_requires_campaign_name(self):
        """CampaignStartRequest requires campaign_name."""
        req = CampaignStartRequest(campaign_name="test-campaign")
        assert req.campaign_name == "test-campaign"

    def test_start_request_continue_flag(self):
        """CampaignStartRequest has continue_from_last flag."""
        req = CampaignStartRequest(campaign_name="test", continue_from_last=True)
        assert req.continue_from_last is True


class TestCampaignExtendRequestValidation:
    """Verify CampaignExtendRequest contract."""

    def test_extend_request_requires_campaign_name(self):
        """CampaignExtendRequest requires campaign_name."""
        req = CampaignExtendRequest(campaign_name="test-campaign")
        assert req.campaign_name == "test-campaign"

    def test_extend_request_current_level(self):
        """CampaignExtendRequest accepts current_level."""
        req = CampaignExtendRequest(campaign_name="test", current_level=5)
        assert req.current_level == 5


class TestCampaignTeardownRequestValidation:
    """Verify CampaignTeardownRequest contract."""

    def test_teardown_request_requires_campaign_name(self):
        """CampaignTeardownRequest requires campaign_name."""
        req = CampaignTeardownRequest(campaign_name="old-campaign")
        assert req.campaign_name == "old-campaign"


class TestCampaignDeployRequestValidation:
    """Verify CampaignDeployRequest contract."""

    def test_deploy_request_requires_campaign_name(self):
        """CampaignDeployRequest requires campaign_name."""
        req = CampaignDeployRequest(campaign_name="test-campaign")
        assert req.campaign_name == "test-campaign"


class TestCampaignRegenerateAssetsValidation:
    """Verify CampaignRegenerateAssetsRequest contract."""

    def test_regenerate_request_requires_campaign_name(self):
        """CampaignRegenerateAssetsRequest requires campaign_name."""
        req = CampaignRegenerateAssetsRequest(campaign_name="test-campaign")
        assert req.campaign_name == "test-campaign"

    def test_regenerate_request_attach_flag(self):
        """CampaignRegenerateAssetsRequest has attach_to_foundry flag."""
        req = CampaignRegenerateAssetsRequest(campaign_name="test", attach_to_foundry=False)
        assert req.attach_to_foundry is False


class TestCampaignRestartRequestValidation:
    """Verify CampaignRestartRequest contract."""

    def test_restart_request_requires_campaign_name(self):
        """CampaignRestartRequest requires campaign_name."""
        req = CampaignRestartRequest(campaign_name="test-campaign")
        assert req.campaign_name == "test-campaign"


class TestSessionEndRequestValidation:
    """Verify SessionEndRequest contract."""

    def test_session_end_request_has_reason(self):
        """SessionEndRequest accepts reason."""
        req = SessionEndRequest(reason="Players left the table")
        assert req.reason == "Players left the table"

    def test_session_end_request_default_reason(self):
        """SessionEndRequest has default reason."""
        req = SessionEndRequest()
        assert req.reason == "GM ended session"


class TestCampaignDeleteRequestValidation:
    """Verify CampaignDeleteRequest contract."""

    def test_delete_request_requires_name(self):
        """CampaignDeleteRequest requires name."""
        req = CampaignDeleteRequest(name="old-campaign")
        assert req.name == "old-campaign"


class TestCampaignScanResponseValidation:
    """Verify CampaignScanResponse contract."""

    def test_scan_response_validates(self):
        """CampaignScanResponse has correct structure."""
        resp = CampaignScanResponse(
            status="ok",
            scan_id="scan-abc",
            scenes=[{"id": "s1"}],
            actors=[{"id": "a1"}],
        )

        assert resp.status == "ok"
        assert resp.scan_id == "scan-abc"
        assert len(resp.scenes) == 1

    def test_scan_response_error_path(self):
        """CampaignScanResponse handles errors."""
        resp = CampaignScanResponse(
            status="error",
            scan_id="scan-xyz",
            error="Connection lost"
        )

        assert resp.status == "error"
        assert resp.error == "Connection lost"


class TestCampaignBuildResponseValidation:
    """Verify CampaignBuildResponse contract."""

    def test_build_response_success_path(self):
        """CampaignBuildResponse success structure."""
        resp = CampaignBuildResponse(
            status="ok",
            campaign_id="camp-1",
            campaign_name="Test",
            ready_to_start=True,
        )

        assert resp.status == "ok"
        assert resp.campaign_id == "camp-1"
        assert resp.ready_to_start is True

    def test_build_response_error_path(self):
        """CampaignBuildResponse error handling."""
        resp = CampaignBuildResponse(
            status="error",
            campaign_id="camp-1",
            campaign_name="Test",
            error="LLM timeout",
            ready_to_start=False,
        )

        assert resp.status == "error"
        assert resp.error == "LLM timeout"


class TestCampaignStartResponseValidation:
    """Verify CampaignStartResponse contract."""

    def test_start_response_success_path(self):
        """CampaignStartResponse includes session_id."""
        resp = CampaignStartResponse(
            status="started",
            session_id="sess-123",
            campaign_name="test-campaign",
        )

        assert resp.status == "started"
        assert resp.session_id == "sess-123"


class TestSessionEndResponseValidation:
    """Verify SessionEndResponse contract."""

    def test_end_response_ended_path(self):
        """SessionEndResponse includes summary."""
        resp = SessionEndResponse(
            status="ended",
            session_id="sess-1",
            campaign_name="test",
            summary="Players reached the dragon's lair",
        )

        assert resp.status == "ended"
        assert resp.session_id == "sess-1"

    def test_end_response_no_session_path(self):
        """SessionEndResponse handles no active session."""
        resp = SessionEndResponse(
            status="no_active_session",
            session_id="",
            campaign_name="",
        )

        assert resp.status == "no_active_session"


class TestCampaignListResponseValidation:
    """Verify CampaignListResponse contract."""

    def test_list_response_success_path(self):
        """CampaignListResponse returns campaigns."""
        resp = CampaignListResponse(
            campaigns=[
                {"name": "camp-1", "status": "ready"},
                {"name": "camp-2", "status": "draft"},
            ]
        )

        assert len(resp.campaigns) == 2
        assert resp.campaigns[0]["name"] == "camp-1"

    def test_list_response_error_path(self):
        """CampaignListResponse handles errors."""
        resp = CampaignListResponse(
            campaigns=[],
            error="Vault path not found"
        )

        assert resp.error == "Vault path not found"
