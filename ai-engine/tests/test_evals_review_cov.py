"""Behavioral coverage for evals/{metrics,judge,scenario,harness,replay,score}.py.

Everything runs against tmp dirs; nothing touches evals/metrics/history.jsonl,
evals/METRICS.md or evals/baselines/.
"""

import asyncio
import json
from types import SimpleNamespace

import pytest

from evals import judge as judge_mod
from evals import metrics as m
from evals import replay as replay_mod
from evals import scenario as sc
from evals import score as score_mod
from evals.harness import (
    MockDatabase, MockFoundryClient, MockNPCRegistry, MockStateTracker, RecordingLLM, ScriptedLLM,
)


def run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------
# metrics
# --------------------------------------------------------------------------

def _rec(rate, volume=None, **kw):
    r = {"contradiction_rate": rate, "recorded_at": kw.pop("at", "2026-01-01T00:00:00"), "backend": "scripted",
         "model": "m", "passed": 3, "scenarios": 3, "git_sha": "abc"}
    if volume is not None:
        r["tick_volume"] = volume
    r.update(kw)
    return r


def test_build_record_drops_none_and_maps_report_fields(monkeypatch):
    monkeypatch.setattr(m, "_git_sha", lambda: None)
    report = {"meta": {"backend": "live", "model": "q"},
              "summary": {"scenarios": 4, "passed": 3, "contradictions": 2, "contradiction_rate": 0.5,
                          "contradictions_by_source": {"canon": 2}}}
    rec = m.build_record(report, tick_volume=7, note="n")
    assert rec["backend"] == "live" and rec["model"] == "q" and rec["tick_volume"] == 7
    assert rec["by_source"] == {"canon": 2} and rec["contradiction_rate"] == 0.5
    assert "git_sha" not in rec  # None fields omitted
    assert set(rec) <= m._RECORD_KEYS
    bare = m.build_record({"summary": {}})
    assert "tick_volume" not in bare and "note" not in bare


def test_git_sha_survives_failure(monkeypatch):
    def boom(*a, **k):
        raise OSError("no git")
    monkeypatch.setattr(m.subprocess, "run", boom)
    assert m._git_sha() is None
    monkeypatch.setattr(m.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="  \n"))
    assert m._git_sha() is None
    monkeypatch.setattr(m.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="abc123\n"))
    assert m._git_sha() == "abc123"


def test_append_and_load_history_roundtrip_and_malformed(tmp_path):
    p = tmp_path / "sub" / "h.jsonl"
    assert m.load_history(p) == []
    m.append_record(_rec(0.1), p)
    m.append_record(_rec(0.2), p)
    with p.open("a") as fh:
        fh.write("\n")  # blank line tolerated
    assert [r["contradiction_rate"] for r in m.load_history(p)] == [0.1, 0.2]
    p.write_text('{"no_rate": 1}\n')
    with pytest.raises(ValueError, match=r"h.jsonl:1: malformed"):
        m.load_history(p)
    p.write_text("[1]\n")
    with pytest.raises(ValueError):
        m.load_history(p)


def test_tick_gate_rules():
    ok, why = m.check_tick_gate([_rec(0.1)])
    assert ok and "unproven" in why
    ok, why = m.check_tick_gate([_rec(0.1, 10), _rec(0.1, 100)])
    assert ok and "10, 100" in why
    ok, why = m.check_tick_gate([_rec(0.1, 10), _rec(0.3, 100, git_sha="zzz")])
    assert not ok and "rose with tick volume" in why and "0.3 at volume 100" in why and "zzz" in why
    # tolerance absorbs a small rise; equality at the same volume isn't "rising with volume"
    assert m.check_tick_gate([_rec(0.1, 10), _rec(0.15, 100)], tolerance=0.1)[0]
    assert m.check_tick_gate([_rec(0.5, 10), _rec(0.1, 10), _rec(0.1, 100)])[0] is True
    assert m.check_tick_gate([_rec(0.1, 10), _rec(0.3, 10)])[0] is True  # same volume: nothing "rose with" it
    # non-tick records never participate
    assert m.check_tick_gate([_rec(0.9), _rec(0.1, 10), _rec(0.1, 20)])[0]
    # the comparison floor is the BEST lower-volume rate, not the previous one
    assert not m.check_tick_gate([_rec(0.1, 10), _rec(0.0, 20), _rec(0.05, 30)])[0]


def test_render_metrics_md_latest_per_backend_and_gate_table():
    recs = [_rec(0.5, backend="scripted", at="2026-01-01"), _rec(0.25, backend="scripted", at="2026-02-01"),
            _rec(0.0, 10, backend="live"), _rec(0.4, 100, backend="live", by_source={"canon": 2, "event": 1})]
    md = m.render_metrics_md(recs)
    assert "| scripted | m | **0.2500** |" in md  # newest scripted record wins "Current"
    assert "Gate: **FAIL**" in md and "| 100 | 0.4000 |" in md
    assert "canon:2, event:1" in md
    empty = m.render_metrics_md([])
    assert "| — | — |" in empty and "## Current" not in empty and "unproven" in empty
    assert m._fmt_rate({"contradiction_rate": None}) == "n/a"


def test_metrics_cli_append_publish_gate_trend(tmp_path, monkeypatch, capsys):
    hist, md = tmp_path / "h.jsonl", tmp_path / "METRICS.md"
    monkeypatch.setattr(m, "METRICS_MD", md)
    monkeypatch.setattr(m, "_git_sha", lambda: "sha1")
    report = tmp_path / "r.json"
    report.write_text(json.dumps({"meta": {"backend": "scripted", "model": "x"},
                                  "summary": {"scenarios": 2, "passed": 2, "contradictions": 0,
                                              "contradiction_rate": 0.0}}))
    assert m.main(["append", str(report), "--tick-volume", "5", "--note", "hi", "--history", str(hist)]) == 0
    assert m.main(["append", str(report), "--tick-volume", "50", "--history", str(hist)]) == 0
    assert json.loads(hist.read_text().splitlines()[0])["note"] == "hi"
    assert m.main(["publish", "--history", str(hist)]) == 0
    assert "# GM quality metrics" in md.read_text()
    assert m.main(["gate", "--history", str(hist)]) == 0
    assert m.main(["trend", "--history", str(hist)]) == 0
    out = capsys.readouterr().out
    assert "tick gate PASS" in out and "tick_volume=50" in out and "sha=sha1" in out
    m.append_record(_rec(0.9, 500), hist)
    assert m.main(["gate", "--history", str(hist)]) == 1
    assert m.main(["gate", "--history", str(hist), "--tolerance", "1.0"]) == 0


# --------------------------------------------------------------------------
# judge
# --------------------------------------------------------------------------

def test_parse_verdict_tolerates_fences_and_rejects_non_bool():
    v = judge_mod.parse_verdict('```json\n{"contradiction": true, "reason": " dead man walks "}\n```')
    assert v.contradiction is True and v.reason == "dead man walks"
    assert judge_mod.parse_verdict('chatter {"contradiction": false} more').reason == ""
    assert judge_mod.parse_verdict('{"contradiction": false, "reason": 5}').reason == ""
    for bad in ("", None, "no json", '{"contradiction": "true"}', '{"contradiction": 1}', "{bad json}",
                "[1,2]", '["x"]'):
        assert judge_mod.parse_verdict(bad) is None


def test_judge_prompt_windows_prior_turns_and_marks_empty():
    p = judge_mod.build_judge_prompt([], [], "T")
    assert "(no canon established)" in p and "(this is the first turn)" in p and p.endswith("Return the JSON now.")
    prior = [f"turn{i}" for i in range(20)]
    p = judge_mod.build_judge_prompt(["Borin runs the inn"], prior, "NEW")
    assert "- Borin runs the inn" in p and "NEW TURN:\nNEW" in p
    assert "turn7" not in p and "1. turn8" in p and "12. turn19" in p  # last 12 only


def test_judge_turns_counts_errors_and_collects_contradictions():
    prompts = []
    replies = iter(['{"contradiction": true, "reason": "bridge is burned"}', "garbage", RuntimeError("net"),
                    42, '{"contradiction": false}'])

    async def ask(system, user):
        prompts.append((system, user))
        r = next(replies)
        if isinstance(r, Exception):
            raise r
        return r

    out = run(judge_mod.judge_turns(["t0", "t1", "t2", "t3", "t4"], ["fact"], ask))
    assert out["errors"] == 3  # unparseable, raised, non-string
    assert [v is None for v in out["verdicts"]] == [False, True, True, True, False]
    assert [str(c) for c in out["contradictions"]] == [str(out["contradictions"][0])]
    assert "bridge is burned" in str(out["contradictions"][0])
    assert "judge" in str(out["contradictions"][0]).lower()
    assert "t0" in prompts[1][1] and "PRIOR TURNS" in prompts[1][1]  # later turns see earlier ones
    assert run(judge_mod.judge_turns([], [], ask)) == {"verdicts": [], "errors": 0, "contradictions": []}


def test_make_ask_posts_deterministic_request_to_configured_endpoint(monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "llm_base_url", "http://judge.test/v1/")
    monkeypatch.setattr(settings, "llm_api_key", "k")
    monkeypatch.setattr(settings, "model", "default-model")
    seen = {}

    class Resp:
        def raise_for_status(self):
            seen["raised"] = True

        def json(self):
            return {"choices": [{"message": {"content": "VERDICT"}}]}

    class Client:
        def __init__(self, timeout=None):
            seen["timeout"] = timeout

        async def post(self, url, headers=None, json=None):
            seen.update(url=url, headers=headers, payload=json)
            return Resp()

        async def aclose(self):
            seen["closed"] = True

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", Client)
    ask = judge_mod.make_ask()
    assert ask.model == "default-model"
    assert run(ask("SYS", "USR")) == "VERDICT"
    assert seen["url"] == "http://judge.test/v1/chat/completions"
    assert seen["headers"] == {"Authorization": "Bearer k"}
    p = seen["payload"]
    assert p["temperature"] == 0.0 and p["model"] == "default-model"
    assert p["messages"] == [{"role": "system", "content": "SYS"}, {"role": "user", "content": "/no_think\nUSR"}]
    assert judge_mod.make_ask("other").model == "other"
    run(ask.close())
    assert seen["closed"]


# --------------------------------------------------------------------------
# scenario validation
# --------------------------------------------------------------------------

def _raw(**over):
    raw = {"id": "s1", "title": "T", "script": [{"event": "idle"}],
           "scripted_responses": [{"actions": []}]}
    raw.update(over)
    return raw


@pytest.mark.parametrize("over,msg", [
    ({"title": " "}, "missing 'title'"),
    ({"script": []}, "non-empty list"),
    ({"script": [{"event": "dance"}]}, "unknown event 'dance'"),
    ({"script": [{"event": "player_message"}]}, "requires 'message'"),
    ({"scripted_responses": "x"}, "must be a list"),
    ({"scripted_responses": [{"nope": 1}]}, "missing 'actions'"),
    ({"script": [{"event": "idle"}, {"event": "idle"}]}, "2 LLM calls"),
    ({"expect": {"bogus": 1}}, "unknown expect keys"),
    ({"canon_facts": [{"fact": ""}]}, "missing 'fact'"),
    ({"canon_facts": [{"fact": "x", "contradiction_patterns": ["("]}]}, "bad contradiction pattern"),
])
def test_scenario_validation_rejects(over, msg):
    with pytest.raises(sc.ScenarioError, match=msg):
        sc._validate("s1", _raw(**over))


def test_scenario_validation_accepts_dropped_beats_and_load_roundtrip(tmp_path):
    raw = _raw(script=[{"event": "idle"}, {"event": "idle", "dropped": True}], tags=["a"],
               canon_facts=[{"fact": "F", "contradiction_patterns": ["x+"]}], expect={"must_call": ["chat_message"]})
    p = tmp_path / "s1.json"
    p.write_text(json.dumps(raw))
    s = sc.load_scenario(p)
    assert s.id == "s1" and s.tags == ["a"] and s.canon_facts[0].contradiction_patterns == ["x+"]
    assert s.baseline_path == sc.BASELINES_DIR / "s1.events.json" and s.path == p
    del raw["id"]
    (tmp_path / "stem_id.json").write_text(json.dumps(raw))
    assert sc.load_scenario(tmp_path / "stem_id.json").id == "stem_id"


def test_load_corpus_filters_and_errors(tmp_path):
    with pytest.raises(sc.ScenarioError, match="no scenario files"):
        sc.load_corpus(tmp_path)
    for name in ("b", "a"):
        (tmp_path / f"{name}.json").write_text(json.dumps(_raw(id=name)))
    assert [s.id for s in sc.load_corpus(tmp_path)] == ["a", "b"]
    assert [s.id for s in sc.load_corpus(tmp_path, only=["b"])] == ["b"]
    with pytest.raises(sc.ScenarioError, match="unknown scenario id"):
        sc.load_corpus(tmp_path, only=["a", "zzz"])


def test_shipped_corpus_validates():
    assert len(sc.load_corpus()) >= 30


# --------------------------------------------------------------------------
# harness doubles
# --------------------------------------------------------------------------

def test_mock_foundry_records_calls_and_serves_fixtures():
    async def go():
        f = MockFoundryClient(scenes=["A", "B"], scene_name="B", world_title="Krynn")
        assert (await f.execute_js("game.world.title"))["result"] == {"title": "Krynn", "id": "krynn"}
        assert (await f.execute_js("canvas?.scene"))["result"]["name"] == "B"
        assert (await f.execute_js("game.scenes"))["result"] == [{"name": "A", "active": False},
                                                                  {"name": "B", "active": True}]
        assert (await f.execute_js("game.actors"))["result"][0]["name"] == "Aria"
        assert (await f.execute_js("game.paused"))["result"] is None
        assert (await f.execute_js("whatever"))["result"] is None
        assert (await f.start_encounter(tokens=["t1"]))["combatants"][0]["initiative"] == 18
        assert f.calls_of("start_combat") == [{"method": "start_combat", "token_ids": ["t1"]}]
        assert (await f.place_token(actor_name="Orc", x=1, y=2))["tokenId"] == "tok_Orc"
        assert (await f.roll("1d20"))["total"] == 15
        assert (await f.scan_world())["modules"][0]["id"] == "midi-qol"
        assert await f.wait_for_hook("x") is True
        got = []

        async def handler(data):
            got.append(data)
        f.subscribe("chan", handler)
        await f.emit("chan", {"a": 1})
        await f.emit("other", {"b": 2})
        assert got == [{"a": 1}]
        for coro in (f.chat_message("hi", "Sage"), f.get_actors(), f.get_scene_tokens(), f.list_scene_names(),
                     f.set_active_scene("A"), f.create_entity("Scene", {"name": "N"}), f.roll_initiative(),
                     f.configure_scene({"x": 1}), f.request_long_rest("Actor.1"),
                     f.decrease_attribute("hp", 3, "u"), f.increase_attribute("hp", 2, "u"),
                     f.subscribe_to_channel("c")):
            await coro
        methods = [c["method"] for c in f.calls]
        assert {"chat_message", "decrease_attribute", "increase_attribute", "request_long_rest"} <= set(methods)
        assert f._get_speaker_name() == "Sage"
        f.reset_message_id()
    run(go())


def test_scripted_llm_repeats_last_response_and_streams_in_chunks():
    async def go():
        llm = ScriptedLLM([{"actions": [1]}, {"actions": [2]}])
        assert (await llm.generate("a"))["actions"] == [1]
        assert (await llm.generate("b"))["actions"] == [2]
        assert (await llm.generate("c"))["actions"] == [2]  # last repeats
        chunks = [c async for c in llm.generate_stream("d")]
        assert len(chunks) > 1 and json.loads("".join(chunks)) == {"actions": [2]}
        await llm.remember_combat("fight")
        assert llm.calls[-1] == "[remember_combat] fight"
        assert llm.system_prompt == "You are a mock GM."
        llm.set_system_prompt("x")
        llm.invalidate_system_prompt()
        llm.set_active_modules(["m"])
        assert llm._system_prompt_cache is None and llm._active_modules == ["m"]
        for setter in (llm.set_dynamic_npc_context, llm.set_dynamic_world_context,
                       llm.set_dynamic_house_rules_context, llm.set_dynamic_canon_context):
            setter("x")
        llm._trim_history()
        await llm.close()
        assert llm.conversation_history == []
    run(go())


def test_recording_llm_records_exchanges_and_delegates():
    async def go():
        inner = ScriptedLLM([{"actions": []}])
        rec = RecordingLLM(inner)
        out = await rec.generate("hi", game_state_summary="GS", extra_context="EC")
        assert out == {"actions": []}
        c = rec.calls[0]
        assert (c["user_message"], c["game_state_summary"], c["extra_context"], c["model"]) == (
            "hi", "GS", "EC", "mock-model")
        toks = [t async for t in rec.generate_stream("yo")]
        s = rec.calls[1]
        assert s["tokens"] == len(toks) and s["first_token_s"] is not None and "total_latency_s" in s
        empty = RecordingLLM(SimpleNamespace(generate_stream=lambda *a, **k: _empty()))
        assert [t async for t in empty.generate_stream("x")] == []
        assert empty.calls[0]["first_token_s"] is None and empty.calls[0]["model"] == "unknown"
        assert rec.system_prompt == "You are a mock GM."  # __getattr__ delegation
        await rec.close()
        await RecordingLLM(object()).close()  # inner without close()
    run(go())


async def _empty():
    return
    yield


def test_mock_database_usage_accounting_and_state():
    async def go():
        db = MockDatabase()
        assert await db.get_active_session() is None and await db.get_active_session_info() is None
        await db.create_session("s", "C")
        assert await db.get_active_session_info() == {"session_id": "s", "campaign": "C"}
        await db.record_llm_usage("s", "C", "10", 5, "m")
        await db.record_llm_usage("other", "C", 100, 100, "m")
        assert await db.get_llm_usage_total(session_id="s") == 15
        await db.save_state("k", {"a": 1})
        assert await db.load_state("k") == {"a": 1} and await db.load_state("nope") is None
        await db.record_event("s", "C", "boom")
        await db.save_conversation("s", "C", "user", "x")
        await db.record_typed_event("s", "C", "t", {})
        assert [c["role"] for c in db._conversations] == ["event", "user"]
    run(go())


def test_mock_state_and_npc_registry():
    async def go():
        st = MockStateTracker(mode="combat", scene="Hall")
        assert st.get_snapshot() == "mode=combat scene=Hall" and st.get_encounter_context() == ""
        await st.set_mode("exploration")
        await st.set_campaign("x")
        await st.save()
        assert st.state.mode == "exploration"
        reg = MockNPCRegistry({"Wenna": {"description": "chandler"}})
        assert reg.get_npc_by_name("Wenna").description == "chandler" and reg.get_npc_by_name("Goblin") is None
        assert len(reg.list_npcs()) == 1 and reg.register_npc(name="x") is None
        assert MockNPCRegistry().get_npc_by_name("Goblin").combat_style.startswith("Hits and runs")
    run(go())


# --------------------------------------------------------------------------
# score
# --------------------------------------------------------------------------

def test_score_checks_drift_and_reports(tmp_path):
    scn = SimpleNamespace(id="x", canon_facts=[], expect={
        "must_call": ["chat_message", "roll:formula=1d20"], "must_not_call": ["start_combat"],
        "must_mention": [["dragon", "wyrm"], "tower"], "must_not_mention": ["Elf"],
        "min_llm_calls": 2, "max_llm_calls": 1})
    calls = [{"method": "chat_message", "text": "A wyrm circles"}, {"method": "roll", "formula": "1d8"},
             {"method": "start_combat"}, {"method": "get_actors"}, {"method": "whisper", "text": "elf"}]
    checks = {c.name: c for c in score_mod.check_expectations(scn, calls, 1)}
    assert checks["must_call:chat_message"].ok and not checks["must_call:roll:formula=1d20"].ok
    assert not checks["must_not_call:start_combat"].ok and "1x" in checks["must_not_call:start_combat"].detail
    assert checks["must_mention:dragon|wyrm"].ok and not checks["must_mention:tower"].ok
    assert not checks["must_not_mention:Elf"].ok  # case-insensitive, whisper counts as speech
    assert not checks["min_llm_calls"].ok and checks["min_llm_calls"].detail == "1 < 2"
    assert checks["max_llm_calls"].ok
    assert score_mod.action_sequence(calls) == ["chat_message", "roll", "start_combat", "whisper"]

    assert score_mod.compute_drift(None, calls) == (None, None)
    assert score_mod.compute_drift([{"method": "roll"}], [{"method": "roll"}]) == (1.0, 0.0)
    ratio, overlap = score_mod.compute_drift(
        [{"method": "chat_message", "text": "dragon attacks tower"}],
        [{"method": "chat_message", "text": "dragon attacks village"}])
    assert ratio == 1.0 and overlap == 0.5

    bad = score_mod.score_run(scn, calls, [], None, "scripted", error="boom")
    assert not bad.passed and bad.checks == []
    judged = score_mod.score_run(
        SimpleNamespace(id="y", canon_facts=[], expect={}), [{"method": "chat_message", "text": "hi"}], [{}], None,
        "live", judge_result={"verdicts": [1, 2], "errors": 1, "contradictions": ["[judge] bad"]})
    assert judged.judged_turns == 2 and judged.judge_errors == 1 and "[judge] bad" in judged.contradictions
    good = score_mod.score_run(SimpleNamespace(id="g", canon_facts=[], expect={}), [], [], None, "scripted")
    summ = score_mod.corpus_summary([bad, judged, good])
    assert summ["scenarios"] == 3 and summ["passed"] == 1 and summ["contradictions_by_source"] == {"judge": 1}
    assert summ["judge_errors"] == 1
    md = score_mod.report_markdown([bad, judged, good], {"backend": "scripted", "generated_at": "t"})
    assert "| x | ERROR |" in md and "| y | FAIL |" in md and "| g | PASS |" in md and "judge coverage" in md
    paths = score_mod.write_reports([good], {"backend": "scripted"}, tmp_path / "out")
    assert json.loads(paths["json"].read_text())["summary"]["passed"] == 1
    assert score_mod.corpus_summary([])["contradiction_rate"] == 0.0


# --------------------------------------------------------------------------
# replay CLI
# --------------------------------------------------------------------------

def _corpus(tmp_path, **extra):
    d = tmp_path / "corpus"
    d.mkdir()
    good = {"id": "good", "title": "ok", "setup": {"scene": "Hall"},
            "script": [{"event": "player_message", "speaker": "Aria", "message": "I look."}],
            "scripted_responses": [{"actions": [{"type": "narrate", "text": "A dusty hall."}]}],
            "expect": {"must_call": ["chat_message"], "must_mention": ["dusty"]}}
    (d / "good.json").write_text(json.dumps(good))
    for name, raw in extra.items():
        (d / f"{name}.json").write_text(json.dumps(raw))
    return d


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "BASELINES_DIR", tmp_path / "baselines")
    monkeypatch.setattr(m, "HISTORY_PATH", tmp_path / "hist.jsonl")
    monkeypatch.delenv(replay_mod.LIVE_CONFIRM_ENV, raising=False)
    monkeypatch.delenv(replay_mod.LIVE_BUDGET_ENV, raising=False)
    monkeypatch.setattr(m, "_git_sha", lambda: "sha")
    return tmp_path


def test_replay_scripted_freeze_then_compare_and_record(isolated, capsys):
    tmp = isolated
    corpus = _corpus(tmp)
    out = tmp / "out"
    assert replay_mod.main(["--corpus", str(corpus), "--out", str(out), "--freeze", "--record",
                            "--tick-volume", "3"]) == 0
    baseline = tmp / "baselines" / "good.events.json"
    assert baseline.exists()
    frozen = json.loads(baseline.read_text())
    assert any(c["method"] == "chat_message" and "dusty hall" in c["text"] for c in frozen["foundry_calls"])
    hist = [json.loads(line) for line in (tmp / "hist.jsonl").read_text().splitlines()]
    assert hist[0]["tick_volume"] == 3 and hist[0]["scenarios"] == 1 and hist[0]["passed"] == 1
    assert hist[0]["model"] == "mock-model"
    text = capsys.readouterr().out
    assert "1/1 passed" in text and "recorded contradiction rate" in text
    # second run compares against the baseline
    out2 = tmp / "out2"
    assert replay_mod.main(["--corpus", str(corpus), "--out", str(out2)]) == 0
    rep = json.loads((out2 / "report.json").read_text())
    assert rep["scenarios"][0]["drift"] == 1.0 and rep["summary"]["mean_drift"] == 1.0
    assert "mean drift vs baseline: 1.0" in capsys.readouterr().out
    assert replay_mod.load_baseline(sc.load_corpus(corpus)[0]) == frozen["foundry_calls"]


def test_replay_scenario_error_stops_unless_keep_going(isolated, capsys):
    tmp = isolated
    broken = {"title": "no responses", "script": [{"event": "idle"}]}  # scripted backend needs responses
    corpus = _corpus(tmp, a_broken=broken)  # sorts before "good"
    assert replay_mod.main(["--corpus", str(corpus), "--out", str(tmp / "o1")]) == 1
    rep = json.loads((tmp / "o1" / "report.json").read_text())
    assert [s["id"] for s in rep["scenarios"]] == ["a_broken"]  # stopped at the first failure
    assert "scripted backend requires 'scripted_responses'" in rep["scenarios"][0]["error"]
    assert replay_mod.main(["--corpus", str(corpus), "--out", str(tmp / "o2"), "--keep-going"]) == 1
    rep = json.loads((tmp / "o2" / "report.json").read_text())
    assert [s["passed"] for s in rep["scenarios"]] == [False, True]
    assert "ERROR" in capsys.readouterr().out


def test_replay_scenario_subset_and_argument_errors(isolated, capsys):
    tmp = isolated
    corpus = _corpus(tmp, other={"title": "o", "script": [{"event": "idle"}],
                                 "scripted_responses": [{"actions": [{"type": "narrate", "text": "x"}]}]})
    assert replay_mod.main(["--corpus", str(corpus), "--out", str(tmp / "o"), "--scenario", "good"]) == 0
    assert [p.name for p in (tmp / "o").glob("*.events.json")] == ["good.events.json"]
    assert replay_mod.main(["--tick-volume", "3"]) == 2
    assert "--tick-volume only makes sense with --record" in capsys.readouterr().err


def test_replay_judge_flow_counts_judge_contradictions(isolated, monkeypatch, capsys):
    tmp = isolated
    corpus = _corpus(tmp)
    monkeypatch.setenv(replay_mod.LIVE_CONFIRM_ENV, "true")
    monkeypatch.setenv(replay_mod.LIVE_BUDGET_ENV, "1000000")
    closed = []

    async def ask(system, user):
        assert "dusty hall" in user
        return '{"contradiction": true, "reason": "hall was burned down"}'

    async def aclose():
        closed.append(True)

    ask.close, ask.model = aclose, "judge-m"
    monkeypatch.setattr(judge_mod, "make_ask", lambda: ask)
    rc = replay_mod.main(["--corpus", str(corpus), "--out", str(tmp / "o"), "--judge"])
    assert rc == 1 and closed == [True]
    events = json.loads((tmp / "o" / "good.events.json").read_text())
    assert events["judge_calls"][0]["verdict"] == {"contradiction": True, "reason": "hall was burned down"}
    rep = json.loads((tmp / "o" / "report.json").read_text())
    assert rep["summary"]["contradictions_by_source"] == {"judge": 1} and rep["meta"]["judged"] is True
    out = capsys.readouterr().out
    assert "contradiction sources: judge: 1" in out and "judge: 1/1 turns audited, 0 errors" in out


def test_live_budget_env_parsing_and_display(monkeypatch):
    monkeypatch.delenv(replay_mod.LIVE_BUDGET_ENV, raising=False)
    assert replay_mod._live_budget() == replay_mod.DEFAULT_LIVE_TOKEN_BUDGET
    monkeypatch.setenv(replay_mod.LIVE_BUDGET_ENV, "0")
    assert replay_mod._live_budget() == 0  # "0" is a non-empty string: explicit opt-out, not default
    monkeypatch.setenv(replay_mod.LIVE_BUDGET_ENV, "-5")
    with pytest.raises(ValueError, match=">= 0"):
        replay_mod._live_budget()
    assert "unparseable" in replay_mod._budget_display()
    monkeypatch.setenv(replay_mod.LIVE_BUDGET_ENV, "abc")
    with pytest.raises(ValueError, match="integer"):
        replay_mod._live_budget()
    assert "unparseable" in replay_mod._budget_display()
    monkeypatch.setenv(replay_mod.LIVE_BUDGET_ENV, "2500")
    assert replay_mod._budget_display() == "2,500 tokens"
    monkeypatch.setenv(replay_mod.LIVE_CONFIRM_ENV, "TRUE")
    assert replay_mod._live_gate_error(SimpleNamespace(backend="live", judge=False)) is None
    monkeypatch.setenv(replay_mod.LIVE_BUDGET_ENV, "-1")
    assert ">= 0" in replay_mod._live_gate_error(SimpleNamespace(backend="live", judge=False))


def test_usage_tracker_shares_one_session_and_enforces_cap(monkeypatch):
    monkeypatch.setenv(replay_mod.LIVE_BUDGET_ENV, "50")
    tracker, sid = replay_mod._make_usage_tracker()
    assert sid.startswith("eval-run-") and tracker.budget == 50
    with pytest.raises(Exception, match="token budget exhausted"):
        run(tracker.before_call(sid, "c", 51))
    run(tracker.before_call(sid, "c", 50))


def test_live_llm_is_recording_wrapper_and_prime_pushes_context(monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "llm_base_url", "http://x.test/v1")
    scn = SimpleNamespace(id="s", setup={
        "llm_context": {"canon": "C", "world": "W", "npcs": "N", "house_rules": "H"},
        "history": [{"role": "user", "content": "old"}]})
    live = replay_mod._live_llm(scn)
    assert isinstance(live, RecordingLLM)
    replay_mod._prime_llm(live, scn)
    inner = live._inner
    assert (inner._dynamic_canon_context, inner._dynamic_world_context, inner._dynamic_npc_context,
            inner._dynamic_house_rules_context) == ("C", "W", "N", "H")
    assert inner._conversation_history == [{"role": "user", "content": "old"}]
    assert inner._conversation_history[0] is not scn.setup["history"][0]  # copied, not shared
    run(live.close())
