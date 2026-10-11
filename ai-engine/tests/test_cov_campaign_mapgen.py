"""Coverage-gap tests for campaign.map_generator: procedural mask default dir,
history polling states, download I/O errors, controlnet copy skips, portraits."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

import campaign.map_generator as mgmod
from campaign.map_generator import MapGenerator
from config import settings

PIL = pytest.importorskip("PIL.Image")
OUT = Path("campaign_assets/t_maps")


def run(c):
    return asyncio.run(c)


def resp(status=200, json=None, content=b""):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = json
    r.content = content
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


@pytest.fixture
def fastclock(monkeypatch):
    t = {"now": 0.0}
    monkeypatch.setattr(mgmod.time, "time", lambda: t["now"])

    async def sleep(s):
        t["now"] += s

    monkeypatch.setattr(mgmod.asyncio, "sleep", sleep)
    return t


def test_procedural_mask_without_output_dir_goes_to_campaign_assets(mg, tmp_path):
    p = run(mg.generate_procedural_layout_mask({"grid_width": 20, "grid_height": 15}, 1280, 960, 64, seed=3))
    assert p.parent == Path("./campaign_assets") / "layouts"
    assert p.exists()


def test_wait_keeps_polling_through_non_200_missing_entry_and_running(mg, fastclock):
    hist_ok = {"p": {"status": {"status_str": "success"}, "outputs": {"9": {"images": [{"filename": "a.png"}]}}}}
    mg._client.get.side_effect = [
        resp(status=500),
        resp(json={}),                                              # prompt not in history yet
        resp(json={"p": {"status": {"status_str": "running"}}}),    # queued/running
        resp(json=hist_ok),
    ]
    mg._download_image = AsyncMock(return_value=Path("a.png"))
    assert run(mg._wait_for_completion("p", OUT)) == Path("a.png")
    assert mg._client.get.await_count == 4
    assert fastclock["now"] == 6          # three 2s sleeps before success


def test_download_oserror_is_logged_and_returns_none(mg, tmp_path):
    mg._client.get.return_value = resp(content=b"PNG")
    missing = tmp_path / "no_such_dir"
    assert run(mg._download_image("a.png", missing)) is None
    assert not missing.exists()


def test_controlnet_skips_copy_when_mask_already_in_place(mg, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "map_muted_palette", False)
    out = Path("campaign_assets/t_maps")
    (out / "layouts").mkdir(parents=True)
    mask = out / "layouts" / "mask.png"
    mask.write_bytes(b"M")                                # already where the record copy would go
    comfy_in = tmp_path / "ci"
    comfy_in.mkdir()
    (comfy_in / "mask.png").write_bytes(b"OLD")           # ComfyUI already has a file of that name
    mg.comfyui_input_dirs = [comfy_in]
    mg._client.get.return_value = resp(200)
    mg._submit_and_wait = AsyncMock(return_value={"status": "success"})
    assert run(mg.generate_map_controlnet("hall", mask, out))["status"] == "success"
    assert mask.read_bytes() == b"M"
    assert (comfy_in / "mask.png").read_bytes() == b"OLD"      # existing input file is not overwritten


def test_zimage_portrait_where_trim_changes_nothing_returns_result_untouched(mg, monkeypatch):
    monkeypatch.setattr(settings, "portrait_model", "zimage")
    mg._submit_and_wait = AsyncMock(return_value={"status": "success", "output_file": "p.png"})
    mg._trim_margins = MagicMock(return_value=False)
    res = run(mg.generate_portrait_comfyui("x", OUT))
    assert res == {"status": "success", "output_file": "p.png", "model": "zimage"}


def test_zimage_portrait_failed_generation_skips_trim(mg, monkeypatch):
    monkeypatch.setattr(settings, "portrait_model", "zimage")
    mg._submit_and_wait = AsyncMock(return_value={"status": "error", "output_file": None})
    mg._trim_margins = MagicMock()
    res = run(mg.generate_portrait_comfyui("x", OUT))
    assert res["status"] == "error" and res["model"] == "zimage"
    mg._trim_margins.assert_not_called()


def test_generate_portrait_routes_to_comfyui_when_healthy_and_errors_when_not(mg):
    mg.health_check = AsyncMock(return_value={"comfyui": True})
    mg.generate_portrait_comfyui = AsyncMock(return_value={"status": "success"})
    assert run(mg.generate_portrait("p", OUT, name="Bob", npc={"x": 1})) == {"status": "success"}
    mg.generate_portrait_comfyui.assert_awaited_once_with("p", OUT, name="Bob", npc={"x": 1})
    mg.health_check = AsyncMock(return_value={"comfyui": False})
    res = run(mg.generate_portrait("p", OUT))
    assert res["status"] == "error" and res["provider"] == "none"
