"""Coverage-gap tests for campaign.orchestrator_assets: prompt fallbacks,
cinematic art, same-path portrait/panel outputs, actor attach outcomes, and
the regenerate / placeholder-portrait failure branches."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

import campaign.orchestrator_assets as oa
from campaign.orchestrator import CampaignOrchestrator


def _orch():
    return CampaignOrchestrator()


# ─── prompts ──────────────────────────────────────────────────────────────


def test_scene_prompt_vertical_only_walls_and_door_kinds():
    scene = {"type": "weird", "description": "a quiet room",
             "scene_setup": {"walls": [[0, 0, 0, 5]], "doors": [{"door": 0}, {"door": 1}, {"door": 2}, {"door": 9}],
                             "grid_width": 10, "grid_height": 8}}
    p = _orch()._build_scene_prompt(scene)
    assert p.startswith("top-down weird, floorplan: room layout with 1 wall segments, 1 vertical walls,")
    assert "horizontal" not in p
    assert "wall, open doorway, secret door, door, grid 10x8" in p
    assert p.endswith("detailed visual, fantasy illustration style")


def test_scene_prompt_horizontal_only_walls():
    scene = {"type": "crypt", "scene_setup": {"walls": [[0, 2, 6, 2]]}}
    p = _orch()._build_scene_prompt(scene)
    assert "1 horizontal walls" in p and "vertical" not in p and "grid 16x12" in p


def test_location_prompt_without_capitalised_words_adds_no_keywords():
    p = _orch()._build_location_prompt({"type": "keep", "description": "just a plain stone place"})
    assert p == "isometric keep overview, detailed aerial view, fantasy cartography style, vibrant colors"


# ─── cinematic art ────────────────────────────────────────────────────────


class _Artist:
    caps = {"still": True, "clip": True}
    still_result = Path("still.png")
    clip_result = Path("clip.webm")
    avail_calls = 0

    def __init__(self, gen):
        pass

    async def available(self):
        type(self).avail_calls += 1
        return dict(type(self).caps)

    async def still(self, description, out):
        type(self).last_description = description
        return type(self).still_result

    async def clip(self, still, out):
        return type(self).clip_result


@pytest.fixture
def cinema(monkeypatch):
    _Artist.caps = {"still": True, "clip": True}
    _Artist.still_result = Path("still.png")
    _Artist.clip_result = Path("clip.webm")
    _Artist.avail_calls = 0
    monkeypatch.setattr(oa, "CinematicArtist", _Artist)
    monkeypatch.setattr(oa.settings, "cinema_art_enabled", True)
    monkeypatch.setattr(oa.settings, "cinema_video_enabled", True)
    monkeypatch.setattr(oa, "reference_map_for", lambda scene, refs: None)
    o = _orch()
    o._cinematic_caps = None
    o._reference_maps = []
    return o


def _art(o, scene):
    asyncio.run(o._build_cinematic_art(scene, object(), Path("/out")))
    return scene


def test_cinematic_disabled_or_non_social_scene_makes_nothing(cinema, monkeypatch):
    assert _art(cinema, {"type": "dungeon"}) == {"type": "dungeon"}
    monkeypatch.setattr(oa.settings, "cinema_art_enabled", False)
    assert _art(cinema, {"type": "tavern"}) == {"type": "tavern"}
    assert _Artist.avail_calls == 0


def test_cinematic_still_and_clip_recorded_and_caps_cached(cinema):
    scene = _art(cinema, {"type": "Tavern", "description": "A smoky inn", "name": "T"})
    assert scene["_cinematic_still_file"] == "still.png" and scene["_cinematic_clip_file"] == "clip.webm"
    assert _Artist.last_description == "A smoky inn"
    _art(cinema, {"type": "tavern", "map_style": "style", "name": "T2"})
    assert _Artist.last_description == "style"
    _art(cinema, {"type": "tavern"})
    assert _Artist.last_description == "a fantasy location"
    assert _Artist.avail_calls == 1


def test_cinematic_no_still_model_and_empty_still_and_no_clip(cinema, monkeypatch):
    _Artist.caps = {"still": False, "clip": False}
    assert "_cinematic_still_file" not in _art(cinema, {"type": "tavern"})
    cinema._cinematic_caps = None
    _Artist.caps = {"still": True, "clip": True}
    _Artist.still_result = None
    assert "_cinematic_still_file" not in _art(cinema, {"type": "tavern"})
    _Artist.still_result = Path("s.png")
    _Artist.clip_result = None
    s = _art(cinema, {"type": "tavern"})
    assert s["_cinematic_still_file"] == "s.png" and "_cinematic_clip_file" not in s
    monkeypatch.setattr(oa.settings, "cinema_video_enabled", False)
    _Artist.clip_result = Path("c.webm")
    assert "_cinematic_clip_file" not in _art(cinema, {"type": "tavern"})


def test_cinematic_reference_map_beats_drawn_art_and_errors_are_swallowed(cinema, monkeypatch):
    monkeypatch.setattr(oa, "reference_map_for", lambda scene, refs: {"file": "/maps/ref.jpg"})
    monkeypatch.setattr(oa, "map_still", lambda f, out: Path("/o/ref_still.png"))
    scene = _art(cinema, {"type": "city"})
    assert scene["_cinematic_still_file"] == "ref_still.png" and _Artist.avail_calls == 0

    def boom(f, out):
        raise OSError("disk")

    monkeypatch.setattr(oa, "map_still", boom)
    assert "_cinematic_still_file" not in _art(cinema, {"type": "city", "name": "C"})


# ─── same-path outputs and empty prologue ─────────────────────────────────


def test_portrait_already_at_target_path_is_not_moved(tmp_path):
    gen = MagicMock()
    expected = tmp_path / "portraits" / "portrait_bob.png"

    async def gp(**kw):
        expected.parent.mkdir(exist_ok=True)
        expected.write_bytes(b"x")
        return {"status": "success", "output_file": str(expected), "provider": "comfy"}

    gen.generate_portrait = gp
    npc = {"name": "Bob"}
    results = {"portraits": []}
    asyncio.run(_orch()._generate_portraits([npc], gen, tmp_path, results))
    assert expected.read_bytes() == b"x"
    assert results["portraits"] == [{"npc": "Bob", "file": str(expected), "provider": "comfy"}]
    assert npc["portrait_file"] == "portrait_bob.png"


def test_prologue_panel_already_at_target_path_and_empty_prologues(tmp_path):
    target = tmp_path / "prologue" / "prologue_panel_01.png"
    gen = MagicMock()

    async def gp(**kw):
        target.write_bytes(b"p")
        return {"status": "success", "output_file": str(target)}

    gen.generate_prologue_panel = gp
    prologue = {"panels": [{"image_prompt": "x", "title": "T"}]}
    (tmp_path / "prologue").mkdir()
    results = {}
    asyncio.run(_orch()._generate_prologue_panels(prologue, gen, tmp_path, results))
    assert results["prologue_panels"] == [{"panel_index": 0, "title": "T", "file": str(target), "provider": "unknown"}]
    assert prologue["panels"][0]["image_file"] == "prologue_panel_01.png"
    empty = {}
    asyncio.run(_orch()._generate_prologue_panels({"panels": []}, gen, tmp_path, empty))
    asyncio.run(_orch()._generate_prologue_panels(None, gen, tmp_path, empty))
    assert empty == {}


def test_prologue_panel_failure_status_and_exception_leave_no_record(tmp_path):
    gen = MagicMock()
    gen.generate_prologue_panel = AsyncMock(side_effect=[{"status": "error", "error": "no"}, RuntimeError("x")])
    prologue = {"panels": [{"image_prompt": "a"}, {"image_prompt": "b"}]}
    results = {}
    asyncio.run(_orch()._generate_prologue_panels(prologue, gen, tmp_path, results))
    assert results == {} and "image_file" not in prologue["panels"][0]


# ─── portrait attach to actors ────────────────────────────────────────────


def _client(**kw):
    c = MagicMock()
    c.update_entity = AsyncMock(return_value=kw.get("entity"))
    c.update_actor = AsyncMock(return_value=kw.get("actor"))
    return c


def _attach(client, npc, uuid_map):
    summary = {"portraits_attached": 0, "errors": []}
    asyncio.run(_orch()._attach_portrait_to_actor(client, npc, "p.png", uuid_map, summary))
    return summary


def test_attach_portrait_by_uuid_success_and_error_result():
    c = _client(entity={"type": "ok"})
    s = _attach(c, {"name": "Elara"}, {"Elara": "Actor.1"})
    assert s == {"portraits_attached": 1, "errors": []}
    assert c.update_entity.await_args.kwargs == {"uuid": "Actor.1", "data": {"img": "p.png", "prototypeToken": {"texture": {"src": "p.png"}}}}
    c = _client(entity={"type": "error", "error": "locked"})
    s = _attach(c, {"name": "Elara"}, {"Elara": "Actor.1"})
    assert s["portraits_attached"] == 0 and s["errors"] == ["Portrait update failed for 'Elara': locked"]


def test_attach_portrait_by_name_falls_back_and_missing_actor_is_not_an_error():
    c = _client(actor={"type": "ok"})
    s = _attach(c, {"name": "Elara"}, {})
    assert s["portraits_attached"] == 1
    assert c.update_actor.await_args.kwargs == {"actor_name": "Elara", "actor_data": {"img": "p.png", "prototypeToken": {"texture": {"src": "p.png"}}}}
    s = _attach(_client(actor=None), {"name": "Elara"}, {})
    assert s == {"portraits_attached": 0, "errors": []}


# ─── regenerate_assets_for_campaign branches ──────────────────────────────


class _Store:
    def __init__(self, tmp, data, deployment=None):
        self.exists = True
        self.safe_name = "camp"
        self.maps_dir = tmp / "maps"
        self.maps_dir.mkdir(exist_ok=True)
        (self.maps_dir / "portraits").mkdir(exist_ok=True)
        self._d, self._dep, self.saved = data, deployment or {}, None

    async def load(self, normalize=False):
        return self._d

    async def load_deployment(self):
        return self._dep

    async def save(self, d):
        self.saved = d


class _Gen:
    def __init__(self, **kw):
        self.closed = False

    async def health_check(self):
        return {"comfyui": True}

    async def close(self):
        self.closed = True


def _regen(tmp_path, monkeypatch, data, deployment, upload):
    store = _Store(tmp_path, data, deployment)
    monkeypatch.setattr("campaign.vault.CampaignStore", lambda n, v: store)
    monkeypatch.setattr("campaign.map_generator.MapGenerator", _Gen)
    monkeypatch.setattr(oa, "upload_image", upload)
    o = _orch()
    o.generate_assets = AsyncMock(return_value={})
    o._attach_map_to_scene = AsyncMock()
    o._attach_portrait_to_actor = AsyncMock()
    client = MagicMock()
    client.is_connected = True
    return o, store, client


def test_regenerate_skips_entries_without_files_and_records_upload_failures(tmp_path, monkeypatch):
    data = {"scenes": [{"name": "NoFile"}, {"name": "Missing", "map_file": "gone.png"},
                       {"name": "Bad", "map_file": "bad.png"}],
            "npcs": [{"name": "NoPortrait"}, {"name": "Ghost", "portrait_file": "ghost.png"},
                     {"name": "Fail", "portrait_file": "fail.png"}]}
    upload = AsyncMock(return_value={"ok": False, "error": "413 too large"})
    o, store, client = _regen(tmp_path, monkeypatch, data, {"scenes": []}, upload)
    (store.maps_dir / "bad.png").write_bytes(b"x")
    (store.maps_dir / "portraits" / "fail.png").write_bytes(b"x")
    summary = asyncio.run(o.regenerate_assets_for_campaign("Camp", foundry_client=client))
    assert upload.await_count == 2
    assert summary["errors"] == ["scene 'Bad': 413 too large", "NPC 'Fail': 413 too large"]
    assert "background_src" not in data["scenes"][2] and "portrait_src" not in data["npcs"][2]
    o._attach_map_to_scene.assert_not_awaited()
    o._attach_portrait_to_actor.assert_not_awaited()
    assert "scenes_enriched" not in summary          # deployment lists no scenes
    assert store.saved is data


def test_regenerate_enrich_failure_and_enrich_errors_are_recorded(tmp_path, monkeypatch):
    upload = AsyncMock(return_value={"ok": True, "src": "s.png"})
    o, store, client = _regen(tmp_path, monkeypatch, {"scenes": [], "npcs": []}, {"scenes": ["X"]}, upload)
    o.enrich_scenes = AsyncMock(return_value={"enriched": 1, "errors": ["wall fail"]})
    summary = asyncio.run(o.regenerate_assets_for_campaign("Camp", foundry_client=client))
    assert summary["scenes_enriched"] == 1 and summary["errors"] == ["wall fail"]
    o.enrich_scenes = AsyncMock(side_effect=RuntimeError("js down"))
    summary = asyncio.run(o.regenerate_assets_for_campaign("Camp", foundry_client=client))
    assert summary["errors"] == ["enrich: js down"]


def test_regenerate_without_attach_skips_all_foundry_work(tmp_path, monkeypatch):
    upload = AsyncMock()
    o, store, client = _regen(tmp_path, monkeypatch, {"scenes": [{"name": "A", "map_file": "a.png"}]}, {}, upload)
    summary = asyncio.run(o.regenerate_assets_for_campaign("Camp", foundry_client=client, attach_to_foundry=False))
    assert summary["errors"] == [] and upload.await_count == 0


# ─── placeholder portraits ────────────────────────────────────────────────


def _placeholder(tmp_path, monkeypatch, gen_result, upload_result=None, update_raises=None, comfy=True):
    client = MagicMock()
    client.is_connected = True
    client.execute_js = AsyncMock(return_value={"result": [{"name": "Ogre", "uuid": "Actor.o"}]})
    client.update_entity = AsyncMock(side_effect=update_raises)
    gen = MagicMock()
    gen.health_check = AsyncMock(return_value={"comfyui": comfy})
    gen.close = AsyncMock()
    gen.generate_portrait = AsyncMock(side_effect=gen_result) if isinstance(gen_result, Exception) \
        else AsyncMock(return_value=gen_result)
    monkeypatch.setattr("campaign.map_generator.MapGenerator", lambda **kw: gen)
    monkeypatch.setattr(oa, "upload_image", AsyncMock(return_value=upload_result or {"ok": True, "src": "u.png"}))
    summary = asyncio.run(_orch()._generate_placeholder_portraits(client, "Camp", output_dir=tmp_path))
    return summary, client, gen


def test_placeholder_generation_failure_status_falls_back_to_icon(tmp_path, monkeypatch):
    s, client, gen = _placeholder(tmp_path, monkeypatch, {"status": "error"})
    assert (s["generated"], s["fallback_icon"], s["errors"]) == (0, 1, [])
    assert client.update_entity.await_args.kwargs["data"]["img"] == _orch()._default_monster_icon("Ogre")
    gen.close.assert_awaited_once()


def test_placeholder_upload_failure_is_recorded_and_icon_used(tmp_path, monkeypatch):
    s, client, _ = _placeholder(tmp_path, monkeypatch, {"status": "success", "output_file": str(tmp_path / "o.png")},
                                upload_result={"ok": False, "error": "bad"})
    assert s["errors"] == ["Ogre: bad"] and s["fallback_icon"] == 1 and s["generated"] == 0


def test_placeholder_generator_exception_and_update_failure_recorded(tmp_path, monkeypatch):
    s, _, _ = _placeholder(tmp_path, monkeypatch, RuntimeError("gpu"), update_raises=RuntimeError("ws"))
    assert s["errors"] == ["Ogre: gpu", "Ogre update: ws"]
    assert s["fallback_icon"] == 1


def test_placeholder_comfy_down_skips_generation_entirely(tmp_path, monkeypatch):
    s, client, gen = _placeholder(tmp_path, monkeypatch, {"status": "success"}, comfy=False)
    gen.generate_portrait.assert_not_awaited()
    assert s["fallback_icon"] == 1


def test_scene_prompt_description_without_capitalised_words_adds_no_keywords():
    p = _orch()._build_scene_prompt({"type": "forest", "description": "quiet and damp", "lighting": "dusk"})
    assert p == "top-down forest clearing, dusk lighting, detailed visual, fantasy illustration style"


def test_regenerate_forwards_progress_messages_to_callback(tmp_path, monkeypatch):
    o, store, client = _regen(tmp_path, monkeypatch, {"scenes": [], "npcs": []}, {}, AsyncMock())
    seen = []
    asyncio.run(o.regenerate_assets_for_campaign(
        "Camp", foundry_client=client, progress=lambda msg, **kw: seen.append((msg, kw.get("step")))))
    assert seen[0] == ("🎨 Regenerating maps and portraits...", "assets")
    assert seen[-1][1] == "assets" and seen[-1][0].startswith("✅ Regenerated 0 map(s), 0 portrait(s)")


def test_scene_prompt_without_description_has_only_perspective_and_style():
    p = _orch()._build_scene_prompt({"type": "cave"})
    assert p == "top-down cavern, warm light lighting, detailed visual, fantasy illustration style"
