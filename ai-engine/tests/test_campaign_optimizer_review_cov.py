"""CampaignOptimizer, AutoOptimizer, CampaignStore and ModuleDiscovery behaviour."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from campaign.auto_optimizer import AutoOptimizer
from campaign.campaign_optimizer import CampaignOptimizer
from campaign.module_discovery import ModuleDiscovery, ModuleInfo, ModuleSynergyMapper


def run(coro):
    return asyncio.run(coro)


def mod(id_="fx", enabled=True, uses=("rain", "fog", "extra")):
    return ModuleInfo(id_, id_.upper(), "1.0", enabled, "d", ["cap"], list(uses))


# ── CampaignOptimizer ────────────────────────────────────────────────────

def _opt(analysis, discovery, synergies, enh=None):
    o = CampaignOptimizer(llm_manager=MagicMock())
    o.analyzer.analyze_campaign = AsyncMock(return_value=analysis)
    o.module_discovery.discover_modules = AsyncMock(return_value=discovery)
    o.synergy_mapper.map_synergies = AsyncMock(return_value=synergies)
    o.story_enricher.generate_enhancements = AsyncMock(return_value=enh or {"hooks": []})
    return o


def test_optimize_campaign_compiles_counts_and_module_list():
    m = mod()
    analysis = {"scenes": [{"name": "a"}, {"name": "b"}], "encounters": [1], "npcs": [1, 2, 3], "narrative_arcs": [1],
                "pacing": {"avg": 5}, "immersion_gaps": ["sound"]}
    syn = {"scene_enhancements": [{"scene": "a"}], "encounter_enhancements": [],
           "npc_enhancements": [{}, {}], "immersion_gap_fills": [{}]}
    o = _opt(analysis, {"modules": [m], "total_modules": 4, "enabled_modules": 3}, syn)
    out = run(o.optimize_campaign({"name": "Oak"}, "client"))
    assert out["status"] == "complete" and out["campaign_name"] == "Oak"
    assert out["analysis"] == {"scene_count": 2, "encounter_count": 1, "npc_count": 3, "narrative_arcs": 1,
                               "drama_analysis": {"avg": 5}, "immersion_gaps_identified": 1}
    assert out["modules"]["total_installed"] == 4 and out["modules"]["enabled"] == 3
    assert out["modules"]["modules_list"] == [{"id": "fx", "name": "FX", "enabled": True,
                                               "capabilities": ["cap"], "narrative_uses": ["rain", "fog", "extra"]}]
    assert out["synergies"]["scene_synergies"] == 1 and out["synergies"]["npc_synergies"] == 2
    assert out["synergies"]["details"] is syn
    o.module_discovery.discover_modules.assert_awaited_once()
    assert o.module_discovery.discover_modules.await_args.args[0] == "client"


def test_discovery_error_degrades_to_no_modules():
    o = _opt({}, {"error": "boom", "modules": []}, {})
    out = run(o.optimize_campaign({}, None))
    assert out["status"] == "complete"
    assert out["campaign_name"] == "Unknown"
    assert out["modules"]["modules_list"] == [] and out["modules"]["total_installed"] == 0
    assert o.synergy_mapper.map_synergies.await_args.args[1] == []


def test_pipeline_exception_returns_error_status():
    o = _opt({}, {}, {})
    o.analyzer.analyze_campaign = AsyncMock(side_effect=RuntimeError("bad analysis"))
    assert run(o.optimize_campaign({}, None)) == {"status": "error", "error": "bad analysis"}


def test_recommendations_cover_every_rule():
    o = CampaignOptimizer()
    scenes = [SimpleNamespace(name="Hot", drama_level=9),         # object, unsupported
              {"name": "Covered", "drama_level": 9},              # dict, has synergy
              {"name": "Calm", "drama_level": 7},                 # not > 7
              {"name": "Hot2", "drama_level": 8}]
    analysis = {"immersion_gaps": ["a", "b", "c", "d"], "scenes": scenes,
                "npcs": list(range(6)), "decision_points": list(range(4))}
    recs = o._generate_recommendations(analysis, [], {"scene_enhancements": [{"scene": "Covered"}]})
    by = {r["category"]: r for r in recs}
    assert [r["priority"] for r in recs] == ["high", "medium", "medium", "low"]
    assert by["Immersion Gaps"]["count"] == 4 and by["Immersion Gaps"]["details"] == ["a", "b", "c"]
    assert by["Drama Enhancement"]["details"] == ["Hot", "Hot2"]
    assert by["NPC Management"]["count"] == 6
    assert by["Player Agency"]["details"] == ["4 key decision points identified"]


def test_recommendations_thresholds_are_exclusive():
    o = CampaignOptimizer()
    analysis = {"npcs": list(range(5)), "decision_points": list(range(3)), "scenes": [{"name": "x", "drama_level": 7}]}
    assert o._generate_recommendations(analysis, [], {}) == []


# ── AutoOptimizer ────────────────────────────────────────────────────────

def _auto(result):
    a = AutoOptimizer(llm_manager=MagicMock(), foundry_client="fc")
    a.optimizer.optimize_campaign = AsyncMock(return_value=result)
    return a


FAIL = {"status": "error", "error": "nope"}


def test_new_scene_builds_minimal_campaign_and_failure_passes_error():
    a = _auto(FAIL)
    assert run(a.optimize_new_scene({"name": "S"}, {"name": "C"})) == {"error": "nope"}
    camp, client = a.optimizer.optimize_campaign.await_args.args
    assert camp == {"name": "C", "scenes": [{"name": "S"}], "encounters": [], "npcs": []}
    assert client == "fc"


def test_new_scene_exception_is_reported():
    a = _auto(FAIL)
    a.optimizer.optimize_campaign = AsyncMock(side_effect=ValueError("x"))
    assert run(a.optimize_new_scene({}, {})) == {"error": "x"}
    assert run(a.optimize_new_encounter({}, {})) == {"error": "x"}
    assert run(a.optimize_new_quest({}, {})) == {"error": "x"}


def test_encounter_and_quest_failure_paths():
    a = _auto(FAIL)
    assert run(a.optimize_new_encounter({"name": "E"}, {"name": "C"})) == {"error": "nope"}
    assert a.optimizer.optimize_campaign.await_args.args[0]["encounters"] == [{"name": "E"}]
    assert run(a.optimize_new_quest({"title": "Q"}, {"name": "C"})) == {"error": "nope"}
    assert a.optimizer.optimize_campaign.await_args.args[0]["quests"] == [{"title": "Q"}]


def test_quest_success_shape():
    a = _auto({"status": "complete", "modules": {"m": 1}, "enhancements": {"h": 1}, "recommendations": ["r"]})
    assert run(a.optimize_new_quest({"title": "Q"}, {})) == {
        "quest_title": "Q", "modules": {"m": 1}, "narrative_enhancements": {"h": 1}, "recommendations": ["r"]}


def test_encounter_success_reads_details_list():
    a = _auto({"status": "complete", "synergies": {"details": {"encounter_enhancements": ["x"]}}})
    out = run(a.optimize_new_encounter({"name": "E"}, {}))
    assert out["synergies"] == ["x"] and out["encounter_name"] == "E"


def test_batch_dispatches_by_type_and_flags_unknown():
    a = _auto(FAIL)
    a.optimize_new_scene = AsyncMock(return_value="s")
    a.optimize_new_encounter = AsyncMock(return_value="e")
    a.optimize_new_quest = AsyncMock(return_value="q")
    for t, exp in (("scene", "s"), ("encounter", "e"), ("quest", "q")):
        assert run(a.optimize_element_batch([{"n": 1}, {"n": 2}], t, {})) == [exp, exp]
    assert run(a.optimize_element_batch([{}], "item", {})) == [{"error": "Unknown element type: item"}]


# ── ModuleDiscovery ──────────────────────────────────────────────────────

def test_fetch_modules_filters_and_keys_by_id():
    fc = MagicMock()
    fc.execute_js = AsyncMock(return_value={"result": [{"id": "a", "name": "A"}, {"name": "noid"}, "junk"]})
    got = run(ModuleDiscovery()._fetch_modules(fc))
    assert got == {"a": {"id": "a", "name": "A"}}
    assert "return" in fc.execute_js.await_args.args[0]


@pytest.mark.parametrize("res", [{"result": "x"}, None, {"result": None}])
def test_fetch_modules_non_list_is_empty(res):
    fc = MagicMock()
    fc.execute_js = AsyncMock(return_value=res)
    assert run(ModuleDiscovery()._fetch_modules(fc)) == {}


def test_fetch_modules_exception_is_empty():
    fc = MagicMock()
    fc.execute_js = AsyncMock(side_effect=RuntimeError("ws"))
    assert run(ModuleDiscovery()._fetch_modules(fc)) == {}


def _discovery_client(mods):
    fc = MagicMock()
    fc.execute_js = AsyncMock(return_value={"result": mods})
    return fc


def test_discover_without_llm_counts_enabled():
    fc = _discovery_client([{"id": "a", "name": "A", "enabled": True}, {"id": "b", "enabled": False}])
    out = run(ModuleDiscovery().discover_modules(fc))
    assert out["total_modules"] == 2 and out["enabled_modules"] == 1
    a, b = out["modules"]
    assert (a.id, a.name, a.version, a.capabilities) == ("a", "A", "unknown", [])
    assert b.name == "b"


def test_discover_empty_and_failing():
    assert run(ModuleDiscovery().discover_modules(_discovery_client([]))) == {
        "total_modules": 0, "enabled_modules": 0, "modules": []}
    d = ModuleDiscovery()
    d._fetch_modules = AsyncMock(side_effect=RuntimeError("x"))
    assert run(d.discover_modules(None)) == {"error": "x", "modules": []}


def test_discover_skips_non_dict_module_data():
    d = ModuleDiscovery()
    d._fetch_modules = AsyncMock(return_value={"a": "str", "b": {"name": "B"}})
    out = run(d.discover_modules(None))
    assert [m.id for m in out["modules"]] == ["b"]


def test_enhance_with_llm_parses_json_and_prompt_has_module():
    llm = MagicMock()
    llm.generate_text = AsyncMock(return_value='{"capabilities": ["fog"], "narrative_use_cases": ["mood"]}')
    info = run(ModuleDiscovery()._enhance_with_llm("fx", {"name": "FX", "enabled": True, "version": "2"}, llm))
    assert (info.capabilities, info.narrative_use_cases, info.version) == (["fog"], ["mood"], "2")
    assert "Module ID: fx" in llm.generate_text.await_args.args[0]


def test_enhance_with_llm_unparseable_keeps_snippet():
    llm = MagicMock()
    llm.generate_text = AsyncMock(return_value="x" * 300)
    info = run(ModuleDiscovery()._enhance_with_llm("fx", {}, llm))
    assert info.capabilities == [] and info.narrative_use_cases == ["x" * 100]


def test_enhance_with_llm_failure_falls_back():
    llm = MagicMock()
    llm.generate_text = AsyncMock(side_effect=RuntimeError("down"))
    info = run(ModuleDiscovery()._enhance_with_llm("fx", {"name": "FX"}, llm))
    assert info.name == "FX" and info.capabilities == [] and info.enabled is False
    assert run(ModuleDiscovery()._enhance_with_llm("fx", "notdict", llm)) is None


# ── ModuleSynergyMapper ──────────────────────────────────────────────────

def _llm(*responses):
    llm = MagicMock()
    llm.generate_text = AsyncMock(side_effect=list(responses))
    return llm


GOOD = '{"synergies": [{"module": "fx", "enhancement": "e"}]}'


def test_map_synergies_only_enabled_modules_reach_prompts():
    llm = MagicMock()
    llm.generate_text = AsyncMock(return_value=GOOD)
    analysis = {"scenes": [{"name": "S"}], "encounters": [{"name": "E"}], "npcs": [SimpleNamespace(
        name="N", description="d", drama_level=3)], "narrative_arcs": [{"title": "A", "progression": [1, 2]}],
        "immersion_gaps": []}
    out = run(ModuleSynergyMapper().map_synergies(analysis, [mod("on"), mod("off", enabled=False)], llm))
    assert set(out) == {"scene_enhancements", "encounter_enhancements", "npc_enhancements",
                        "narrative_arc_enhancements", "immersion_gap_fills"}
    assert all(len(out[k]) == 1 for k in list(out)[:4]) and out["immersion_gap_fills"] == []
    prompts = [c.args[0] for c in llm.generate_text.await_args_list]
    assert len(prompts) == 4
    assert all("- ON: rain, fog" in p and "OFF" not in p for p in prompts)
    assert "Story Stages: 2" in prompts[3]


def test_synergies_skipped_without_llm():
    out = run(ModuleSynergyMapper().map_synergies(
        {"scenes": [{"name": "S"}], "immersion_gaps": ["g"]}, [mod()]))
    assert all(v == [] for v in out.values())


@pytest.mark.parametrize("meth,item", [
    ("_map_scene_synergies", {"name": "S"}), ("_map_encounter_synergies", {"name": "E"}),
    ("_map_npc_synergies", {"name": "N"}), ("_map_narrative_synergies", {"title": "A"})])
def test_synergy_mappers_drop_unparseable_empty_and_failed(meth, item):
    m = ModuleSynergyMapper()
    fn = getattr(m, meth)
    assert run(fn([item], [mod()], _llm("no json here"))) == []
    assert run(fn([item], [mod()], _llm('{"synergies": []}'))) == []
    llm = MagicMock()
    llm.generate_text = AsyncMock(side_effect=RuntimeError("x"))
    assert run(fn([item], [mod()], llm)) == []
    # one failure does not lose the next item
    llm = _llm(RuntimeError("x"), GOOD)
    assert len(run(fn([item, item], [mod()], llm))) == 1


def test_immersion_gaps_paths():
    m = ModuleSynergyMapper()
    llm = _llm('{"fills": [{"gap": "g", "module": "fx"}]}')
    assert run(m._map_immersion_gaps(["g"], [mod()], llm)) == [{"gap": "g", "module": "fx"}]
    assert "- g" in llm.generate_text.await_args.args[0]
    assert run(m._map_immersion_gaps(["g"], [mod()], _llm("nope"))) == []
    assert run(m._map_immersion_gaps([], [mod()], _llm(GOOD))) == []
    bad = MagicMock()
    bad.generate_text = AsyncMock(side_effect=RuntimeError("x"))
    assert run(m._map_immersion_gaps(["g"], [mod()], bad)) == []
