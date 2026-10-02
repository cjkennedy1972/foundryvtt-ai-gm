"""Maps that read as D&D (muted palette, a sharper 2x pass at a 128 px grid) and fantasy NPC portraits.

Each behavior is a reaction to something seen on real output: maps averaged 0.60 saturation with neon runes, and
after the 9/28 first-sentence prompt a bare role ("a lieutenant of the Ironclad Regiment") drew a modern officer.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from campaign.map_generator import MapGenerator
from config import settings


@pytest.fixture
def gen():
    return MapGenerator()


# ── palette ─────────────────────────────────────────────────────────────────

def test_loud_wording_is_toned_down_and_muted_cues_added(gen):
    out = gen._finish_prompt("top-down crypt, glowing ancient runes, eerie blue spectral glow, dramatic shadows, crimson carpet")
    for loud in ("glowing", "spectral", "dramatic", "crimson", "eerie"):
        assert loud not in out.lower()
    assert "faintly lit" in out and "dark red" in out and "muted natural earth tones" in out


def test_the_negative_prompt_no_longer_fights_a_muted_palette(gen):
    out = gen._finish_negative("blurry, washed out, flat lighting, uniformly gray, empty")
    assert "washed out" not in out and "flat lighting" not in out and "uniformly gray" not in out
    assert "blurry" in out and "empty" in out
    assert "oversaturated" in out and "neon" in out and out.count("oversaturated") == 1


def test_the_palette_can_be_turned_off(gen):
    with patch.object(settings, "map_muted_palette", False):
        assert gen._finish_prompt("glowing runes") == "glowing runes"
        assert gen._finish_negative("washed out") == "washed out"


def test_map_sampling_uses_the_configured_cfg(gen, tmp_path):
    seen = {}

    async def run():
        with patch.object(MapGenerator, "_checked_output_dir", lambda self, d: tmp_path), \
             patch.object(MapGenerator, "_submit_and_wait", new=AsyncMock(return_value={})) as submit:
            await gen.generate_map_comfyui("a tavern", tmp_path, seed=1)
            seen["wf"] = submit.await_args.args[0]

    asyncio.run(run())
    sampler = next(n for n in seen["wf"].values() if n["class_type"] == "KSampler")
    assert sampler["inputs"]["cfg"] == settings.map_cfg == 5.0


# ── 2x detail pass ──────────────────────────────────────────────────────────

def _wf(gen, **kw):
    return gen._build_sdxl_workflow("p", "n", 1024, 768, 28, 5.0, 7, filename_prefix="t", **kw)


def test_the_detail_pass_upscales_resamples_lightly_and_saves_the_result(gen):
    wf = _wf(gen)
    assert wf["30"]["inputs"]["width"] == 2048 and wf["30"]["inputs"]["height"] == 1536
    assert wf["31"]["class_type"] == "VAEEncodeTiled" and wf["33"]["class_type"] == "VAEDecodeTiled"   # MPS needs tiles
    assert wf["32"]["inputs"]["denoise"] == settings.map_hires_denoise
    assert wf["11"]["inputs"]["images"] == ["33", 0]                                                    # the saved image is the detailed one


def test_the_controlnet_variant_gets_the_same_pass(gen):
    wf = _wf(gen, use_controlnet=True, controlnet_model="cn.safetensors", layout_image_path="mask.png")
    assert wf["15"]["inputs"]["images"] == ["33", 0] and wf["30"]["inputs"]["image"] == ["14", 0]
    assert wf["32"]["inputs"]["positive"] == ["4", 0]       # plain prompt: the layout holds at this denoise


def test_scale_one_leaves_the_workflow_alone(gen):
    with patch.object(settings, "map_hires_scale", 1):
        wf = _wf(gen)
    assert "30" not in wf and wf["11"]["inputs"]["images"] == ["8", 0]


def test_prologue_panels_do_not_get_the_pass(gen):
    wf = gen._build_sdxl_workflow("p", "n", 1344, 768, 28, 7.5, 1, filename_prefix="t", hires=False)
    assert "30" not in wf


# ── grid follows the saved image ────────────────────────────────────────────

def test_a_built_campaign_scene_records_a_128_px_grid(tmp_path):
    from campaign.orchestrator import CampaignOrchestrator

    scene = {"name": "Hall", "scene_setup": {"grid_width": 16, "grid_height": 12}}
    gen = SimpleNamespace(hires_scale=2, generate_map=AsyncMock(return_value={"status": "success", "output_file": str(tmp_path / "m.png")}),
                          generate_map_controlnet=AsyncMock(return_value={"status": "success", "output_file": str(tmp_path / "m.png")}))
    orch = CampaignOrchestrator.__new__(CampaignOrchestrator)
    results = {"maps": [], "errors": []}
    asyncio.run(orch._generate_scene_maps([scene], gen, tmp_path, results))

    assert scene["_grid_size_px"] == 128 and scene["_map_width_px"] == 2048 and scene["_map_height_px"] == 1536
    assert gen.generate_map.await_args.kwargs["width"] == 1024         # generated at the layout size, then detailed


def test_the_runtime_map_action_scales_scene_and_grid():
    from actions import executors

    created = {}

    async def create_entity(kind, data):
        created.update(data)
        return {"id": "s1"}

    foundry = SimpleNamespace(upload_file=AsyncMock(return_value={"path": "maps/x.png"}), create_entity=create_entity,
                              set_active_scene=AsyncMock(), chat_message=AsyncMock())
    gen = SimpleNamespace(hires_scale=2, generate_map=AsyncMock(return_value={"status": "success", "output_file": __file__}))
    app = SimpleNamespace(map_generator=gen, map_output_dir="/tmp")
    asyncio.run(executors.execute_generate_map("a crypt", "Crypt", size="small", app_state=app, foundry=foundry))

    assert (created.get("width"), created.get("height")) == (2048, 1536) and created["grid"]["size"] == 128


# ── portraits ───────────────────────────────────────────────────────────────

def test_stated_facts_win_and_the_rest_is_stable_per_name(gen):
    a = gen._portrait_attributes("Aldric", "A stern old dwarf commander. She-wolf banners fly over his hall.", {"gender": "male"})
    assert a["ancestry"] == "dwarf" and a["gender"] == "man" and a["age"] == "elderly"
    again = gen._portrait_attributes("Aldric", "A stern old dwarf commander.", {"gender": "male"})
    assert {k: a[k] for k in ("skin", "hair", "mood")} == {k: again[k] for k in ("skin", "hair", "mood")}
    other = gen._portrait_attributes("Zephyra", "A stern old dwarf commander.", {"gender": "male"})
    assert (a["skin"], a["hair"], a["mood"]) != (other["skin"], other["hair"], other["mood"]) or a != other


def test_different_npcs_get_different_looks(gen):
    looks = {tuple(gen._portrait_attributes(n, "A merchant.")[k] for k in ("gender", "skin", "hair", "mood")) for n in
             ("Brek", "Ysolde", "Marcus", "Tilda", "Orin", "Keth", "Palla", "Dov")}
    assert len(looks) >= 5


def test_a_bare_role_still_reads_as_fantasy(gen):
    text = gen._portrait_prompt("A human lieutenant of the Ironclad Regiment.", gen._portrait_attributes("Vendri", "A human lieutenant."), zimage=True)
    assert "medieval fantasy attire" in text and "lieutenant" in text and "bust portrait" in text
    assert "uniform" not in text
    assert "no border" in text and "no white margin" in text       # z-image ignores negatives: say it in the positive


def _portrait_graph(gen, tmp_path, *, model, available):
    async def run():
        with patch.object(MapGenerator, "_checked_output_dir", lambda self, d: tmp_path), \
             patch.object(settings, "portrait_model", model), \
             patch.object(MapGenerator, "_zimage_available", new=AsyncMock(return_value=available)), \
             patch.object(MapGenerator, "_submit_and_wait", new=AsyncMock(return_value={})) as submit:
            out = await gen.generate_portrait_comfyui("A weathered bard.", tmp_path, seed=3, name="Fenn")
            return submit.await_args.args[0], out

    return asyncio.run(run())


def test_auto_uses_zimage_when_comfyui_has_it(gen, tmp_path):
    wf, out = _portrait_graph(gen, tmp_path, model="auto", available=True)
    assert out["model"] == "zimage" and wf["1"]["inputs"]["unet_name"] == "z_image_turbo_bf16.safetensors"
    assert (wf["7"]["inputs"]["width"], wf["7"]["inputs"]["height"]) == (768, 960)


def test_auto_falls_back_to_sd15_without_it(gen, tmp_path):
    wf, out = _portrait_graph(gen, tmp_path, model="auto", available=False)
    assert out["model"] == "sd15" and wf["3"]["inputs"]["ckpt_name"].startswith("v1-5")


def test_a_forced_model_is_honoured(gen, tmp_path):
    assert _portrait_graph(gen, tmp_path, model="sd15", available=True)[1]["model"] == "sd15"
    assert _portrait_graph(gen, tmp_path, model="zimage", available=False)[1]["model"] == "zimage"


def test_monster_portraits_do_not_get_human_attributes(gen, tmp_path):
    async def run():
        with patch.object(MapGenerator, "_checked_output_dir", lambda self, d: tmp_path), \
             patch.object(settings, "portrait_model", "sd15"), \
             patch.object(MapGenerator, "_submit_and_wait", new=AsyncMock(return_value={})) as submit:
            await gen.generate_portrait_comfyui("fantasy TTRPG monster portrait of a Goblin", tmp_path, name="Goblin", npc={"monster": True})
            return submit.await_args.args[0]

    text = asyncio.run(run())["4"]["inputs"]["text"]
    assert "Goblin" in text and "skinned" not in text and "expression" not in text


def test_a_white_matte_and_shadow_are_trimmed_off_a_portrait(tmp_path):
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (400, 500), (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.rectangle([60, 70, 340, 440], fill=(40, 30, 25))                    # the painting
    d.rectangle([340, 80, 360, 450], fill=(215, 215, 215))                 # soft drop shadow to its right
    path = tmp_path / "p.png"
    img.save(path)

    assert MapGenerator._trim_margins(path) is True

    cut = Image.open(path)
    assert cut.size == (280, 371) and cut.getpixel((5, 5)) == (40, 30, 25)


def test_a_full_bleed_portrait_is_left_alone(tmp_path):
    from PIL import Image
    path = tmp_path / "p.png"
    Image.new("RGB", (400, 500), (40, 30, 25)).save(path)

    assert MapGenerator._trim_margins(path) is False and Image.open(path).size == (400, 500)
