"""Prologue: scripts sent to Foundry, replay flow, cinema staging, interruption."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

import campaign.prologue as pro


def run(c):
    return asyncio.run(c)


@pytest.fixture
def sleeps(monkeypatch):
    rec = []

    async def fake(s):
        rec.append(s)
    monkeypatch.setattr(pro.asyncio, "sleep", fake)
    return rec


def foundry(*results):
    f = MagicMock()
    f.execute_js = AsyncMock(side_effect=list(results))
    f._prologue_interrupt_event = None
    return f


# ── pure helpers ─────────────────────────────────────────────────────────

def test_strip_and_truncate():
    assert pro._strip_html("<p>a  <b>b</b>\n c</p>") == "a b c"
    assert pro._strip_html(None) == ""
    assert pro._truncate("abc", 3) == "abc"
    t = pro._truncate("x" * 50, 10)
    assert t == "x" * 9 + "…" and len(t) == 10


def test_dwell_scales_and_caps():
    assert pro._panel_dwell_seconds("") == 4.0
    assert pro._panel_dwell_seconds("x" * 150) == 14.0
    assert pro._panel_dwell_seconds("x" * 10000) == 25.0


def test_build_pages_edge_cases():
    # no frame -> no Prologue page; non-dict panels skipped; era optional; image_file prefixed
    pages = pro.build_prologue_pages({"panels": ["junk", {"title": "P", "body": " b ", "image_file": "p.png"}]})
    assert [p["type"] for p in pages] == ["image", "text"]
    assert pages[0]["image"]["src"] == "ai-gm-prologue/p.png"
    assert pages[1]["text"]["content"] == "<h2>P</h2><p>b</p>"
    # frame only
    only = pro.build_prologue_pages({"title": "T", "frame_narrative": " f "})
    assert only[0]["name"] == "Prologue" and only[0]["text"]["content"] == "<h2>T</h2><p><em>f</em></p>"
    assert pro.build_prologue_pages({}) == []
    assert "<h3><em>[old]</em></h3>" in pro.build_prologue_pages({"panels": [{"era": "old"}]})[0]["text"]["content"]


def test_describe_prologue_variants():
    assert pro.describe_prologue({}) == "Prologue 'Prologue' (tome)."
    entry = {"title": "T", "vessel": "scroll", "pages": [
        {"type": "image", "content": "ignored"},
        {"type": "text", "name": "Prologue", "content": "<p>Frame text</p>"},
        {"type": "text", "name": "One", "content": "<p>first</p>"},
        {"type": "text", "content": "second"},
        "junk",
    ]}
    assert pro.describe_prologue(entry) == "Prologue 'T' (scroll). Frame: Frame text Beats: One: first | Panel: second"
    # no frame page -> everything is a beat
    e2 = {"pages": [{"type": "text", "name": "A", "content": "a"}]}
    assert pro.describe_prologue(e2).endswith("Beats: A: a")


# ── Foundry scripts ──────────────────────────────────────────────────────

def test_load_entry_script_variants_and_result_shape():
    f = foundry({"result": {"uuid": "u"}})
    assert run(pro.load_prologue_entry(f)) == {"uuid": "u"}
    js = f.execute_js.await_args.args[0]
    assert "const target = null;" in js and "!j.flags" in js
    f = foundry({"result": {"uuid": "u"}})
    run(pro.load_prologue_entry(f, 'Journal."x"', include_shown=True))
    js = f.execute_js.await_args.args[0]
    assert json.dumps('Journal."x"') in js and "!j.flags" not in js
    assert run(pro.load_prologue_entry(foundry({"result": [1]}))) is None
    assert run(pro.load_prologue_entry(foundry("str"))) is None


def test_set_shown_and_reset():
    f = foundry({"result": True})
    assert run(pro._set_prologue_shown(f, "J.1", False)) is True
    assert '"flags.ai-gm.shown": false' in f.execute_js.await_args.args[0]
    assert run(pro._set_prologue_shown(foundry({"result": False}), "J", True)) is False
    assert run(pro._set_prologue_shown(foundry("x"), "J", True)) is False


def test_reset_prologue_shown_flow():
    # entry missing -> False without a second call
    f = foundry({"result": None})
    assert run(pro.reset_prologue_shown(f)) is False and f.execute_js.await_count == 1
    f = foundry({"result": {"uuid": "J.9"}}, {"result": True})
    assert run(pro.reset_prologue_shown(f, "J.9")) is True
    assert "shown" in f.execute_js.await_args_list[1].args[0] and "J.9" in f.execute_js.await_args_list[1].args[0]
    assert "!j.flags" not in f.execute_js.await_args_list[0].args[0]     # include_shown=True


def test_call_narrate_handles_sync_and_async():
    seen = []
    run(pro._call_narrate(lambda t: seen.append(t), "a"))

    async def anarr(t):
        seen.append(t + "!")
    run(pro._call_narrate(anarr, "b"))
    assert seen == ["a", "b!"]


# ── dwell ────────────────────────────────────────────────────────────────

def test_dwell_steps_in_one_second_chunks(sleeps):
    run(pro._dwell(2.5, None))
    assert sleeps == [1.0, 1.0, 0.5]


def test_dwell_interrupt_returns_early(sleeps, monkeypatch):
    ev = asyncio.Event()
    ev.set()
    run(pro._dwell(10, ev))
    assert sleeps == [1]
    ev2 = asyncio.Event()

    async def set_after(s):
        sleeps.append(s)
        ev2.set()
    monkeypatch.setattr(pro.asyncio, "sleep", set_after)
    sleeps.clear()
    run(pro._dwell(10, ev2))
    assert sleeps == [1.0]


# ── present_prologue ─────────────────────────────────────────────────────

ENTRY = {"uuid": "J.1", "title": "T", "shown": False, "pages": [
    {"type": "image", "name": "Img", "src": "a.png"},
    {"type": "text", "name": "Prologue", "content": "<p>Frame</p>"},
    {"type": "text", "name": "P1", "content": "<p>Body</p>"},
    {"type": "audio", "name": "x"},
]}


def test_present_skips_when_missing_shown_or_unmarkable(sleeps):
    narr = MagicMock()
    f = foundry({"result": None})
    assert run(pro.present_prologue(f, narr, "J")) is False
    assert run(pro.present_prologue(foundry(), narr, "J", entry={"uuid": "J", "shown": True})) is False
    assert run(pro.present_prologue(foundry(), narr, "J", entry={"title": "no uuid"})) is False
    f = foundry({"result": False})                       # could not set shown flag
    assert run(pro.present_prologue(f, narr, "J", entry=dict(ENTRY))) is False
    narr.assert_not_called()


def test_present_without_pages_marks_shown_only(sleeps):
    f = foundry({"result": True})
    assert run(pro.present_prologue(f, MagicMock(), "J", entry={"uuid": "J", "pages": []})) is True
    assert f.execute_js.await_count == 1


def test_present_plain_flow_shares_image_and_narrates(sleeps):
    f = foundry({"result": True}, {"result": True})      # mark shown, share image
    spoken = []
    assert run(pro.present_prologue(f, spoken.append, "J", entry=dict(ENTRY))) is True
    assert spoken == ["Frame", "Body"]
    share = f.execute_js.await_args_list[1].args[0]
    assert '"a.png"' in share and '"Img"' in share
    # frame page dwells 1 s; body "Body" = 4.27 s -> 1.0 | 1,1,1,1,0.27
    assert sleeps[0] == 1.0 and sum(sleeps[1:]) == pytest.approx(4 + 4 / 15)


def test_present_uses_interrupt_event_from_foundry(sleeps):
    f = foundry({"result": True}, {"result": True})
    ev = asyncio.Event()
    ev.set()
    f._prologue_interrupt_event = ev
    run(pro.present_prologue(f, lambda t: None, "J", entry=dict(ENTRY)))
    assert set(sleeps) == {1}            # interrupted dwell: a single 1 s beat per text page


def test_present_survives_image_and_narration_failures(sleeps):
    f = foundry({"result": True}, RuntimeError("popout"))
    calls = []

    def narr(t):
        calls.append(t)
        if t == "Frame":
            raise RuntimeError("tts down")
    assert run(pro.present_prologue(f, narr, "J", entry=dict(ENTRY))) is True
    assert calls == ["Frame", "Body"]
    # failed narration skips its dwell: only Body dwelled
    assert sum(sleeps) == pytest.approx(4 + 4 / 15)


def test_present_skips_empty_text_and_non_dict_pages(sleeps):
    entry = {"uuid": "J", "title": "T", "pages": ["x", {"type": "text", "content": "<p> </p>"}, {"type": "image"}]}
    spoken = []
    assert run(pro.present_prologue(foundry({"result": True}), spoken.append, "J", entry=entry)) is True
    assert spoken == []


def _cinema(available=True, saved=("flags",), set_ok=True):
    c = MagicMock()
    c.available = AsyncMock(return_value=available)
    c.scene_flags = AsyncMock(return_value=saved)
    c.set_scene = AsyncMock(return_value=set_ok)
    c.say = AsyncMock()
    c.clear_subtitles = AsyncMock()
    c.restore_scene = AsyncMock()
    return c


def test_present_with_cinema_sets_backdrop_subtitles_and_restores(sleeps):
    f = foundry({"result": True})
    c = _cinema()
    spoken = []
    assert run(pro.present_prologue(f, spoken.append, "J", entry=dict(ENTRY), cinema=c)) is True
    assert f.execute_js.await_count == 1                          # no popout when cinema is on
    c.set_scene.assert_any_await(active=True, dim=0.0)
    c.set_scene.assert_any_await(background="a.png")
    assert [x.args[:2] for x in c.say.await_args_list] == [("Narrator", "Frame"), ("Narrator", "Body")]
    assert all(x.kwargs["duration_s"] >= 3.0 for x in c.say.await_args_list)
    c.clear_subtitles.assert_awaited_once()
    c.restore_scene.assert_awaited_once_with(None, ("flags",))


@pytest.mark.parametrize("kw", [dict(available=False), dict(saved=None), dict(set_ok=False)])
def test_present_cinema_unusable_falls_back_to_popouts(sleeps, kw):
    f = foundry({"result": True}, {"result": True})
    c = _cinema(**kw)
    run(pro.present_prologue(f, lambda t: None, "J", entry=dict(ENTRY), cinema=c))
    assert "ImagePopout" in f.execute_js.await_args_list[1].args[0]
    c.say.assert_not_awaited()
    c.restore_scene.assert_not_awaited()


def test_present_cinema_restore_failure_is_swallowed(sleeps):
    c = _cinema()
    c.restore_scene = AsyncMock(side_effect=RuntimeError("gone"))
    assert run(pro.present_prologue(foundry({"result": True}), lambda t: None, "J", entry=dict(ENTRY), cinema=c)) is True
    c.clear_subtitles.assert_awaited_once()


def test_present_restores_cinema_even_if_playback_raises(sleeps):
    c = _cinema()
    c.say = AsyncMock(side_effect=RuntimeError("boom"))
    with pytest.raises(RuntimeError):
        run(pro.present_prologue(foundry({"result": True}), lambda t: None, "J", entry=dict(ENTRY), cinema=c))
    c.restore_scene.assert_awaited_once()


def test_dwell_failure_is_logged_not_raised(sleeps, monkeypatch):
    monkeypatch.setattr(pro, "_dwell", AsyncMock(side_effect=RuntimeError("x")))
    spoken = []
    assert run(pro.present_prologue(foundry({"result": True}, {"result": True}), spoken.append, "J", entry=dict(ENTRY))) is True
    assert spoken == ["Frame", "Body"]
