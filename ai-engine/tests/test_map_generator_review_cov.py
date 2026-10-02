"""MapGenerator: prompt finishing, hires pass, ComfyUI polling/download, layout masks, portraits, wrappers."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

import campaign.map_generator as mgmod
from campaign.map_generator import MapGenerator
from config import settings

PIL = pytest.importorskip("PIL.Image")


def run(c):
    return asyncio.run(c)


def resp(status=200, json=None, content=b"", text=""):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = json
    r.content = content
    r.text = text
    return r


@pytest.fixture
def mg(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(MapGenerator, "_zimage_ok", None)
    g = MapGenerator(comfyui_url="http://comfy:1/", comfyui_input_dirs=[])
    g._client = MagicMock()
    g._client.get = AsyncMock()
    g._client.post = AsyncMock()
    return g


OUT = Path("campaign_assets/t_maps")


# ── prompt finishing ─────────────────────────────────────────────────────

def test_finish_prompt_tones_down_loud_words(mg, monkeypatch):
    monkeypatch.setattr(settings, "map_muted_palette", True)
    out = mg._finish_prompt("A glowing crimson hall, vibrant , dramatic eerie blue light")
    assert "glowing" not in out and "crimson" not in out and "vibrant" not in out
    assert "faintly lit dark red hall" in out and "soft" in out
    assert out.endswith(mg._MUTED_CUES)
    assert "  " not in out and " ," not in out


def test_finish_prompt_disabled_is_identity(mg, monkeypatch):
    monkeypatch.setattr(settings, "map_muted_palette", False)
    assert mg._finish_prompt("glowing") == "glowing"
    assert mg._finish_negative("washed out, x") == "washed out, x"


def test_finish_negative_drops_conflicts_and_dedupes(mg, monkeypatch):
    monkeypatch.setattr(settings, "map_muted_palette", True)
    out = mg._finish_negative("blurry, Washed Out, flat lighting, neon").split(", ")
    assert out[:2] == ["blurry", "neon"]
    assert "washed out" not in [o.lower() for o in out] and "flat lighting" not in out
    assert out.count("neon") == 1 and "oversaturated" in out


def test_resolve_cfg_and_hires_scale(monkeypatch):
    monkeypatch.setattr(settings, "map_cfg", 4.5)
    assert MapGenerator._resolve_cfg(None) == 4.5 and MapGenerator._resolve_cfg(7) == 7.0
    monkeypatch.setattr(settings, "map_hires_scale", 0)
    assert MapGenerator().hires_scale == 1
    monkeypatch.setattr(settings, "map_hires_scale", 2)
    assert MapGenerator().hires_scale == 2


def test_to_pixel_coords_is_fixed_grid():
    assert MapGenerator._to_pixel_coords([1, 2.5, 0, 3], 64) == [64, 160, 0, 192]


# ── workflows / hires ────────────────────────────────────────────────────

def test_sdxl_workflow_text_only_with_and_without_hires(mg, monkeypatch):
    monkeypatch.setattr(settings, "map_hires_scale", 2)
    monkeypatch.setattr(settings, "map_hires_denoise", 0.4)
    wf = mg._build_sdxl_workflow("p", "n", 1024, 768, 28, 6.0, 9, filename_prefix="pfx")
    assert wf["6"]["inputs"]["sampler_name"] == "dpmpp_3m_sde" and wf["6"]["inputs"]["seed"] == 9
    assert wf["4"]["inputs"]["text"] == "p" and wf["5"]["inputs"]["text"] == "n"
    assert wf["30"]["inputs"]["width"] == 2048 and wf["30"]["inputs"]["image"] == ["8", 0]
    assert wf["32"]["inputs"]["denoise"] == 0.4 and wf["32"]["inputs"]["latent_image"] == ["31", 0]
    assert wf["11"]["inputs"]["images"] == ["33", 0]          # SaveImage rerouted through the detail pass
    assert wf["3"]["inputs"]["ckpt_name"] == mg.checkpoint_name
    plain = mg._build_sdxl_workflow("p", "n", 1024, 768, 10, 6.0, 9, hires=False)
    assert plain["6"]["inputs"]["sampler_name"] == "dpmpp_2m_sde" and "30" not in plain
    assert plain["11"]["inputs"]["images"] == ["8", 0]


def test_sdxl_workflow_controlnet_wiring(mg, monkeypatch):
    monkeypatch.setattr(settings, "map_hires_scale", 2)
    wf = mg._build_sdxl_workflow("p", "n", 512, 512, 28, 6.0, 1, use_controlnet=True,
                                 layout_image_path="/x/y/mask.png", controlnet_strength=0.7)
    assert wf["12"]["inputs"]["image"] == "mask.png"                  # basename only
    assert wf["7"]["inputs"]["strength"] == 0.7 and wf["6"]["inputs"]["control_net_name"] == mg.controlnet_model
    assert wf["10"]["inputs"]["positive"] == ["7", 0]
    assert wf["30"]["inputs"]["image"] == ["14", 0] and wf["15"]["inputs"]["images"] == ["33", 0]
    assert wf["11" if "11" in wf else "15"]


def test_append_hires_noop_at_scale_one(mg, monkeypatch):
    monkeypatch.setattr(settings, "map_hires_scale", 1)
    wf = {"x": 1}
    assert mg._append_hires(wf, 1, 1, 1, 1.0) is wf and wf == {"x": 1}


# ── input dir detection ──────────────────────────────────────────────────

def test_input_dir_prefers_configured_then_cached(mg, tmp_path):
    mg.comfyui_input_dirs = [tmp_path / "in"]
    assert run(mg._ensure_comfyui_input_dir()) == tmp_path / "in"
    mg.comfyui_input_dirs = []
    mg._detected_comfyui_input_dir = tmp_path / "cached"
    assert run(mg._ensure_comfyui_input_dir()) == tmp_path / "cached"
    mg._client.get.assert_not_called()


def test_input_dir_autodetected_from_argv(mg, tmp_path):
    (tmp_path / "base" / "input").mkdir(parents=True)
    mg._client.get.return_value = resp(json={"system": {"argv": ["main.py", "--base-directory", str(tmp_path / "base")]}})
    assert run(mg._ensure_comfyui_input_dir()) == tmp_path / "base" / "input"
    assert mg._detected_comfyui_input_dir == tmp_path / "base" / "input"


@pytest.mark.parametrize("argv", [["--base-directory"], ["--base_path", "/does/not/exist"], []])
def test_input_dir_undetectable(mg, argv):
    mg._client.get.return_value = resp(json={"system": {"argv": argv}})
    assert run(mg._ensure_comfyui_input_dir()) is None


def test_input_dir_http_failures(mg):
    mg._client.get.return_value = resp(status=500)
    assert run(mg._ensure_comfyui_input_dir()) is None
    mg._client.get.side_effect = RuntimeError("down")
    assert run(mg._ensure_comfyui_input_dir()) is None


# ── layout masks ─────────────────────────────────────────────────────────

def _px(path, xy):
    with PIL.open(path) as im:
        return im.getpixel(xy)


def test_layout_mask_walls_white_doors_black(mg, tmp_path):
    setup = {"walls": [[0, 1, 4, 1], [1, 2]], "doors": [{"c": [2, 1, 3, 1]}, {"c": [1]}], "_output_dir": str(tmp_path)}
    p = run(mg.generate_layout_mask(setup, 512, 256, 64))
    assert p.parent == tmp_path / "layouts"
    assert _px(p, (32, 64)) == 255            # wall pixel at grid (0.5, 1)
    assert _px(p, (160, 64)) == 0             # door gap at grid (2.5, 1)
    assert _px(p, (400, 200)) == 0
    with PIL.open(p) as im:
        assert im.size == (512, 256)


def test_layout_mask_none_without_geometry(mg):
    assert run(mg.generate_layout_mask({}, 64, 64)) is None
    assert run(mg.generate_layout_mask({"walls": [], "doors": []})) is None


def test_layout_mask_without_pil(mg, monkeypatch):
    monkeypatch.setattr(mgmod, "PIL_AVAILABLE", False)
    assert run(mg.generate_layout_mask({"walls": [[0, 0, 1, 1]]})) is None
    assert run(mg.generate_procedural_layout_mask({})) is None


def test_layout_mask_default_dir_is_campaign_assets(mg, tmp_path):
    p = run(mg.generate_layout_mask({"walls": [[0, 0, 2, 0]]}, 128, 128))
    assert Path("campaign_assets/layouts") == p.parent


def test_procedural_mask_is_seeded_and_saved(mg, tmp_path):
    setup = {"grid_width": 20, "grid_height": 15, "_output_dir": str(tmp_path)}
    p = run(mg.generate_procedural_layout_mask(setup, 20 * 64, 15 * 64, 64, seed=3))
    assert p.name.startswith("layout_mask_procedural_") and p.parent == tmp_path / "layouts"
    with PIL.open(p) as im:
        assert im.size == (1280, 960)
        assert 255 in set(im.getdata())                        # real walls were drawn
    # bad generated layout -> None, no file
    mgmod_validate = mgmod.validate_scene_setup
    mgmod.validate_scene_setup = lambda s: (False, ["x"])
    try:
        assert run(mg.generate_procedural_layout_mask({"grid_width": 20, "grid_height": 15, "_output_dir": str(tmp_path / "o2")})) is None
    finally:
        mgmod.validate_scene_setup = mgmod_validate
    assert not (tmp_path / "o2").exists()


def test_fallback_layout_replaces_scene_geometry(mg, tmp_path):
    scene = {"name": "Crypt", "type": "dungeon",
             "scene_setup": {"grid_width": 20, "grid_height": 15, "walls": [[0, 0, 1, 0]], "doors": [], "_output_dir": str(tmp_path)}}
    p = run(mg.fallback_layout_for_scene(scene, 1280, 960, 64))
    assert p is not None and p.exists()
    assert len(scene["scene_setup"]["walls"]) > 1                # swapped for the procedural geometry
    assert scene["scene_setup"]["grid_width"] == 20


def test_fallback_layout_validation_failure_returns_none(mg, monkeypatch):
    monkeypatch.setattr(mgmod, "validate_scene_setup", lambda s: (False, ["bad"]))
    scene = {"type": "dungeon", "scene_setup": {"walls": [[9, 9, 9, 9]]}}
    assert run(mg.fallback_layout_for_scene(scene)) is None
    assert scene["scene_setup"]["walls"] == [[9, 9, 9, 9]]       # untouched


# ── health / models ──────────────────────────────────────────────────────

def test_health_and_models(mg):
    mg._client.get.return_value = resp(200)
    assert run(mg.health_check()) == {"comfyui": True}
    mg._client.get.return_value = resp(500)
    assert run(mg.health_check()) == {"comfyui": False}
    mg._client.get.side_effect = RuntimeError("x")
    assert run(mg.health_check()) == {"comfyui": False}
    mg._client.get.side_effect = None
    mg._client.get.return_value = resp(json={"CheckpointLoaderSimple": {"input": {"required": {"ckpt_name": [["a.safetensors", 3]]}}}})
    assert run(mg.get_models()) == ["a.safetensors", "3"]
    mg._client.get.return_value = resp(json={})
    assert run(mg.get_models()) == []
    mg._client.get.return_value = resp(json={"CheckpointLoaderSimple": {"input": {"required": {"ckpt_name": ["notalist"]}}}})
    assert run(mg.get_models()) == []


# ── submit / wait / download ─────────────────────────────────────────────

def test_submit_error_paths(mg):
    mg._client.post.return_value = resp(status=400, text="bad graph")
    assert run(mg._submit_and_wait({}, OUT, "x")) == {"status": "error", "prompt_id": None, "error": "bad graph", "provider": "comfyui"}
    mg._client.post.return_value = resp(json={})
    out = run(mg._submit_and_wait({}, OUT, "x"))
    assert out["status"] == "error" and "prompt_id" in out["error"]


def test_submit_success_posts_workflow_and_client_id(mg):
    mg._client.post.return_value = resp(json={"prompt_id": "p1"})
    mg._wait_for_completion = AsyncMock(return_value=Path("a.png"))
    out = run(mg._submit_and_wait({"n": 1}, OUT, "x", accept=(".mp4",)))
    assert out == {"status": "success", "prompt_id": "p1", "output_file": "a.png", "provider": "comfyui"}
    assert mg._client.post.await_args.args[0] == "http://comfy:1/prompt"
    assert mg._client.post.await_args.kwargs["json"] == {"prompt": {"n": 1}, "client_id": mg._client_id}
    mg._wait_for_completion.assert_awaited_once_with("p1", OUT, (".mp4",))
    mg._wait_for_completion = AsyncMock(return_value=None)
    assert run(mg._submit_and_wait({}, OUT, "x"))["status"] == "error"


@pytest.fixture
def fastclock(monkeypatch):
    t = {"now": 0.0}
    monkeypatch.setattr(mgmod.time, "time", lambda: t["now"])

    async def sleep(s):
        t["now"] += s
    monkeypatch.setattr(mgmod.asyncio, "sleep", sleep)
    return t


def test_wait_returns_first_accepted_output(mg, fastclock):
    hist = {"p": {"status": {"status_str": "success"},
                  "outputs": {"9": {"images": [{"filename": "notes.txt"}, {"filename": "a.png"}]}}}}
    mg._client.get.side_effect = [resp(json={}), resp(json=hist)]
    mg._download_image = AsyncMock(return_value=Path("a.png"))
    assert run(mg._wait_for_completion("p", OUT)) == Path("a.png")
    mg._download_image.assert_awaited_once_with("a.png", OUT)
    assert mg._client.get.await_args.args[0] == "http://comfy:1/history/p"


def test_wait_error_status_stops_immediately(mg, fastclock):
    mg._client.get.return_value = resp(json={"p": {"status": {"status_str": "error"}}})
    assert run(mg._wait_for_completion("p", OUT)) is None
    assert mg._client.get.await_count == 1


def test_wait_times_out_and_survives_poll_errors(mg, fastclock):
    mg.timeout = 6
    mg._client.get.side_effect = RuntimeError("net")
    assert run(mg._wait_for_completion("p", OUT)) is None
    assert fastclock["now"] >= 6


def test_wait_success_with_no_usable_output_does_not_spin_until_timeout(mg, fastclock):
    """A finished prompt whose outputs hold nothing we accept will never produce one."""
    mg._client.get.return_value = resp(json={"p": {"status": {"status_str": "success"}, "outputs": {"9": {"images": [{"filename": "a.gif"}]}}}})
    assert run(mg._wait_for_completion("p", OUT)) is None
    assert fastclock["now"] < mg.timeout


def test_download_writes_inside_output_dir(mg, tmp_path):
    out = tmp_path / "o"
    out.mkdir()
    mg._client.get.return_value = resp(content=b"PNG")
    p = run(mg._download_image("a.png", out))
    assert p == out / "a.png" and p.read_bytes() == b"PNG"
    assert mg._client.get.await_args.kwargs["params"] == {"filename": "a.png", "type": "output", "subfolder": ""}


@pytest.mark.parametrize("bad", ["../evil.png", "sub/a.png", "", "/etc/passwd"])
def test_download_rejects_unsafe_names(mg, tmp_path, bad):
    assert run(mg._download_image(bad, tmp_path)) is None
    mg._client.get.assert_not_called()


def test_download_http_error_and_exception(mg, tmp_path):
    mg._client.get.return_value = resp(status=404)
    assert run(mg._download_image("a.png", tmp_path)) is None
    assert not (tmp_path / "a.png").exists()
    mg._client.get.side_effect = RuntimeError("x")
    assert run(mg._download_image("a.png", tmp_path)) is None


# ── generate_map_comfyui / controlnet / map / batch ──────────────────────

def test_generate_map_comfyui_builds_styled_workflow(mg, monkeypatch):
    monkeypatch.setattr(settings, "map_muted_palette", False)
    mg._submit_and_wait = AsyncMock(return_value={"status": "success"})
    run(mg.generate_map_comfyui("a castle", OUT, negative_prompt="neg", seed=11, style="battlemap", cfg=5))
    wf, outdir, hint = mg._submit_and_wait.await_args.args
    assert wf["4"]["inputs"]["text"] == mg._STYLE_PREFIXES["battlemap"] + "a castle"
    assert wf["5"]["inputs"]["text"] == "neg" and wf["6"]["inputs"]["seed"] == 11 and wf["6"]["inputs"]["cfg"] == 5.0
    assert outdir.name == "t_maps" and outdir.is_dir() and hint == "map"
    run(mg.generate_map_comfyui("x", OUT, style="unknown-style"))
    assert mg._submit_and_wait.await_args.args[0]["4"]["inputs"]["text"].startswith(mg._STYLE_PREFIXES["fantasy_map"])


def test_generate_map_comfyui_refuses_escape(mg):
    with pytest.raises(ValueError):
        run(mg.generate_map_comfyui("x", Path("/etc/evil")))


def test_generate_map_unreachable_comfy_short_circuits(mg):
    mg._client.get.return_value = resp(500)
    out = run(mg.generate_map("x", OUT))
    assert out == {"status": "error", "error": "ComfyUI backend is not available", "provider": "none"}
    assert run(mg.generate_portrait("x", OUT)) == out
    assert run(mg.generate_prologue_panel("x", "tome", OUT)) == out
    assert run(mg.generate_map_controlnet("x", "m.png", OUT)) == out


def test_generate_map_parses_size_and_style_negative(mg):
    mg._client.get.return_value = resp(200)
    mg.generate_map_comfyui = AsyncMock(return_value={"ok": 1})
    run(mg.generate_map("p", OUT, size="640X480", style="battlemap"))
    kw = mg.generate_map_comfyui.await_args.kwargs
    assert (kw["width"], kw["height"]) == (640, 480)
    assert kw["negative_prompt"] == mg._BATTLEMAP_NEGATIVE and kw["style"] == "battlemap"
    run(mg.generate_map("p", OUT, size="garbage", width=100, height=50, negative_prompt="mine"))
    kw = mg.generate_map_comfyui.await_args.kwargs
    assert (kw["width"], kw["height"], kw["negative_prompt"], kw["style"]) == (100, 50, "mine", "fantasy_map")
    run(mg.generate_map("p", OUT))
    assert "photorealistic" in mg.generate_map_comfyui.await_args.kwargs["negative_prompt"]


def test_prologue_panel_uses_vessel_style_without_hires(mg):
    mg._client.get.return_value = resp(200)
    mg._submit_and_wait = AsyncMock(return_value={"status": "success"})
    run(mg.generate_prologue_panel("a battle", "stained_glass", OUT, seed=2))
    wf = mg._submit_and_wait.await_args.args[0]
    assert wf["4"]["inputs"]["text"] == mg._VESSEL_PREFIXES["stained_glass"] + "a battle"
    assert (wf["7"]["inputs"]["width"], wf["7"]["inputs"]["height"]) == (1344, 768) and "30" not in wf
    assert wf["11"]["inputs"]["filename_prefix"].startswith("prologue_stained_glass_")
    run(mg.generate_prologue_panel("a", "nonsense", OUT))
    assert mg._submit_and_wait.await_args.args[0]["4"]["inputs"]["text"].startswith(mg._VESSEL_PREFIXES["tome"])


def test_batch_runs_sequentially_and_tags_prompts(mg):
    mg.generate_map = AsyncMock(side_effect=[{"status": "success"}, {"status": "error"}])
    out = run(mg.generate_batch(["a", "b"], OUT))
    assert [(r["status"], r["prompt"]) for r in out] == [("success", "a"), ("error", "b")]


def test_controlnet_copies_mask_and_injects_filename(mg, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "map_muted_palette", False)
    mask = tmp_path / "mask.png"
    mask.write_bytes(b"M")
    comfy_in = tmp_path / "comfy_in"
    mg.comfyui_input_dirs = [comfy_in]
    mg._client.get.return_value = resp(200)
    mg._submit_and_wait = AsyncMock(return_value={"status": "success"})
    run(mg.generate_map_controlnet("hall", mask, OUT, seed=4, controlnet_strength=0.5))
    assert (OUT / "layouts" / "mask.png").read_bytes() == b"M"
    assert (comfy_in / "mask.png").read_bytes() == b"M"
    wf, _, hint = mg._submit_and_wait.await_args.args
    assert hint == "map_controlnet" and wf["12"]["inputs"]["image"] == "mask.png" and wf["7"]["inputs"]["strength"] == 0.5
    assert wf["4"]["inputs"]["text"].startswith(mg._STYLE_PREFIXES["dungeon"])


def test_controlnet_without_input_dir_still_submits(mg, tmp_path):
    mask = tmp_path / "mask.png"
    mask.write_bytes(b"M")
    mg._client.get.side_effect = [resp(200), RuntimeError("no stats")]     # health, then input-dir probe
    mg._submit_and_wait = AsyncMock(return_value={"status": "success"})
    assert run(mg.generate_map_controlnet("hall", mask, OUT))["status"] == "success"
    assert (OUT / "layouts" / "mask.png").exists()


def test_controlnet_copy_failure_is_survivable(mg, tmp_path, monkeypatch):
    mask = tmp_path / "mask.png"
    mask.write_bytes(b"M")
    mg.comfyui_input_dirs = [tmp_path / "ci"]
    mg._client.get.return_value = resp(200)
    mg._submit_and_wait = AsyncMock(return_value={"status": "success"})
    import shutil
    real = shutil.copy2
    calls = []

    def flaky(src, dst):
        calls.append(dst)
        if "ci" in str(dst):
            raise OSError("readonly")
        return real(src, dst)
    monkeypatch.setattr(shutil, "copy2", flaky)
    assert run(mg.generate_map_controlnet("hall", mask, OUT))["status"] == "success"
    assert len(calls) == 2


# ── portraits ────────────────────────────────────────────────────────────

def test_portrait_attributes_stated_facts_win():
    a = MapGenerator._portrait_attributes("Brek", "An old dwarf lord of the hold", {"gender": "female"})
    assert (a["ancestry"], a["gender"], a["age"]) == ("dwarf", "woman", "elderly")
    a = MapGenerator._portrait_attributes("X", "She is a young elf apprentice", {})
    assert (a["ancestry"], a["gender"], a["age"]) == ("elf", "woman", "young")
    for phrase, anc in (("the dwarves of the hold", "dwarf"), ("a dwarven smith", "dwarf"), ("an elven archer", "elf"), ("the elves", "elf")):
        assert MapGenerator._portrait_attributes("X", phrase)["ancestry"] == anc
    a = MapGenerator._portrait_attributes("X", "", {"race": "Tieflings", "gender": "m"})
    assert a["ancestry"] == "tiefling" and a["gender"] == "man"
    a = MapGenerator._portrait_attributes("X", "he and she argue", {})
    assert a["gender"] in ("man", "woman") and a["ancestry"] == "human"


def test_portrait_attributes_deterministic_by_name():
    a1 = MapGenerator._portrait_attributes("Mira", "")
    assert a1 == MapGenerator._portrait_attributes("mira", "")
    assert set(a1) == {"ancestry", "gender", "age", "skin", "hair", "mood"}
    names = {tuple(sorted(MapGenerator._portrait_attributes(n, "").items())) for n in ("Mira", "Zed the Odd", "Brannock", "Ilsa", "Tor", "Quill")}
    assert len(names) > 1  # the name actually seeds the attributes


def test_portrait_subject_first_sentence_30_words():
    assert MapGenerator._portrait_subject("One two. Three four.") == "One two."
    assert len(MapGenerator._portrait_subject(" ".join(["w"] * 50)).split()) == 30
    assert MapGenerator._portrait_subject(None) == ""


def test_portrait_prompt_variants(mg):
    attrs = MapGenerator._portrait_attributes("Mira", "")
    z = mg._portrait_prompt("A smith.", attrs, zimage=True)
    s = mg._portrait_prompt("A smith.", attrs, zimage=False)
    assert "A smith." in z and "full-bleed" in z and "Dungeons and Dragons" in s and attrs["hair"] in s


def test_zimage_available_checks_once_and_caches(mg):
    def get(url, timeout=None):
        node = url.rsplit("/", 1)[1]
        field = {"UNETLoader": "unet_name", "CLIPLoader": "clip_name", "VAELoader": "vae_name"}[node]
        names = {"unet_name": [mg.ZIMAGE["unet"]], "clip_name": [mg.ZIMAGE["clip"]], "vae_name": [mg.ZIMAGE["vae"]]}[field]
        return resp(json={node: {"input": {"required": {field: [names]}}}})
    mg._client.get = AsyncMock(side_effect=get)
    assert run(mg._zimage_available()) is True
    n = mg._client.get.await_count
    assert run(mg._zimage_available()) is True and mg._client.get.await_count == n


def test_zimage_available_failure_not_cached(mg):
    mg._client.get = AsyncMock(side_effect=RuntimeError("down"))
    assert run(mg._zimage_available()) is False
    assert MapGenerator._zimage_ok is None


def test_zimage_available_false_when_model_missing(mg):
    mg._client.get = AsyncMock(return_value=resp(json={"UNETLoader": {"input": {"required": {"unet_name": [["other"]]}}}}))
    assert run(mg._zimage_available()) is False and MapGenerator._zimage_ok is False


def test_portrait_zimage_workflow_monster_and_trim(mg, monkeypatch):
    monkeypatch.setattr(settings, "portrait_model", "zimage")
    mg._submit_and_wait = AsyncMock(return_value={"status": "success", "output_file": "campaign_assets/t_maps/p.png"})
    mg._trim_margins = MagicMock(return_value=True)
    res = run(mg.generate_portrait_comfyui("a red dragon", OUT, seed=3, name="Smaug", npc={"monster": True}))
    wf = mg._submit_and_wait.await_args.args[0]
    assert res["model"] == "zimage" and wf["1"]["inputs"]["unet_name"] == mg.ZIMAGE["unet"]
    assert wf["5"]["inputs"]["text"].startswith("a red dragon, bust portrait, fantasy creature art")
    assert (wf["7"]["inputs"]["width"], wf["7"]["inputs"]["height"]) == (768, 960)
    mg._trim_margins.assert_called_once_with(Path("campaign_assets/t_maps/p.png"))


def test_portrait_trim_failure_keeps_the_portrait(mg, monkeypatch):
    monkeypatch.setattr(settings, "portrait_model", "zimage")
    mg._submit_and_wait = AsyncMock(return_value={"status": "success", "output_file": "p.png"})
    mg._trim_margins = MagicMock(side_effect=OSError("corrupt"))
    res = run(mg.generate_portrait_comfyui("x", OUT))
    assert res["status"] == "success" and res["output_file"] == "p.png"


def test_portrait_auto_falls_back_to_sd15(mg, monkeypatch):
    monkeypatch.setattr(settings, "portrait_model", "auto")
    mg._zimage_available = AsyncMock(return_value=False)
    mg._submit_and_wait = AsyncMock(return_value={})
    res = run(mg.generate_portrait_comfyui("x", OUT))
    wf = mg._submit_and_wait.await_args.args[0]
    assert res["model"] == "sd15" and wf["5"]["inputs"]["text"] == mg._PORTRAIT_NEGATIVE


def _png(path, size, fill, inner=None):
    im = PIL.new("RGB", size, fill)
    if inner:
        im.paste((20, 20, 20), inner)
    im.save(path)


def test_trim_margins_crops_light_matte(tmp_path):
    p = tmp_path / "a.png"
    _png(p, (200, 200), (250, 250, 250), inner=(20, 20, 180, 180))
    assert MapGenerator._trim_margins(p) is True
    with PIL.open(p) as im:
        assert im.size == (160, 160)


def test_trim_margins_leaves_full_bleed_and_blank(tmp_path):
    full = tmp_path / "f.png"
    _png(full, (100, 100), (30, 30, 30))
    assert MapGenerator._trim_margins(full) is False
    blank = tmp_path / "b.png"
    _png(blank, (100, 100), (255, 255, 255))
    assert MapGenerator._trim_margins(blank) is False
    small = tmp_path / "s.png"                      # keeps < half the frame: not trusted
    _png(small, (200, 200), (250, 250, 250), inner=(80, 80, 120, 120))
    assert MapGenerator._trim_margins(small) is False
    with PIL.open(small) as im:
        assert im.size == (200, 200)
