"""Checks for the unified upload path (campaign/assets.py) and the orchestrator
methods that use it: upload_maps_to_foundry, upload_portraits_to_foundry, and
the regenerate-time scene/actor attach helpers.

Was 3 divergent copies of "read bytes, upload, resolve path" across
orchestrator.py — the divergence is exactly what caused "regenerate works but
build doesn't" bugs in the past, so this file pins the behavior of all three
call sites against one shared implementation.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from campaign.assets import resolve_uploaded_path, upload_image
from campaign.orchestrator import CampaignOrchestrator


def test_prefers_relay_reported_path_and_unquotes_it():
    assert resolve_uploaded_path({"path": "ai-gm-maps/the%20crypt/map.png"}, "fallback") \
        == "ai-gm-maps/the crypt/map.png"


def test_falls_back_when_path_missing_or_response_malformed():
    assert resolve_uploaded_path({"path": ""}, "fallback") == "fallback"
    assert resolve_uploaded_path({}, "fallback") == "fallback"
    assert resolve_uploaded_path(None, "fallback") == "fallback"
    assert resolve_uploaded_path("not-a-dict", "fallback") == "fallback"


class StubFoundry:
    """Configurable foundry client for exercising upload/attach paths."""

    is_connected = True

    def __init__(self, upload_response=None, upload_error=None, scenes=None):
        self.upload_response = upload_response if upload_response is not None else {"path": "served/path.png"}
        self.upload_error = upload_error
        self.scenes = scenes or {}
        self.update_scene_calls = []
        self.update_entity_calls = []
        self.update_actor_calls = []
        self.update_scene_response = {"type": "ok"}
        self.update_entity_response = {"type": "ok"}
        self.update_actor_response = {"type": "ok"}

    async def upload_file(self, **kw):
        if self.upload_error:
            raise self.upload_error
        return self.upload_response

    async def get_scene_by_name(self, name):
        return self.scenes.get(name)

    async def update_scene(self, name, data):
        self.update_scene_calls.append((name, data))
        return self.update_scene_response

    async def update_entity(self, **kw):
        self.update_entity_calls.append(kw)
        return self.update_entity_response

    async def update_actor(self, **kw):
        self.update_actor_calls.append(kw)
        return self.update_actor_response


def test_upload_image_success_and_failure(tmp_path):
    img = tmp_path / "map.png"
    img.write_bytes(b"fake-png")

    ok = asyncio.run(upload_image(
        StubFoundry(upload_response={"path": "ai-gm-maps/x/map.png"}),
        img, "ai-gm-maps/x", "map.png", "fallback.png",
    ))
    assert ok == {"ok": True, "src": "ai-gm-maps/x/map.png"}

    failed = asyncio.run(upload_image(
        StubFoundry(upload_error=RuntimeError("relay 408")),
        img, "ai-gm-maps/x", "map.png", "fallback.png",
    ))
    assert failed["ok"] is False
    assert "relay 408" in failed["error"]


def test_upload_maps_to_foundry_sets_background_src_and_counts(tmp_path):
    (tmp_path / "crypt.png").write_bytes(b"png")
    campaign_data = {
        "scenes": [
            {"name": "The Crypt", "map_file": "crypt.png"},
            {"name": "No File Scene"},  # no map_file — skipped, not counted
            {"name": "Missing On Disk", "map_file": "ghost.png"},  # file doesn't exist
        ]
    }
    client = StubFoundry(upload_response={"path": "ai-gm-maps/camp/crypt.png"})
    orch = CampaignOrchestrator()

    summary = asyncio.run(orch.upload_maps_to_foundry(campaign_data, client, tmp_path, "camp"))

    assert summary["uploaded"] == 1
    assert summary["failed"] == 1
    assert campaign_data["scenes"][0]["background_src"] == "ai-gm-maps/camp/crypt.png"
    assert "Missing On Disk" in summary["errors"][0]


def test_upload_maps_to_foundry_skips_when_not_connected(tmp_path):
    client = StubFoundry()
    client.is_connected = False
    orch = CampaignOrchestrator()
    summary = asyncio.run(orch.upload_maps_to_foundry({"scenes": [{"name": "X", "map_file": "x.png"}]}, client, tmp_path, "camp"))
    assert summary["uploaded"] == 0
    assert "not connected" in summary["errors"][0].lower()


def test_upload_portraits_to_foundry_sets_portrait_src(tmp_path):
    portraits_dir = tmp_path / "portraits"
    portraits_dir.mkdir()
    (portraits_dir / "elara.png").write_bytes(b"png")
    campaign_data = {"npcs": [{"name": "Elara", "portrait_file": "elara.png"}]}
    client = StubFoundry(upload_response={"path": "ai-gm-portraits/camp/elara.png"})
    orch = CampaignOrchestrator()

    summary = asyncio.run(orch.upload_portraits_to_foundry(campaign_data, client, tmp_path, "camp"))

    assert summary["uploaded"] == 1
    assert campaign_data["npcs"][0]["portrait_src"] == "ai-gm-portraits/camp/elara.png"


def test_attach_map_to_scene_preserves_other_levels_and_updates_base():
    scene = {"name": "The Crypt"}
    client = StubFoundry(scenes={
        "The Crypt": {"levels": [{"name": "Upper Floor"}, {"name": "Base Level", "background": {"src": "old.png"}}]}
    })
    orch = CampaignOrchestrator()
    summary = {"scenes_attached": 0, "errors": []}

    asyncio.run(orch._attach_map_to_scene(client, scene, "new.png", summary))

    assert summary["scenes_attached"] == 1
    name, data = client.update_scene_calls[0]
    assert name == "The Crypt"
    levels = data["levels"]
    assert levels[0]["name"] == "Upper Floor"  # untouched
    assert levels[1]["background"]["src"] == "new.png"  # Base Level updated


def test_attach_map_to_scene_reports_scene_not_found():
    client = StubFoundry(scenes={})  # "Ghost Town" not present
    orch = CampaignOrchestrator()
    summary = {"scenes_attached": 0, "errors": []}

    asyncio.run(orch._attach_map_to_scene(client, {"name": "Ghost Town"}, "new.png", summary))

    assert summary["scenes_attached"] == 0
    assert "not found in Foundry" in summary["errors"][0]


def test_attach_portrait_prefers_uuid_map_then_falls_back_to_name():
    client = StubFoundry()
    orch = CampaignOrchestrator()
    summary = {"portraits_attached": 0, "errors": []}

    asyncio.run(orch._attach_portrait_to_actor(
        client, {"name": "Elara"}, "src.png", {"Elara": "Actor.abc123"}, summary
    ))
    assert client.update_entity_calls == [{"uuid": "Actor.abc123", "data": {"img": "src.png"}}]
    assert client.update_actor_calls == []
    assert summary["portraits_attached"] == 1

    summary2 = {"portraits_attached": 0, "errors": []}
    asyncio.run(orch._attach_portrait_to_actor(
        client, {"name": "Beringar"}, "src2.png", {}, summary2  # not in uuid map
    ))
    assert client.update_actor_calls == [{"actor_name": "Beringar", "actor_data": {"img": "src2.png"}}]
    assert summary2["portraits_attached"] == 1


def test_attach_portrait_records_foundry_error_response():
    client = StubFoundry()
    client.update_actor_response = {"type": "error", "error": "actor not found"}
    orch = CampaignOrchestrator()
    summary = {"portraits_attached": 0, "errors": []}

    asyncio.run(orch._attach_portrait_to_actor(client, {"name": "Ghost"}, "src.png", {}, summary))

    assert summary["portraits_attached"] == 0
    assert "actor not found" in summary["errors"][0]


# ── Regression: campaign LLM JSON parsing retries on malformed output ──────
# A single malformed-JSON turn used to be a hard failure with no recourse
# (2026-07-05 "The Forbidden Library" arc extension: "Expecting ':' delimiter").
# json_repair was evaluated and rejected — it mangled this exact error class
# (missing colon) into a wrong structure instead of raising, which risks
# silently deploying corrupted campaign data. Re-prompting the LLM is the
# root-cause fix: it eliminates the bad turn instead of guessing at repair.

class StubLLMResponse:
    def __init__(self, content):
        self.status_code = 200
        self._content = content

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


class StubLLMClient:
    """Returns canned chat-completion contents in sequence, one per post()."""

    def __init__(self, contents):
        self.contents = list(contents)
        self.calls = 0

    async def post(self, endpoint, headers=None, json=None, timeout=None):
        self.calls += 1
        return StubLLMResponse(self.contents.pop(0))


_MALFORMED_ARC_JSON = (
    '{"campaign": {"name": "Test", "description": "x"}, '
    '"npcs" [{"name": "A"}], "scenes": [{"name": "S"}]}'
)
_WELL_FORMED_ARC_JSON = (
    '{"campaign": {"name": "Test", "description": "x"}, '
    '"npcs": [{"name": "A"}], "scenes": [{"name": "S"}]}'
)


def test_retries_on_malformed_json_and_succeeds_on_second_attempt():
    client = StubLLMClient([_MALFORMED_ARC_JSON, _WELL_FORMED_ARC_JSON])
    orch = CampaignOrchestrator()

    data = asyncio.run(orch._post_and_parse_campaign_json(client, "http://fake/chat/completions", {}, {}))

    assert client.calls == 2
    assert data["npcs"] == [{"name": "A"}]
    assert data["scenes"][0]["name"] == "S"


def test_gives_up_after_exhausting_all_retries_on_persistent_malformed_json():
    import json as json_module

    client = StubLLMClient([_MALFORMED_ARC_JSON, _MALFORMED_ARC_JSON, _MALFORMED_ARC_JSON])
    orch = CampaignOrchestrator()

    try:
        asyncio.run(orch._post_and_parse_campaign_json(
            client, "http://fake/chat/completions", {}, {}, max_attempts=3
        ))
        assert False, "expected JSONDecodeError to propagate after exhausting retries"
    except json_module.JSONDecodeError:
        pass
    assert client.calls == 3


def test_does_not_retry_when_first_attempt_is_well_formed():
    client = StubLLMClient([_WELL_FORMED_ARC_JSON])
    orch = CampaignOrchestrator()

    data = asyncio.run(orch._post_and_parse_campaign_json(client, "http://fake/chat/completions", {}, {}))

    assert client.calls == 1
    assert data["npcs"] == [{"name": "A"}]
