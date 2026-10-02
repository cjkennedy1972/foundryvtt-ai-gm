"""CinematicArtist: capability probe, still/clip staging, WebM re-encode."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from campaign import cinematic_art as ca
from campaign.cinematic_art import CinematicArtist, to_webm
from config import settings


def run(c):
    return asyncio.run(c)


def fake_gen(object_info):
    """object_info: {(node, field): [choices]} -> a generator whose HTTP client serves it."""
    async def get(url, timeout=None):
        node = url.rsplit("/", 1)[1]
        field = [f for (n, f) in object_info if n == node]
        if not field:
            raise RuntimeError("404")
        r = MagicMock()
        r.json.return_value = {node: {"input": {"required": {f: [object_info[(node, f)]] for f in field}}}}
        return r
    g = MagicMock()
    g._client.get = get
    g.comfyui_base_url = "http://comfy"
    g._checked_output_dir = lambda p: Path(p)
    return g


def full_info():
    return {("UNETLoader", "unet_name"): [ca.Z["unet"]],
            ("CLIPLoader", "clip_name"): [ca.Z["clip"], settings.ltx_text_encoder],
            ("VAELoader", "vae_name"): [ca.Z["vae"]],
            ("CheckpointLoaderSimple", "ckpt_name"): [settings.ltx_checkpoint],
            ("LTXVImgToVideo", "width"): []}


def test_available_all_present():
    assert run(CinematicArtist(fake_gen(full_info())).available()) == {"still": True, "clip": True}


def test_available_still_only_when_ltx_missing():
    info = full_info()
    info[("CheckpointLoaderSimple", "ckpt_name")] = ["other.safetensors"]
    assert run(CinematicArtist(fake_gen(info)).available()) == {"still": True, "clip": False}


def test_available_no_still_model():
    info = full_info()
    info[("UNETLoader", "unet_name")] = []
    assert run(CinematicArtist(fake_gen(info)).available()) == {"still": False, "clip": True}


def test_available_old_comfyui_without_ltx_node_or_unreachable():
    info = full_info()
    del info[("LTXVImgToVideo", "width")]
    assert run(CinematicArtist(fake_gen(info)).available()) == {"still": False, "clip": False}


def test_still_prompt_embeds_description():
    p = ca.still_prompt("a tavern")
    assert "a tavern" in p and "no text" in p


def test_graphs_wire_prompt_seed_and_prefix():
    art = CinematicArtist(MagicMock())
    g = art._still_graph("a keep", 42, "pfx")
    assert g["8"]["inputs"]["seed"] == 42 and g["11"]["inputs"]["filename_prefix"] == "pfx"
    assert "a keep" in g["5"]["inputs"]["text"]
    assert (g["7"]["inputs"]["width"], g["7"]["inputs"]["height"]) == ca.STILL_SIZE
    c = art._clip_graph("staged.png", 7, "clip")
    assert c["5"]["inputs"]["image"] == "staged.png" and c["10"]["inputs"]["noise_seed"] == 7
    assert c["13"]["inputs"]["filename_prefix"] == "clip"
    assert c["6"]["inputs"]["length"] == ca.CLIP_FRAMES and (ca.CLIP_FRAMES - 1) % 8 == 0


def test_still_submits_graph_and_returns_path(tmp_path):
    g = fake_gen({})
    g._submit_and_wait = AsyncMock(return_value={"output_file": str(tmp_path / "a.png")})
    out = run(CinematicArtist(g).still("a keep", tmp_path / "sub", seed=5))
    assert out == tmp_path / "a.png" and (tmp_path / "sub").is_dir()
    graph, outdir, tag = g._submit_and_wait.await_args.args
    assert graph["8"]["inputs"]["seed"] == 5 and outdir == tmp_path / "sub" and tag == "cinematic"


def test_still_none_when_nothing_produced(tmp_path):
    g = fake_gen({})
    g._submit_and_wait = AsyncMock(return_value={})
    assert run(CinematicArtist(g).still("x", tmp_path)) is None


def test_still_refuses_unsafe_dir(tmp_path):
    g = fake_gen({})
    g._checked_output_dir = MagicMock(side_effect=ValueError("Refusing"))
    with pytest.raises(ValueError):
        run(CinematicArtist(g).still("x", tmp_path))


def test_clip_without_comfy_input_dir_is_none(tmp_path):
    g = fake_gen({})
    g._ensure_comfyui_input_dir = AsyncMock(return_value=None)
    g._submit_and_wait = AsyncMock()
    assert run(CinematicArtist(g).clip(tmp_path / "s.png", tmp_path)) is None
    g._submit_and_wait.assert_not_awaited()


def test_clip_stages_still_and_prefers_webm(tmp_path, monkeypatch):
    still = tmp_path / "s.png"
    still.write_bytes(b"PNGDATA")
    inp = tmp_path / "input"
    inp.mkdir()
    mp4 = tmp_path / "c.mp4"
    g = fake_gen({})
    g._ensure_comfyui_input_dir = AsyncMock(return_value=inp)
    seen = {}

    async def submit(graph, *a, **k):          # ComfyUI reads the staged still while this runs
        name = graph["5"]["inputs"]["image"]
        seen["name"], seen["data"] = name, (inp / name).read_bytes()
        return {"output_file": str(mp4)}
    g._submit_and_wait = AsyncMock(side_effect=submit)
    webm = tmp_path / "c.webm"
    monkeypatch.setattr(ca, "to_webm", AsyncMock(return_value=webm))
    assert run(CinematicArtist(g).clip(still, tmp_path, seed=3)) == webm
    assert seen["data"] == b"PNGDATA"
    assert list(inp.iterdir()) == []           # and it is cleaned up afterwards
    assert g._submit_and_wait.await_args.kwargs["accept"] == (".mp4", ".webm")


def test_clip_falls_back_to_mp4_and_none(tmp_path, monkeypatch):
    still = tmp_path / "s.png"
    still.write_bytes(b"x")
    g = fake_gen({})
    g._ensure_comfyui_input_dir = AsyncMock(return_value=tmp_path)
    g._submit_and_wait = AsyncMock(return_value={"output_file": str(tmp_path / "c.mp4")})
    monkeypatch.setattr(ca, "to_webm", AsyncMock(return_value=None))
    assert run(CinematicArtist(g).clip(still, tmp_path)) == tmp_path / "c.mp4"
    g._submit_and_wait = AsyncMock(return_value={})
    assert run(CinematicArtist(g).clip(still, tmp_path)) is None


def test_to_webm_without_ffmpeg(monkeypatch, tmp_path):
    monkeypatch.setattr(ca.shutil, "which", lambda n: None)
    assert run(to_webm(tmp_path / "a.mp4")) is None


def _proc(rc, err=b""):
    p = MagicMock()
    p.communicate = AsyncMock(return_value=(b"", err))
    p.returncode = rc
    return p


def test_to_webm_success_builds_vp9_command(monkeypatch, tmp_path):
    mp4 = tmp_path / "a.mp4"
    created = {}

    async def fake_exec(*args, **kw):
        created["args"] = args
        (tmp_path / "a.webm").write_bytes(b"w")
        return _proc(0)
    monkeypatch.setattr(ca.shutil, "which", lambda n: "/usr/bin/ffmpeg")
    monkeypatch.setattr(ca.asyncio, "create_subprocess_exec", fake_exec)
    assert run(to_webm(mp4)) == tmp_path / "a.webm"
    a = created["args"]
    assert a[0] == "ffmpeg" and str(mp4) in a and "libvpx-vp9" in a and a[-1] == str(tmp_path / "a.webm")


@pytest.mark.parametrize("rc,make_file", [(1, True), (0, False)])
def test_to_webm_failure_returns_none(monkeypatch, tmp_path, rc, make_file):
    async def fake_exec(*args, **kw):
        if make_file:
            (tmp_path / "a.webm").write_bytes(b"w")
        return _proc(rc, b"bad codec")
    monkeypatch.setattr(ca.shutil, "which", lambda n: "/usr/bin/ffmpeg")
    monkeypatch.setattr(ca.asyncio, "create_subprocess_exec", fake_exec)
    assert run(to_webm(tmp_path / "a.mp4")) is None
