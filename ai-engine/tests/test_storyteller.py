"""Storyteller add-ons: Cinema subtitles and stage, the open-book journal sheet, and the optional cinematic art.

Cinema's `say` writes the speaker and text into every client's page as raw HTML and takes its duration in MILLISECONDS
(both read from its source), so those two are the behaviors most worth pinning.
"""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from campaign import cinematic_art, prologue
from campaign.cinematic_art import CinematicArtist, still_prompt
from campaign.modules.registry import MODULE_REGISTRY, run_flag_hook
from campaign.modules.storyteller_cinema import on_scene
from campaign.modules.storyteller_x import BOOK_SHEET, book_sheet_flags
from config import settings
from immersion import storyteller
from immersion.cinema import CinemaDirector, reading_time


def _foundry(result=True):
    f = MagicMock()
    f.execute_js = AsyncMock(return_value={"result": result})
    return f


def _scripts(foundry):
    return [c.args[0] for c in foundry.execute_js.await_args_list]


# ── CinemaDirector ──────────────────────────────────────────────────────────

def test_it_is_unavailable_when_the_module_is_not_active_or_the_script_cannot_run():
    assert asyncio.run(CinemaDirector(_foundry(False)).available()) is False
    broken = MagicMock()
    broken.execute_js = AsyncMock(side_effect=RuntimeError("relay down"))
    assert asyncio.run(CinemaDirector(broken).available()) is False
    assert asyncio.run(CinemaDirector(MagicMock()).available()) is False          # a client that is not awaitable at all


def test_availability_is_cached_briefly():
    f = _foundry(True)
    d = CinemaDirector(f)

    async def run():
        await d.available()
        await d.available()

    asyncio.run(run())
    assert f.execute_js.await_count == 1


def test_say_escapes_speaker_and_text_and_sends_milliseconds():
    f = _foundry(True)
    d = CinemaDirector(f)

    asyncio.run(d.say('Vex <b>"the Bold"</b>', '<img src=x onerror=alert(1)> Run!', portrait="a/b.png", duration_s=4.5))

    script = _scripts(f)[-1]
    assert "<img" not in script and "&lt;img" in script and "&lt;b&gt;" in script       # no raw markup reaches the page
    assert '"duration": 4500' in script and '"portrait": "a/b.png"' in script


def test_say_does_nothing_when_cinema_is_not_there():
    f = _foundry(False)
    assert asyncio.run(CinemaDirector(f).say("Vex", "hello")) is False
    assert len(_scripts(f)) == 1                         # only the availability probe; no subtitle script ran


def test_subtitles_are_cleared_without_touching_settings():
    f = _foundry(True)
    asyncio.run(CinemaDirector(f).clear_subtitles())
    assert "clearSubtitles()" in _scripts(f)[-1] and "settings" not in _scripts(f)[-1]


def test_set_scene_writes_only_the_flags_given():
    f = _foundry(True)
    asyncio.run(CinemaDirector(f).set_scene(active=True, dim=0.0))
    script = _scripts(f)[-1]
    assert '"active": true' in script and '"cinematicBgDim": 0.0' in script and "cinematicBg\"" not in script.replace("cinematicBgDim", "")
    assert "game.scenes.active" in script


def test_set_scene_by_name_resolves_id_or_name_safely():
    f = _foundry(True)
    asyncio.run(CinemaDirector(f).set_scene('Gate "North"', background="x.png"))
    script = _scripts(f)[-1]
    assert json.dumps('Gate "North"') in script and "game.scenes.getName" in script


def test_restore_puts_back_set_flags_and_unsets_the_rest():
    f = _foundry(True)
    asyncio.run(CinemaDirector(f).restore_scene(None, {"active": None, "viewMode": "cinematic", "cinematicBg": None, "cinematicBgDim": None}))
    script = _scripts(f)[-1]
    assert "unsetFlag('storyteller-cinema', k)" in script and '"viewMode": "cinematic"' in script


def test_reading_time_is_bounded():
    assert reading_time("Hi.") == 3.0 and reading_time("word " * 200) == 12.0 and 3.0 < reading_time("word " * 15) < 12.0


# ── NPC subtitles ───────────────────────────────────────────────────────────

def _speak(cinema, *, whisper=None, enabled=True, text="Halt! Who goes there?"):
    from actions import executors

    foundry = SimpleNamespace(chat_message=AsyncMock(return_value={}), cinema=cinema)
    seen = []

    def spawn(coro):
        seen.append(coro)
        coro.close()

    with patch.object(executors, "_is_player_character", AsyncMock(return_value=False)), \
         patch.object(executors, "spawn", spawn), \
         patch.object(settings, "cinema_subtitles", enabled), \
         patch.object(executors.tts_playback, "is_active", lambda: False):
        asyncio.run(executors.execute_speak("Captain Brek", text, whisper_to=whisper, foundry=foundry))
    return seen


def test_an_npc_line_becomes_a_subtitle():
    cinema = CinemaDirector(_foundry(True))
    cinema.say = MagicMock(return_value=asyncio.sleep(0))
    _speak(cinema)
    cinema.say.assert_called_once()
    assert cinema.say.call_args.args == ("Captain Brek", "Halt! Who goes there?")
    assert 3.0 <= cinema.say.call_args.kwargs["duration_s"] <= 12.0


def test_whispers_and_the_off_switch_show_no_subtitle():
    cinema = CinemaDirector(_foundry(True))
    cinema.say = MagicMock(return_value=asyncio.sleep(0))
    _speak(cinema, whisper="Chris")
    _speak(cinema, enabled=False)
    cinema.say.assert_not_called()


def test_a_client_without_cinema_speaks_exactly_as_before():
    plain = _speak(None)                           # no cinema attached: no error
    imposter = MagicMock()
    spawned = _speak(imposter)                     # a mock is not a CinemaDirector: ignored, never awaited
    imposter.say.assert_not_called()
    assert len(spawned) == len(plain)


# ── prologue on the cinematic stage ─────────────────────────────────────────

ENTRY = {"uuid": "J.1", "title": "The Ashen Crown", "shown": False, "pages": [
    {"type": "text", "name": "Prologue", "content": "<p>Long ago.</p>"},
    {"type": "image", "name": "Panel", "src": "ai-gm/panel1.png"},
    {"type": "text", "name": "Panel", "content": "<p>The crown fell.</p>"}]}


def _play(cinema, foundry):
    narrated = []
    with patch.object(prologue, "_set_prologue_shown", AsyncMock(return_value=True)), \
         patch.object(prologue, "_dwell", AsyncMock()):
        asyncio.run(prologue.present_prologue(foundry, lambda t: narrated.append(t), "J.1", entry=ENTRY, cinema=cinema))
    return narrated


class _FakeCinema:
    def __init__(self, available=True, saved=None):
        self._available, self.saved, self.calls = available, saved if saved is not None else {"active": None}, []

    async def available(self):
        return self._available

    async def scene_flags(self, scene=None):
        return self.saved

    async def set_scene(self, scene=None, **kw):
        self.calls.append(("set_scene", kw))
        return True

    async def say(self, speaker, text, **kw):
        self.calls.append(("say", speaker, text))
        return True

    async def clear_subtitles(self):
        self.calls.append(("clear",))
        return True

    async def restore_scene(self, scene, saved):
        self.calls.append(("restore", saved))
        return True


def test_with_cinema_the_prologue_is_staged_subtitled_and_put_back():
    cinema = _FakeCinema(saved={"active": None, "viewMode": "battlemap"})
    foundry = _foundry(True)

    narrated = _play(cinema, foundry)

    kinds = [c[0] for c in cinema.calls]
    assert cinema.calls[0] == ("set_scene", {"active": True, "dim": 0.0})
    assert ("set_scene", {"background": "ai-gm/panel1.png"}) in cinema.calls
    assert [c[2] for c in cinema.calls if c[0] == "say"] == ["Long ago.", "The crown fell."]
    assert kinds[-2:] == ["clear", "restore"] and cinema.calls[-1][1] == {"active": None, "viewMode": "battlemap"}
    assert narrated == ["Long ago.", "The crown fell."]                 # chat and TTS narration still happen
    assert not any("ImagePopout" in s for s in _scripts(foundry))      # the popout is replaced by the stage


def test_without_cinema_images_are_still_shared_as_popouts():
    foundry = _foundry(True)
    _play(_FakeCinema(available=False), foundry)
    assert any("ImagePopout" in s for s in _scripts(foundry))


def test_the_scene_is_restored_even_if_playback_fails():
    cinema = _FakeCinema()
    with patch.object(prologue, "_play_pages", AsyncMock(side_effect=RuntimeError("boom"))), \
         patch.object(prologue, "_set_prologue_shown", AsyncMock(return_value=True)):
        with pytest.raises(RuntimeError):
            asyncio.run(prologue.present_prologue(_foundry(True), lambda t: None, "J.1", entry=ENTRY, cinema=cinema))
    assert cinema.calls[-1][0] == "restore"


# ── module integrations ─────────────────────────────────────────────────────

def test_both_modules_are_registered():
    assert "storyteller-cinema" in MODULE_REGISTRY and "story-teller-x" in MODULE_REGISTRY


def test_a_social_scene_with_a_backdrop_opens_cinematic():
    scene = {"type": "tavern", "_cinematic_bg_src": "ai-gm-cinematic/x/still.png"}
    assert on_scene(scene, {}) == {"cinematicBg": "ai-gm-cinematic/x/still.png", "cinematicBgDim": 0.0, "viewMode": "cinematic"}
    assert on_scene({"type": "dungeon", "_cinematic_bg_src": "s.png"}, {})["viewMode"] == "battlemap"


def test_a_scene_with_no_backdrop_is_left_alone():
    assert on_scene({"type": "tavern"}, {}) is None
    assert run_flag_hook("on_scene", {"type": "tavern"}, {"storyteller-cinema": True}) in (None, {})


def test_explicit_module_flags_win():
    assert on_scene({"type": "tavern", "module_flags": {"storyteller-cinema": {"viewMode": "battlemap"}}}, {}) == {"viewMode": "battlemap"}


def test_the_book_sheet_only_when_the_module_is_active():
    assert book_sheet_flags({"story-teller-x": True}) == {"core": {"sheetClass": BOOK_SHEET}}
    assert book_sheet_flags({"midi-qol": True}) == {} and book_sheet_flags(None) == {}


def test_recaps_outside_deploy_find_out_whether_to_be_a_book():
    storyteller._cache.clear()
    f = MagicMock()
    f.get_active_modules_info = AsyncMock(return_value={"modules": [{"id": "story-teller-x"}, {"id": "dae"}]})
    assert asyncio.run(storyteller.book_flags(f)) == {"core": {"sheetClass": BOOK_SHEET}}
    storyteller._cache.clear()
    assert asyncio.run(storyteller.book_flags(MagicMock())) == {}                  # unreadable: no flag, no error


# ── cinematic art ───────────────────────────────────────────────────────────

def _generator(info=None, **extra):
    gen = MagicMock()
    gen.comfyui_base_url = "http://c"

    async def get(url, **kw):
        node = url.rsplit("/", 1)[-1]
        return SimpleNamespace(json=lambda: {node: {"input": {"required": {
            "unet_name": [["z_image_turbo_bf16.safetensors"]], "clip_name": [["qwen_3_4b.safetensors", "t5xxl_fp16.safetensors"]],
            "vae_name": [["ae.safetensors"]], "ckpt_name": [["ltxv-2b-0.9.8-distilled.safetensors"]], "width": ["INT"]}}}})

    gen._client = SimpleNamespace(get=get)
    gen.__dict__.update(extra)
    return gen


def test_availability_reports_which_stages_comfyui_can_run():
    assert asyncio.run(CinematicArtist(_generator()).available()) == {"still": True, "clip": True}
    with patch.object(settings, "ltx_checkpoint", "missing.safetensors"):
        assert asyncio.run(CinematicArtist(_generator()).available()) == {"still": True, "clip": False}


def test_availability_is_false_when_comfyui_cannot_be_asked():
    gen = MagicMock()
    gen.comfyui_base_url = "http://c"
    gen._client = SimpleNamespace(get=AsyncMock(side_effect=RuntimeError("down")))
    assert asyncio.run(CinematicArtist(gen).available()) == {"still": False, "clip": False}


def test_the_still_prompt_asks_for_a_picture_you_look_into():
    text = still_prompt("a torch-lit gatehouse at dusk")
    assert "establishing shot" in text and "gatehouse" in text and "no border" in text


def test_the_graphs_use_valid_ltx_dimensions_and_the_still_model():
    artist = CinematicArtist(_generator())
    still = artist._still_graph("a gate", 5, "p")
    assert still["1"]["inputs"]["unet_name"] == "z_image_turbo_bf16.safetensors"
    w, h = cinematic_art.STILL_SIZE
    assert w % 32 == 0 and h % 32 == 0 and still["7"]["inputs"]["width"] == w
    clip = artist._clip_graph("staged.png", 9, "c")
    n = clip["6"]["inputs"]
    assert (n["length"] - 1) % 8 == 0 and n["width"] % 32 == 0 and n["height"] % 32 == 0       # LTX: 8n+1 frames
    assert clip["13"]["class_type"] == "SaveVideo" and "/" not in clip["13"]["inputs"]["filename_prefix"]   # root only: /view fetches the root


def test_no_ffmpeg_means_the_mp4_is_kept():
    with patch.object(cinematic_art.shutil, "which", return_value=None):
        assert asyncio.run(cinematic_art.to_webm(Path("/tmp/x.mp4"))) is None


def test_video_outputs_are_downloaded_when_asked_for():
    from campaign.map_generator import MapGenerator

    gen = MapGenerator()
    entry = {"p1": {"status": {"status_str": "success"}, "outputs": {"13": {"images": [{"filename": "clip_00001_.mp4"}]}}}}
    resp = SimpleNamespace(status_code=200, json=lambda: entry)

    async def run(accept):
        with patch.object(gen._client, "get", AsyncMock(return_value=resp)), \
             patch.object(MapGenerator, "_download_image", AsyncMock(return_value=Path("/tmp/ai-gm-maps/clip.mp4"))) as dl:
            out = await gen._wait_for_completion("p1", Path("/tmp/ai-gm-maps"), accept) if accept else None
            return out, dl.await_count

    assert asyncio.run(run((".mp4", ".webm"))) == (Path("/tmp/ai-gm-maps/clip.mp4"), 1)


# ── build and upload ────────────────────────────────────────────────────────

def _orch():
    from campaign.orchestrator import CampaignOrchestrator
    return CampaignOrchestrator.__new__(CampaignOrchestrator)


def test_art_is_only_built_when_enabled_and_for_social_scenes(tmp_path):
    orch = _orch()
    scene = {"type": "tavern", "description": "a warm tavern"}
    with patch.object(settings, "cinema_art_enabled", False):
        asyncio.run(orch._build_cinematic_art(scene, MagicMock(), tmp_path))
    assert "_cinematic_still_file" not in scene
    with patch.object(settings, "cinema_art_enabled", True), \
         patch("campaign.orchestrator_assets.CinematicArtist") as artist_cls:
        asyncio.run(orch._build_cinematic_art({"type": "dungeon"}, MagicMock(), tmp_path))
    artist_cls.assert_not_called()


def test_a_social_scene_gets_a_still_and_a_clip_when_both_are_on(tmp_path):
    orch = _orch()
    scene = {"type": "tavern", "description": "a warm tavern"}
    artist = MagicMock()
    artist.available = AsyncMock(return_value={"still": True, "clip": True})
    artist.still = AsyncMock(return_value=tmp_path / "s.png")
    artist.clip = AsyncMock(return_value=tmp_path / "c.webm")
    with patch.object(settings, "cinema_art_enabled", True), patch.object(settings, "cinema_video_enabled", True), \
         patch("campaign.orchestrator_assets.CinematicArtist", return_value=artist):
        asyncio.run(orch._build_cinematic_art(scene, MagicMock(), tmp_path))
    assert scene["_cinematic_still_file"] == "s.png" and scene["_cinematic_clip_file"] == "c.webm"


def test_a_failing_art_step_never_breaks_the_build(tmp_path):
    orch = _orch()
    artist = MagicMock()
    artist.available = AsyncMock(side_effect=RuntimeError("comfyui exploded"))
    scene = {"type": "tavern"}
    with patch.object(settings, "cinema_art_enabled", True), patch("campaign.orchestrator_assets.CinematicArtist", return_value=artist):
        asyncio.run(orch._build_cinematic_art(scene, MagicMock(), tmp_path))     # does not raise
    assert "_cinematic_still_file" not in scene and "_cinematic_clip_file" not in scene


def _upload(tmp_path, *, video_backgrounds, uploads=None):
    orch = _orch()
    (tmp_path / "s.png").write_bytes(b"png")
    (tmp_path / "c.webm").write_bytes(b"webm")
    scene = {"name": "Inn", "_cinematic_still_file": "s.png", "_cinematic_clip_file": "c.webm"}
    seen = []

    async def fake_upload(client, path, upload_dir, name, fallback, mime_type="image/png"):
        seen.append((name, mime_type))
        return (uploads or {}).get(name, {"ok": True, "src": f"{upload_dir}/{name}"})

    summary = {"errors": []}
    with patch("campaign.orchestrator_assets.upload_image", fake_upload), patch.object(settings, "cinema_video_backgrounds", video_backgrounds):
        asyncio.run(orch._upload_cinematic_art(scene, MagicMock(), tmp_path, "camp", summary))
    return scene, seen, summary


def test_the_still_is_the_default_backdrop_and_the_clip_is_opt_in(tmp_path):
    scene, seen, _ = _upload(tmp_path, video_backgrounds=False)
    assert scene["_cinematic_bg_src"] == "ai-gm-cinematic/camp/s.png" and ("c.webm", "video/webm") in seen
    scene, _, _ = _upload(tmp_path, video_backgrounds=True)
    assert scene["_cinematic_bg_src"] == "ai-gm-cinematic/camp/c.webm"


def test_a_failed_clip_upload_falls_back_to_the_still_and_is_noted(tmp_path):
    scene, _, summary = _upload(tmp_path, video_backgrounds=True, uploads={"c.webm": {"ok": False, "error": "413"}})
    assert scene["_cinematic_bg_src"] == "ai-gm-cinematic/camp/s.png" and "413" in summary["errors"][0]


def test_a_cinematic_file_name_cannot_escape_the_asset_directory(tmp_path):
    """The name is read from campaign data: `../secret.png` must neither be read nor uploaded (CodeQL py/path-injection)."""
    orch = _orch()
    assets = tmp_path / "assets"
    assets.mkdir()
    (tmp_path / "secret.png").write_bytes(b"not for upload")
    (assets / "notes.txt").write_text("wrong type")
    scene = {"name": "Inn", "_cinematic_still_file": "../secret.png", "_cinematic_clip_file": "notes.txt"}
    seen, summary = [], {"errors": []}

    async def fake_upload(*args, **kwargs):
        seen.append(args)
        return {"ok": True, "src": "x"}

    with patch("campaign.orchestrator_assets.upload_image", fake_upload):
        asyncio.run(orch._upload_cinematic_art(scene, MagicMock(), assets, "camp", summary))

    assert seen == [] and "_cinematic_bg_src" not in scene
    assert any("outside the asset directory" in e for e in summary["errors"])
