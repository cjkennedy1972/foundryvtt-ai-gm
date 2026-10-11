import json

import pytest

from playtest.scenario import ScenarioError, Step, load_scenario, new_gm_messages, parse_scenario


def _doc(*steps):
    return {"name": "s", "steps": list(steps)}


def test_parses_each_action_and_defaults(tmp_path):
    name, steps = parse_scenario(_doc(
        {"player": "A", "say": "hi", "roll": "1d20", "move": {"token": "t1", "x": 10, "y": 20.5}, "wait_for_gm": True},
        {"player": "B", "say": "ok"},
    ))

    assert name == "s"
    first, second = steps
    assert (first.say, first.roll, first.move.token, first.move.x, first.move.y) == ("hi", "1d20", "t1", 10.0, 20.5)
    assert first.wait_for_gm is True and first.timeout == 120.0
    assert second.wait_for_gm is False and second.move is None


def test_load_reads_a_file(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps(_doc({"player": "A", "say": "hi"})))

    name, steps = load_scenario(str(p))

    assert steps == [Step("A", "hi", None, None, False, 120.0)]


@pytest.mark.parametrize("step", [
    {"say": "hi"},
    {"player": "", "say": "hi"},
    {"player": "A"},
    {"player": "A", "say": "   "},
    {"player": "A", "roll": ""},
    {"player": "A", "say": "hi", "wait_for_gm": "yes"},
    {"player": "A", "say": "hi", "timeout": 0},
    {"player": "A", "say": "hi", "timeout": 9999},
    {"player": "A", "move": {"token": "", "x": 1, "y": 2}},
    {"player": "A", "move": {"token": "t", "x": "1", "y": 2}},
    {"player": "A", "move": {"token": "t", "x": True, "y": 2}},
])
def test_rejects_bad_steps(step):
    with pytest.raises(ScenarioError):
        parse_scenario(_doc(step))


def test_rejects_an_empty_or_unnamed_scenario():
    with pytest.raises(ScenarioError):
        parse_scenario({"name": "x", "steps": []})
    with pytest.raises(ScenarioError):
        parse_scenario({"steps": [{"player": "A", "say": "hi"}]})


def test_only_the_gm_counts_as_a_reply_and_only_new_messages():
    messages = [
        {"id": "old", "author": "ai-gm", "content": "before", "timestamp": 1},
        {"id": "mine", "author": "Player A", "content": "hello", "timestamp": 2},
        {"id": "new2", "author": "ai-gm", "content": "second", "timestamp": 4},
        {"id": "new1", "author": "ai-gm", "content": "first", "timestamp": 3},
    ]

    fresh = new_gm_messages(messages, seen={"old", "mine"}, gm_name="ai-gm")

    assert [m["id"] for m in fresh] == ["new1", "new2"]
