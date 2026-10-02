"""Follow-ups from the 2026-10-02 coverage review: each was red before its fix."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.deps import AppState, get_app_state
from api.routes import system as system_routes
from campaign import cinematic_art as ca
from campaign.cinematic_art import CinematicArtist, to_webm
from campaign.generator import generate_arc_extension_prompt
from combat.mechanics import CombatMechanics, TacticalAnalysis


def test_analyze_reports_the_ally_who_flanks_with_you():
    m = CombatMechanics()
    m.update_position("me", 0, 1)
    m.update_position("orc", 1, 1)
    m.update_position("buddy", 2, 1)      # directly opposite me across the orc
    m.update_position("bystander", 5, 5)  # nowhere near
    a = TacticalAnalysis.analyze(m, "me", ["orc"], ["buddy", "bystander"])
    assert a.flanking_enemies == ["orc"]
    assert a.flanking_allies == ["buddy"]
    assert any("1 ally" in r for r in a.get_recommendations())


def test_query_building_by_service_at_a_time_requires_someone_there():
    from procedural.settlement import (
        Building, NPCSchedule, Settlement, SettlementNPC, TimeSlot)
    s = Settlement(name="Redmarch", size="village")
    s.add_building(Building("Smithy", "shop", services=["repair"]))
    s.add_building(Building("Forge Annex", "shop", services=["repair"]))
    sched = NPCSchedule("Mara")
    sched.add_entry(TimeSlot.MORNING, "Smithy", "work")
    sched.add_entry(TimeSlot.EVENING, "Forge Annex", "work")
    s.add_npc(SettlementNPC("Mara", "smith", schedule=sched, building="Smithy"))
    assert [b.name for b in s.query_building_by_service("repair")] == ["Smithy", "Forge Annex"]
    assert [b.name for b in s.query_building_by_service("repair", TimeSlot.MORNING)] == ["Smithy"]
    assert [b.name for b in s.query_building_by_service("repair", TimeSlot.EVENING)] == ["Forge Annex"]
    assert s.query_building_by_service("repair", TimeSlot.NIGHT) == []


@pytest.mark.parametrize("level,end", [(1, 4), (4, 10), (5, 10), (9, 10), (10, 16), (13, 16), (16, 20), (20, 20)])
def test_arc_extension_advances_exactly_one_tier(level, end):
    prompt = generate_arc_extension_prompt({"campaign": {}}, level, 2, None)
    assert f"Levels {level}-{end}" in prompt


def test_to_webm_removes_a_partial_file_when_ffmpeg_fails(monkeypatch, tmp_path):
    async def fake_exec(*a, **k):
        (tmp_path / "a.webm").write_bytes(b"half")
        p = MagicMock()
        p.communicate = AsyncMock(return_value=(b"", b"bad"))
        p.returncode = 1
        return p
    monkeypatch.setattr(ca.shutil, "which", lambda n: "/usr/bin/ffmpeg")
    monkeypatch.setattr(ca.asyncio, "create_subprocess_exec", fake_exec)
    assert asyncio.run(to_webm(tmp_path / "a.mp4")) is None
    assert not (tmp_path / "a.webm").exists()


def test_clip_removes_the_staged_still_from_comfyui_input(tmp_path, monkeypatch):
    still = tmp_path / "s.png"
    still.write_bytes(b"x")
    inp = tmp_path / "input"
    inp.mkdir()
    g = MagicMock()
    g._checked_output_dir = lambda d: d
    g._ensure_comfyui_input_dir = AsyncMock(return_value=inp)
    g._submit_and_wait = AsyncMock(return_value={"output_file": str(tmp_path / "c.mp4")})
    monkeypatch.setattr(ca, "to_webm", AsyncMock(return_value=None))
    asyncio.run(CinematicArtist(g).clip(still, tmp_path))
    assert list(inp.iterdir()) == []
    # ...and also when the generation raises
    g._submit_and_wait = AsyncMock(side_effect=RuntimeError("comfy down"))
    with pytest.raises(RuntimeError):
        asyncio.run(CinematicArtist(g).clip(still, tmp_path))
    assert list(inp.iterdir()) == []


def test_relay_logs_reads_the_file_off_the_event_loop(tmp_path, monkeypatch):
    (tmp_path / "relay.log").write_text("a\nb\nc\n")
    state = AppState()
    state.relay_manager = SimpleNamespace(data_dir=tmp_path)
    app = FastAPI()
    app.include_router(system_routes.router)
    app.dependency_overrides[get_app_state] = lambda: state
    offloaded = []
    real = system_routes.asyncio.to_thread

    async def spy(fn, *a, **k):
        offloaded.append(fn)
        return await real(fn, *a, **k)
    monkeypatch.setattr(system_routes.asyncio, "to_thread", spy)
    body = TestClient(app).get("/api/relay/logs", params={"lines": 2}).json()
    assert offloaded and body["lines"] == ["b\n", "c\n"] and body["total"] == 3
