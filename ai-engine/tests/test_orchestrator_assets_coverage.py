"""Coverage for campaign/orchestrator_assets.py branches the existing suites
(test_generate_assets.py, test_build_campaign_uploads.py,
test_scene_layout_validation.py) don't reach: prompt-building fallbacks,
location maps, portrait/prologue generation success paths, the upload
failure/skip branches, the regenerate-time pipeline, and the placeholder
monster-portrait pass.

Tests assert on actual outputs (counts, dict fields, call arguments) rather
than "it didn't raise" — see .coveragerc's warning about toothless tests.
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from campaign.orchestrator import CampaignOrchestrator


# ── _build_scene_prompt / _build_location_prompt ────────────────────────────

def test_build_scene_prompt_includes_atmosphere_lighting_and_description_keywords():
    orch = CampaignOrchestrator()
    scene = {
        "type": "tavern",
        "description": "A Smoky Haunted room full of Rowdy Patrons",
        "atmosphere": "tense",
        "lighting": "dim candlelight",
    }

    prompt = orch._build_scene_prompt(scene)

    assert "top-down tavern interior" in prompt
    assert "tense atmosphere" in prompt
    assert "dim candlelight lighting" in prompt
    # Capitalized words longer than 4 chars, up to 3 of them
    assert "Smoky" in prompt
    assert "Haunted" in prompt


def test_build_location_prompt_known_type_uses_features_and_adjectives():
    orch = CampaignOrchestrator()
    location = {
        "type": "dungeon",
        "description": "Crumbling stonework hides Forgotten Treasures",
        "key_features": ["a broken altar", "flooded corridor", "collapsed stair", "rune circle"],
    }

    prompt = orch._build_location_prompt(location)

    assert "top-down dungeon complex" in prompt
    assert "a broken altar, flooded corridor, collapsed stair, rune circle" in prompt
    assert "Forgotten" in prompt
    assert "fantasy cartography style" in prompt


def test_build_location_prompt_unknown_type_falls_back_to_generic_isometric():
    orch = CampaignOrchestrator()
    location = {"type": "spaceport", "description": "", "key_features": []}

    prompt = orch._build_location_prompt(location)

    assert prompt.startswith("isometric spaceport overview")


# ── _generate_location_maps ─────────────────────────────────────────────────

def _no_provider(err="no backend"):
    return {"status": "error", "error": err, "provider": "none"}


def _success(name="map.png"):
    return {"status": "success", "output_file": f"out/{name}", "provider": "comfyui"}


def test_generate_location_maps_records_success_and_sets_map_file(tmp_path):
    loc = {"name": "The Sunken Keep", "type": "ruins", "map_needed": True}
    gen = MagicMock()
    gen.generate_map = AsyncMock(return_value=_success("keep.png"))
    results = {"maps": []}

    asyncio.run(CampaignOrchestrator()._generate_location_maps([loc], gen, Path(tmp_path), results))

    assert results["maps"] == [{
        "location": "The Sunken Keep", "type": "location_map",
        "file": "out/keep.png", "provider": "comfyui",
    }]
    assert loc["map_file"] == "keep.png"


def test_generate_location_maps_absorbs_failure_status_without_recording_it(tmp_path):
    loc = {"name": "Nowhere", "type": "ruins", "map_needed": True}
    gen = MagicMock()
    gen.generate_map = AsyncMock(return_value=_no_provider())
    results = {"maps": []}

    asyncio.run(CampaignOrchestrator()._generate_location_maps([loc], gen, Path(tmp_path), results))

    assert results["maps"] == []
    assert "map_file" not in loc


def test_generate_location_maps_absorbs_raised_exception(tmp_path):
    loc = {"name": "Nowhere", "type": "ruins", "map_needed": True}
    gen = MagicMock()
    gen.generate_map = AsyncMock(side_effect=ConnectionError("down"))
    results = {"maps": []}

    # Must not raise — one bad location must not abort the whole build.
    asyncio.run(CampaignOrchestrator()._generate_location_maps([loc], gen, Path(tmp_path), results))

    assert results["maps"] == []


# ── _generate_portraits success path (file rename) ──────────────────────────

def test_generate_portraits_renames_raw_output_to_per_npc_filename(tmp_path):
    npc = {"name": "Elara Vane", "description": "a sharp-eyed scout"}
    portraits_dir = Path(tmp_path) / "portraits"
    portraits_dir.mkdir()
    raw_file = portraits_dir / "comfyui_output_0001.png"
    raw_file.write_bytes(b"png")

    gen = MagicMock()
    gen.generate_portrait = AsyncMock(return_value={
        "status": "success", "output_file": str(raw_file), "provider": "comfyui",
    })
    results = {"portraits": []}

    asyncio.run(CampaignOrchestrator()._generate_portraits([npc], gen, Path(tmp_path), results))

    assert not raw_file.exists(), "raw ComfyUI output should have been renamed away"
    # sanitize_filename lowercases but doesn't replace spaces, so the name
    # keeps its space; this pins that (surprising) actual behavior.
    expected = portraits_dir / "portrait_elara vane.png"
    assert expected.exists()
    assert results["portraits"] == [{
        "npc": "Elara Vane", "file": str(expected), "provider": "comfyui",
    }]
    assert npc["portrait_file"] == "portrait_elara vane.png"


def test_generate_portraits_absorbs_failure(tmp_path):
    npc = {"name": "Ghost", "description": "nothing"}
    gen = MagicMock()
    gen.generate_portrait = AsyncMock(return_value=_no_provider())
    results = {"portraits": []}

    asyncio.run(CampaignOrchestrator()._generate_portraits([npc], gen, Path(tmp_path), results))

    assert results["portraits"] == []
    assert "portrait_file" not in npc


# ── _generate_prologue_panels ────────────────────────────────────────────────

def test_prologue_panel_missing_image_prompt_is_skipped(tmp_path):
    prologue = {"vessel": "tome", "panels": [{"title": "Cold Open"}]}  # no image_prompt
    gen = MagicMock()
    gen.generate_prologue_panel = AsyncMock()
    results = {}

    asyncio.run(CampaignOrchestrator()._generate_prologue_panels(prologue, gen, Path(tmp_path), results))

    gen.generate_prologue_panel.assert_not_awaited()
    assert results.get("prologue_panels", []) == []


def test_prologue_panel_success_renames_file_and_records_entry(tmp_path):
    prologue_dir = Path(tmp_path) / "prologue"
    prologue_dir.mkdir()
    raw = prologue_dir / "raw_panel.png"
    raw.write_bytes(b"png")
    panel = {"title": "The Omen", "image_prompt": "a crow over a burning tower"}
    prologue = {"vessel": "scroll", "panels": [panel]}

    gen = MagicMock()
    gen.generate_prologue_panel = AsyncMock(return_value={
        "status": "success", "output_file": str(raw), "provider": "comfyui",
    })
    results = {}

    asyncio.run(CampaignOrchestrator()._generate_prologue_panels(prologue, gen, Path(tmp_path), results))

    expected = prologue_dir / "prologue_panel_01.png"
    assert expected.exists()
    assert not raw.exists()
    assert results["prologue_panels"] == [{
        "panel_index": 0, "title": "The Omen", "file": str(expected), "provider": "comfyui",
    }]
    assert panel["image_file"] == "prologue_panel_01.png"


def test_prologue_panel_non_dict_entries_are_skipped_not_raised(tmp_path):
    prologue = {"vessel": "tome", "panels": ["not-a-dict", {"image_prompt": ""}]}
    gen = MagicMock()
    gen.generate_prologue_panel = AsyncMock()
    results = {}

    asyncio.run(CampaignOrchestrator()._generate_prologue_panels(prologue, gen, Path(tmp_path), results))

    gen.generate_prologue_panel.assert_not_awaited()


# ── Layout-guided ControlNet generation branch of _generate_scene_maps ──────

def test_layout_guided_generation_uses_controlnet_when_mask_exists(tmp_path):
    mask_path = Path(tmp_path) / "mask.png"
    mask_path.write_bytes(b"mask")
    scene = {
        "name": "The Crypt", "type": "dungeon",
        "scene_setup": {
            "grid_width": 16, "grid_height": 12,
            "walls": [[0, 0, 16, 0], [16, 0, 16, 12], [16, 12, 0, 12], [0, 12, 0, 0]],
            "doors": [{"c": [4, 0, 8, 0], "door": 1, "ds": 0}],
        },
    }
    gen = MagicMock()
    gen.generate_layout_mask = AsyncMock(return_value=mask_path)
    gen.generate_map_controlnet = AsyncMock(return_value=_success("crypt.png"))
    results = {"maps": []}

    asyncio.run(CampaignOrchestrator()._generate_scene_maps([scene], gen, Path(tmp_path), results))

    gen.generate_map_controlnet.assert_awaited_once()
    kwargs = gen.generate_map_controlnet.await_args.kwargs
    assert kwargs["layout_image_path"] == str(mask_path)
    assert kwargs["style"] == "dungeon"
    assert results["maps"][0]["file"] == "out/crypt.png"
    assert scene["map_file"] == "crypt.png"


def test_layout_guided_generation_exception_falls_back_to_text_only(tmp_path):
    scene = {
        "name": "The Hall", "type": "castle",
        "scene_setup": {
            "grid_width": 16, "grid_height": 12,
            "walls": [[0, 0, 16, 0], [16, 0, 16, 12], [16, 12, 0, 12], [0, 12, 0, 0]],
            "doors": [{"c": [4, 0, 8, 0], "door": 1, "ds": 0}],
        },
    }
    gen = MagicMock()
    gen.generate_layout_mask = AsyncMock(side_effect=RuntimeError("ControlNet model missing"))
    gen.generate_map = AsyncMock(return_value=_success("hall.png"))
    results = {"maps": []}

    asyncio.run(CampaignOrchestrator()._generate_scene_maps([scene], gen, Path(tmp_path), results))

    gen.generate_map.assert_awaited_once()
    assert results["maps"][0]["file"] == "out/hall.png"


def test_layout_guided_generation_text_only_fallback_also_failing_records_error_result(tmp_path):
    scene = {
        "name": "The Pit", "type": "dungeon",
        "scene_setup": {
            "grid_width": 16, "grid_height": 12,
            "walls": [[0, 0, 16, 0], [16, 0, 16, 12], [16, 12, 0, 12], [0, 12, 0, 0]],
            "doors": [{"c": [4, 0, 8, 0], "door": 1, "ds": 0}],
        },
    }
    gen = MagicMock()
    gen.generate_layout_mask = AsyncMock(side_effect=RuntimeError("ControlNet model missing"))
    gen.generate_map = AsyncMock(side_effect=ConnectionError("ComfyUI down"))
    results = {"maps": []}

    # Neither path works, but the whole scene build must not raise.
    asyncio.run(CampaignOrchestrator()._generate_scene_maps([scene], gen, Path(tmp_path), results))

    assert results["maps"] == []


# ── upload_maps_to_foundry / upload_portraits_to_foundry failure+skip paths ─

class _StubFoundry:
    is_connected = True

    def __init__(self, upload_ok=True, upload_error="relay 408"):
        self.upload_ok = upload_ok
        self.upload_error = upload_error

    async def upload_file(self, **kw):
        if not self.upload_ok:
            raise RuntimeError(self.upload_error)
        return {"path": "served/path.png"}


def test_upload_maps_to_foundry_records_failure_when_upload_raises(tmp_path):
    (tmp_path / "crypt.png").write_bytes(b"png")
    campaign_data = {"scenes": [{"name": "The Crypt", "map_file": "crypt.png"}]}
    client = _StubFoundry(upload_ok=False, upload_error="relay 408")

    summary = asyncio.run(
        CampaignOrchestrator().upload_maps_to_foundry(campaign_data, client, Path(tmp_path), "camp")
    )

    assert summary["uploaded"] == 0
    assert summary["failed"] == 1
    assert "relay 408" in summary["errors"][0]
    assert "background_src" not in campaign_data["scenes"][0]


def test_upload_portraits_to_foundry_skips_when_not_connected(tmp_path):
    client = _StubFoundry()
    client.is_connected = False
    campaign_data = {"npcs": [{"name": "Elara", "portrait_file": "elara.png"}]}

    summary = asyncio.run(
        CampaignOrchestrator().upload_portraits_to_foundry(campaign_data, client, Path(tmp_path), "camp")
    )

    assert summary["uploaded"] == 0
    assert "not connected" in summary["errors"][0].lower()


def test_upload_portraits_to_foundry_skips_npc_without_file_or_missing_on_disk(tmp_path):
    client = _StubFoundry()
    campaign_data = {"npcs": [
        {"name": "No Portrait Field"},
        {"name": "File Missing", "portrait_file": "ghost.png"},
    ]}

    summary = asyncio.run(
        CampaignOrchestrator().upload_portraits_to_foundry(campaign_data, client, Path(tmp_path), "camp")
    )

    assert summary["uploaded"] == 0
    assert summary["failed"] == 0  # neither case even attempts upload
    assert summary["errors"] == []


def test_upload_portraits_to_foundry_records_failure_when_upload_raises(tmp_path):
    portraits_dir = Path(tmp_path) / "portraits"
    portraits_dir.mkdir()
    (portraits_dir / "elara.png").write_bytes(b"png")
    campaign_data = {"npcs": [{"name": "Elara", "portrait_file": "elara.png"}]}
    client = _StubFoundry(upload_ok=False, upload_error="timeout")

    summary = asyncio.run(
        CampaignOrchestrator().upload_portraits_to_foundry(campaign_data, client, Path(tmp_path), "camp")
    )

    assert summary["uploaded"] == 0
    assert summary["failed"] == 1
    assert "timeout" in summary["errors"][0]
    assert "portrait_src" not in campaign_data["npcs"][0]


# ── upload_prologue_to_foundry ───────────────────────────────────────────────

def test_upload_prologue_to_foundry_skips_when_not_connected():
    client = _StubFoundry()
    client.is_connected = False
    campaign_data = {"prologue": {"panels": [{"image_file": "p1.png"}]}}

    summary = asyncio.run(
        CampaignOrchestrator().upload_prologue_to_foundry(campaign_data, client, Path("/tmp"), "camp")
    )

    assert summary["uploaded"] == 0
    assert "not connected" in summary["errors"][0].lower()


def test_upload_prologue_to_foundry_no_prologue_or_no_panels_is_a_noop(tmp_path):
    client = _StubFoundry()

    r1 = asyncio.run(
        CampaignOrchestrator().upload_prologue_to_foundry({}, client, Path(tmp_path), "camp")
    )
    r2 = asyncio.run(
        CampaignOrchestrator().upload_prologue_to_foundry(
            {"prologue": {"panels": []}}, client, Path(tmp_path), "camp"
        )
    )

    assert r1 == {"uploaded": 0, "failed": 0, "errors": []}
    assert r2 == {"uploaded": 0, "failed": 0, "errors": []}


def test_upload_prologue_to_foundry_uploads_each_panel_and_sets_image_src(tmp_path):
    prologue_dir = Path(tmp_path) / "prologue"
    prologue_dir.mkdir()
    (prologue_dir / "prologue_panel_01.png").write_bytes(b"png")
    campaign_data = {"prologue": {"panels": [
        {"title": "Cold Open", "image_file": "prologue_panel_01.png"},
        {"title": "Missing On Disk", "image_file": "ghost.png"},
        {"title": "No File"},
    ]}}
    client = _StubFoundry()

    summary = asyncio.run(
        CampaignOrchestrator().upload_prologue_to_foundry(campaign_data, client, Path(tmp_path), "camp")
    )

    assert summary["uploaded"] == 1
    assert summary["failed"] == 0
    panels = campaign_data["prologue"]["panels"]
    assert panels[0]["image_src"] == "served/path.png"
    assert "image_src" not in panels[1]
    assert "image_src" not in panels[2]


def test_upload_prologue_to_foundry_records_upload_failure():
    client = _StubFoundry(upload_ok=False, upload_error="connection reset")

    import tempfile
    with tempfile.TemporaryDirectory() as td:
        prologue_dir = Path(td) / "prologue"
        prologue_dir.mkdir()
        (prologue_dir / "p1.png").write_bytes(b"png")
        campaign_data = {"prologue": {"panels": [{"title": "X", "image_file": "p1.png"}]}}

        summary = asyncio.run(
            CampaignOrchestrator().upload_prologue_to_foundry(campaign_data, client, Path(td), "camp")
        )

    assert summary["uploaded"] == 0
    assert summary["failed"] == 1
    assert "connection reset" in summary["errors"][0]


# ── _attach_map_to_scene remaining branches ──────────────────────────────────

class _SceneFoundry:
    def __init__(self, scenes=None, update_scene_response=None, update_scene_raises=None):
        self.scenes = scenes or {}
        self.update_scene_response = update_scene_response
        self.update_scene_calls = []
        self._raises = update_scene_raises

    async def get_scene_by_name(self, name):
        return self.scenes.get(name)

    async def update_scene(self, name, data):
        if self._raises:
            raise self._raises
        self.update_scene_calls.append((name, data))
        return self.update_scene_response


def test_attach_map_to_scene_creates_base_level_when_scene_has_no_levels():
    # A non-empty dict with no "levels" key — an empty dict would be falsy
    # and misread as "scene not found", which isn't what we're exercising here.
    client = _SceneFoundry(scenes={"Empty Room": {"_id": "scene1"}}, update_scene_response={"type": "ok"})
    summary = {"scenes_attached": 0, "errors": []}

    asyncio.run(CampaignOrchestrator()._attach_map_to_scene(client, {"name": "Empty Room"}, "new.png", summary))

    assert summary["scenes_attached"] == 1
    name, data = client.update_scene_calls[0]
    assert data["levels"] == [{"name": "Base Level", "background": {
        "src": "new.png", "offsetX": 0, "offsetY": 0, "scaleX": 1.0, "scaleY": 1.0,
    }}]


def test_attach_map_to_scene_records_foundry_error_response():
    client = _SceneFoundry(
        scenes={"X": {"levels": [{"name": "Base Level"}]}},
        update_scene_response={"type": "error", "error": "permission denied"},
    )
    summary = {"scenes_attached": 0, "errors": []}

    asyncio.run(CampaignOrchestrator()._attach_map_to_scene(client, {"name": "X"}, "new.png", summary))

    assert summary["scenes_attached"] == 0
    assert "permission denied" in summary["errors"][0]


def test_attach_map_to_scene_records_falsy_response_as_network_timeout():
    client = _SceneFoundry(scenes={"X": {"levels": [{"name": "Base Level"}]}}, update_scene_response=None)
    summary = {"scenes_attached": 0, "errors": []}

    asyncio.run(CampaignOrchestrator()._attach_map_to_scene(client, {"name": "X"}, "new.png", summary))

    assert summary["scenes_attached"] == 0
    assert "timeout" in summary["errors"][0].lower()


def test_attach_map_to_scene_records_exception():
    client = _SceneFoundry(
        scenes={"X": {"levels": [{"name": "Base Level"}]}},
        update_scene_raises=RuntimeError("socket closed"),
    )
    summary = {"scenes_attached": 0, "errors": []}

    asyncio.run(CampaignOrchestrator()._attach_map_to_scene(client, {"name": "X"}, "new.png", summary))

    assert summary["scenes_attached"] == 0
    assert "socket closed" in summary["errors"][0]


# ── _attach_portrait_to_actor remaining branches ─────────────────────────────

def test_attach_portrait_to_actor_missing_name_field_raises_key_error_is_caught():
    client = MagicMock()
    summary = {"portraits_attached": 0, "errors": []}

    asyncio.run(CampaignOrchestrator()._attach_portrait_to_actor(client, {}, "src.png", {}, summary))

    assert summary["portraits_attached"] == 0
    assert "missing field" in summary["errors"][0]


def test_attach_portrait_to_actor_records_generic_exception():
    client = MagicMock()
    client.update_entity = AsyncMock(side_effect=RuntimeError("relay gone"))
    summary = {"portraits_attached": 0, "errors": []}

    asyncio.run(CampaignOrchestrator()._attach_portrait_to_actor(
        client, {"name": "Elara"}, "src.png", {"Elara": "Actor.1"}, summary
    ))

    assert summary["portraits_attached"] == 0
    assert "relay gone" in summary["errors"][0]


# ── _default_monster_icon ────────────────────────────────────────────────────

@pytest.mark.parametrize("name,expected", [
    ("Skeleton Warrior", "icons/svg/skull.svg"),
    ("Flame Imp", "icons/svg/fire.svg"),
    ("Giant Rat", "icons/svg/mystery-man.svg"),
])
def test_default_monster_icon_picks_theme_by_name(name, expected):
    assert CampaignOrchestrator._default_monster_icon(name) == expected


# ── regenerate_assets_for_campaign ───────────────────────────────────────────

class _FakeStore:
    """Stand-in for campaign.vault.CampaignStore used inside regenerate."""

    def __init__(self, campaign_name, vault_path, campaign_data=None, deployment=None, exists=True):
        self.name = campaign_name
        self.exists = exists
        self.safe_name = "the-crypt"
        self.maps_dir = Path(vault_path or "/tmp") / "maps"
        self._campaign_data = campaign_data or {}
        self._deployment = deployment or {}
        self.saved = None

    async def load(self, normalize=False):
        return self._campaign_data

    async def load_deployment(self):
        return self._deployment

    async def save(self, data):
        self.saved = data


class _FakeMapGenerator:
    def __init__(self, comfy_up=True):
        self.comfy_up = comfy_up
        self.closed = False

    async def health_check(self):
        return {"comfyui": self.comfy_up}

    async def close(self):
        self.closed = True


def test_regenerate_assets_for_campaign_reports_error_when_campaign_not_found(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "campaign.vault.CampaignStore",
        lambda name, vault_path: _FakeStore(name, vault_path, exists=False),
    )
    orch = CampaignOrchestrator()

    summary = asyncio.run(orch.regenerate_assets_for_campaign("Nope"))

    assert summary["status"] == "error"
    assert "not found" in summary["errors"][0]


def test_regenerate_assets_for_campaign_reports_error_when_comfyui_unreachable(tmp_path, monkeypatch):
    store = _FakeStore("The Crypt", str(tmp_path), campaign_data={"scenes": [], "npcs": []})
    monkeypatch.setattr("campaign.vault.CampaignStore", lambda name, vault_path: store)
    monkeypatch.setattr("campaign.map_generator.MapGenerator", lambda **kw: _FakeMapGenerator(comfy_up=False))
    orch = CampaignOrchestrator()

    summary = asyncio.run(orch.regenerate_assets_for_campaign("The Crypt"))

    assert summary["status"] == "error"
    assert "ComfyUI" in summary["errors"][0]
    assert store.saved is None  # never reached the save step


def test_regenerate_assets_for_campaign_uploads_and_attaches_when_connected(tmp_path, monkeypatch):
    (tmp_path / "maps").mkdir()
    (tmp_path / "maps" / "crypt.png").write_bytes(b"png")
    (tmp_path / "maps" / "portraits").mkdir()
    (tmp_path / "maps" / "portraits" / "elara.png").write_bytes(b"png")

    campaign_data = {
        "scenes": [{"name": "The Crypt", "map_file": "crypt.png"}],
        "npcs": [{"name": "Elara", "portrait_file": "elara.png"}],
    }
    deployment = {
        "npcs": [{"name": "Elara", "uuid": "Actor.abc", "status": "created"}],
        "scenes": ["The Crypt"],
    }
    store = _FakeStore("The Crypt", str(tmp_path), campaign_data=campaign_data, deployment=deployment)
    monkeypatch.setattr("campaign.vault.CampaignStore", lambda name, vault_path: store)
    fake_gen = _FakeMapGenerator(comfy_up=True)
    monkeypatch.setattr("campaign.map_generator.MapGenerator", lambda **kw: fake_gen)

    orch = CampaignOrchestrator()
    orch.generate_assets = AsyncMock(return_value={"total_maps": 1, "total_portraits": 1})
    orch._attach_map_to_scene = AsyncMock(side_effect=lambda client, scene, src, summary: summary.__setitem__(
        "scenes_attached", summary["scenes_attached"] + 1
    ))
    orch._attach_portrait_to_actor = AsyncMock(side_effect=lambda client, npc, src, uuid_map, summary: summary.__setitem__(
        "portraits_attached", summary["portraits_attached"] + 1
    ))
    orch.enrich_scenes = AsyncMock(return_value={"enriched": 2, "errors": []})

    import campaign.orchestrator_assets as oa
    uploaded = AsyncMock(return_value={"ok": True, "src": "served.png"})
    monkeypatch.setattr(oa, "upload_image", uploaded)

    client = MagicMock()
    client.is_connected = True

    summary = asyncio.run(orch.regenerate_assets_for_campaign("The Crypt", foundry_client=client))

    assert summary["status"] == "completed"
    assert summary["maps_generated"] == 1
    assert summary["portraits_generated"] == 1
    assert summary["scenes_attached"] == 1
    assert summary["portraits_attached"] == 1
    assert summary["scenes_enriched"] == 2
    assert campaign_data["scenes"][0]["background_src"] == "served.png"
    assert campaign_data["npcs"][0]["portrait_src"] == "served.png"
    assert store.saved is campaign_data
    assert fake_gen.closed is True


def test_regenerate_assets_for_campaign_notes_when_foundry_not_connected(tmp_path, monkeypatch):
    (tmp_path / "maps").mkdir()
    campaign_data = {"scenes": [], "npcs": []}
    store = _FakeStore("The Crypt", str(tmp_path), campaign_data=campaign_data)
    monkeypatch.setattr("campaign.vault.CampaignStore", lambda name, vault_path: store)
    fake_gen = _FakeMapGenerator(comfy_up=True)
    monkeypatch.setattr("campaign.map_generator.MapGenerator", lambda **kw: fake_gen)

    orch = CampaignOrchestrator()
    orch.generate_assets = AsyncMock(return_value={"total_maps": 0, "total_portraits": 0})

    summary = asyncio.run(orch.regenerate_assets_for_campaign("The Crypt", foundry_client=None))

    assert summary["status"] == "completed"
    assert any("not attached" in e for e in summary["errors"])
    assert summary["scenes_attached"] == 0


# ── _generate_placeholder_portraits ──────────────────────────────────────────

def test_generate_placeholder_portraits_noop_when_not_connected(tmp_path):
    client = MagicMock()
    client.is_connected = False

    summary = asyncio.run(
        CampaignOrchestrator()._generate_placeholder_portraits(client, "Camp", output_dir=tmp_path)
    )

    assert summary == {"generated": 0, "fallback_icon": 0, "errors": []}


def test_generate_placeholder_portraits_noop_when_nothing_pending(tmp_path):
    client = MagicMock()
    client.is_connected = True
    client.execute_js = AsyncMock(return_value={"result": []})

    summary = asyncio.run(
        CampaignOrchestrator()._generate_placeholder_portraits(client, "Camp", output_dir=tmp_path)
    )

    assert summary["generated"] == 0
    assert summary["errors"] == []


def test_generate_placeholder_portraits_lookup_error_is_recorded(tmp_path):
    client = MagicMock()
    client.is_connected = True
    client.execute_js = AsyncMock(side_effect=RuntimeError("js failed"))

    summary = asyncio.run(
        CampaignOrchestrator()._generate_placeholder_portraits(client, "Camp", output_dir=tmp_path)
    )

    assert summary["generated"] == 0
    assert "js failed" in summary["errors"][0]


def test_generate_placeholder_portraits_generates_and_uploads_when_comfyui_up(tmp_path, monkeypatch):
    client = MagicMock()
    client.is_connected = True
    client.execute_js = AsyncMock(return_value={
        "result": [{"name": "Goblin Thug", "uuid": "Actor.goblin1"}],
    })
    client.update_entity = AsyncMock(return_value={"type": "ok"})

    fake_gen = MagicMock()
    fake_gen.health_check = AsyncMock(return_value={"comfyui": True})
    raw_portrait = Path(tmp_path) / "raw.png"

    async def _close():
        fake_gen.closed = True
    fake_gen.close = _close
    fake_gen.generate_portrait = AsyncMock(return_value={
        "status": "success", "output_file": str(raw_portrait),
    })
    monkeypatch.setattr("campaign.map_generator.MapGenerator", lambda **kw: fake_gen)

    import campaign.orchestrator_assets as oa
    monkeypatch.setattr(oa, "upload_image", AsyncMock(return_value={"ok": True, "src": "portrait.png"}))

    # raw_portrait doesn't need to physically exist; upload_image is mocked
    # and never touches the filesystem in this test.
    summary = asyncio.run(
        CampaignOrchestrator()._generate_placeholder_portraits(client, "Camp", output_dir=tmp_path)
    )

    assert summary["generated"] == 1
    assert summary["fallback_icon"] == 0
    assert summary["errors"] == []
    update_kwargs = client.update_entity.await_args.kwargs
    assert update_kwargs["uuid"] == "Actor.goblin1"
    assert update_kwargs["data"]["img"] == "portrait.png"
    assert update_kwargs["data"]["flags"]["ai-gm"]["needs_portrait"] is False


def test_generate_placeholder_portraits_falls_back_to_icon_when_comfyui_down(tmp_path, monkeypatch):
    client = MagicMock()
    client.is_connected = True
    client.execute_js = AsyncMock(return_value={
        "result": [{"name": "Skeleton Grunt", "uuid": "Actor.skel1"}],
    })
    client.update_entity = AsyncMock(return_value={"type": "ok"})

    fake_gen = MagicMock()
    fake_gen.health_check = AsyncMock(return_value={"comfyui": False})
    fake_gen.generate_portrait = AsyncMock()

    async def _close():
        pass
    fake_gen.close = _close
    monkeypatch.setattr("campaign.map_generator.MapGenerator", lambda **kw: fake_gen)

    summary = asyncio.run(
        CampaignOrchestrator()._generate_placeholder_portraits(client, "Camp", output_dir=tmp_path)
    )

    assert summary["generated"] == 0
    assert summary["fallback_icon"] == 1
    fake_gen.generate_portrait.assert_not_awaited()
    update_kwargs = client.update_entity.await_args.kwargs
    assert update_kwargs["data"]["img"] == "icons/svg/skull.svg"


def test_generate_placeholder_portraits_skips_actor_with_no_uuid(tmp_path, monkeypatch):
    client = MagicMock()
    client.is_connected = True
    client.execute_js = AsyncMock(return_value={"result": [{"name": "No UUID"}]})
    client.update_entity = AsyncMock()

    fake_gen = MagicMock()
    fake_gen.health_check = AsyncMock(return_value={"comfyui": False})

    async def _close():
        pass
    fake_gen.close = _close
    monkeypatch.setattr("campaign.map_generator.MapGenerator", lambda **kw: fake_gen)

    summary = asyncio.run(
        CampaignOrchestrator()._generate_placeholder_portraits(client, "Camp", output_dir=tmp_path)
    )

    assert summary["generated"] == 0
    assert summary["fallback_icon"] == 0
    client.update_entity.assert_not_awaited()


def test_generate_placeholder_portraits_update_entity_failure_is_recorded(tmp_path, monkeypatch):
    client = MagicMock()
    client.is_connected = True
    client.execute_js = AsyncMock(return_value={
        "result": [{"name": "Skeleton Grunt", "uuid": "Actor.skel1"}],
    })
    client.update_entity = AsyncMock(side_effect=RuntimeError("actor gone"))

    fake_gen = MagicMock()
    fake_gen.health_check = AsyncMock(return_value={"comfyui": False})

    async def _close():
        pass
    fake_gen.close = _close
    monkeypatch.setattr("campaign.map_generator.MapGenerator", lambda **kw: fake_gen)

    summary = asyncio.run(
        CampaignOrchestrator()._generate_placeholder_portraits(client, "Camp", output_dir=tmp_path)
    )

    assert summary["fallback_icon"] == 1
    assert "actor gone" in summary["errors"][0]
