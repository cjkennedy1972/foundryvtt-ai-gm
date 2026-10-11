import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.routes import campaign as campaign_routes


@pytest.fixture
def state(monkeypatch):
    s = SimpleNamespace(foundry_client=SimpleNamespace(is_connected=True))
    monkeypatch.setattr(campaign_routes, "_select_campaign_world", AsyncMock(return_value=None))
    campaign_routes._deploys_in_progress.clear()
    return s


def _request(name="The Sunder-Weave"):
    return campaign_routes.CampaignDeployRequest(campaign_name=name)


def test_a_second_deploy_of_the_same_campaign_is_refused_while_the_first_runs(monkeypatch, state):
    gate = asyncio.Event()
    calls = []

    async def slow_deploy(name, st):
        calls.append(name)
        await gate.wait()
        return {"status": "complete"}

    monkeypatch.setattr(campaign_routes, "_deploy_campaign_to_world", slow_deploy)

    async def scenario():
        first = asyncio.create_task(campaign_routes.deploy_campaign_endpoint(_request(), state))
        await asyncio.sleep(0)  # let the first request take its place
        second = await campaign_routes.deploy_campaign_endpoint(_request(), state)
        gate.set()
        return await first, second

    first, second = asyncio.run(scenario())

    assert calls == ["The Sunder-Weave"]
    assert first.status == "complete"
    assert second.status == "error" and "already running" in second.error


def test_the_name_is_released_after_a_deploy_finishes(monkeypatch, state):
    monkeypatch.setattr(campaign_routes, "_deploy_campaign_to_world",
                        AsyncMock(return_value={"status": "complete"}))

    asyncio.run(campaign_routes.deploy_campaign_endpoint(_request(), state))
    again = asyncio.run(campaign_routes.deploy_campaign_endpoint(_request(), state))

    assert again.status == "complete"
    assert campaign_routes._deploys_in_progress == set()


def test_the_name_is_released_when_the_deploy_raises(monkeypatch, state):
    monkeypatch.setattr(campaign_routes, "_deploy_campaign_to_world",
                        AsyncMock(side_effect=RuntimeError("foundry gone")))

    failed = asyncio.run(campaign_routes.deploy_campaign_endpoint(_request(), state))

    assert failed.status == "error"
    assert campaign_routes._deploys_in_progress == set()


def test_different_campaigns_deploy_side_by_side(monkeypatch, state):
    gate = asyncio.Event()

    async def slow_deploy(name, st):
        await gate.wait()
        return {"status": "complete"}

    monkeypatch.setattr(campaign_routes, "_deploy_campaign_to_world", slow_deploy)

    async def scenario():
        a = asyncio.create_task(campaign_routes.deploy_campaign_endpoint(_request("A"), state))
        await asyncio.sleep(0)
        b = asyncio.create_task(campaign_routes.deploy_campaign_endpoint(_request("B"), state))
        await asyncio.sleep(0)
        gate.set()
        return await a, await b

    a, b = asyncio.run(scenario())

    assert a.status == "complete" and b.status == "complete"
