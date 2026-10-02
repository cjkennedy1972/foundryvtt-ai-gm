"""Deploy-time file audit: campaign/file_audit.py and its hook at the end of deploy_to_foundry."""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from campaign.file_audit import SAMPLE, audit_world_files
from campaign.orchestrator import CampaignOrchestrator
from foundry.world_cli import WorldCLIError


def _cli(result=None, error=None):
    cli = MagicMock()
    cli.call = AsyncMock(side_effect=error) if error else AsyncMock(return_value=result)
    return cli


def _broken(n, doc="Actor"):
    return [{"docType": doc, "id": f"id{i}", "field": "img", "path": f"worlds/w/missing{i}.png"} for i in range(n)]


@pytest.mark.asyncio
async def test_unconfigured_world_cli_means_no_audit_at_all():
    assert await audit_world_files(None) is None


@pytest.mark.asyncio
async def test_a_clean_world_reports_zero_and_what_it_checked(caplog):
    cli = _cli({"broken": [], "total": 0, "checkedRefs": 42})
    with caplog.at_level(logging.INFO):
        out = await audit_world_files(cli)
    assert out == {"status": "ok", "checked": 42, "broken": 0, "by_type": {}, "sample": []}
    cli.call.assert_awaited_once_with("world.audit-files", {"limit": 100})


@pytest.mark.asyncio
async def test_broken_references_are_counted_by_type_sampled_and_warned_about(caplog):
    cli = _cli({"broken": _broken(30) + _broken(2, "Scene"), "total": 32, "checkedRefs": 200})
    with caplog.at_level(logging.WARNING):
        out = await audit_world_files(cli)
    assert out["broken"] == 32 and out["by_type"] == {"Actor": 30, "Scene": 2}
    assert len(out["sample"]) == SAMPLE and out["sample"][0] == {"docType": "Actor", "id": "id0", "field": "img", "path": "worlds/w/missing0.png"}
    warning = next(r.message for r in caplog.records if r.levelno == logging.WARNING)
    assert "32 broken file reference" in warning and "worlds/w/missing0.png" in warning


@pytest.mark.asyncio
@pytest.mark.parametrize("error,reason", [
    (WorldCLIError("DAEMON_UNAVAILABLE", "down"), "DAEMON_UNAVAILABLE"),
    (WorldCLIError("BRIDGE_NOT_READY", "no GM"), "BRIDGE_NOT_READY"),
    (WorldCLIError("COMMAND_DENIED", "denied"), "COMMAND_DENIED"),
    (RuntimeError("boom"), "ERROR"),
])
async def test_an_audit_that_cannot_answer_reports_why_and_never_raises(error, reason):
    assert await audit_world_files(_cli(error=error)) == {"status": "unavailable", "reason": reason}


def _foundry():
    f = AsyncMock()
    f._send.return_value = {"data": {"uuid": "Actor.1", "_id": "Actor.1"}}
    f.execute_js.return_value = {"result": []}
    return f


@pytest.mark.asyncio
async def test_deploy_attaches_the_audit_only_when_world_cli_is_configured():
    without = await CampaignOrchestrator().deploy_to_foundry({}, _foundry(), {}, scan_result={"active_modules": {}})
    assert "file_audit" not in without

    cli = _cli({"broken": _broken(1), "total": 1, "checkedRefs": 5})
    with_cli = await CampaignOrchestrator(world_cli=cli).deploy_to_foundry({}, _foundry(), {}, scan_result={"active_modules": {}})
    assert with_cli["file_audit"]["broken"] == 1 and with_cli["status"] == "complete"


@pytest.mark.asyncio
async def test_a_failing_audit_cannot_fail_the_deploy():
    cli = _cli(error=WorldCLIError("DAEMON_UNAVAILABLE", "down"))
    result = await CampaignOrchestrator(world_cli=cli).deploy_to_foundry(
        {"npcs": [{"name": "Borin", "role": "innkeeper"}]}, _foundry(), {}, scan_result={"active_modules": {}})
    assert result["status"] == "complete" and [n["name"] for n in result["npcs"]] == ["Borin"]
    assert result["file_audit"] == {"status": "unavailable", "reason": "DAEMON_UNAVAILABLE"}
