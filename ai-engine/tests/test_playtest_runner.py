import asyncio

from playtest.runner import run_scenario
from playtest.scenario import Move, Step


class FakeBot:
    """Records what it was asked to do. `gm_replies` is the chat the GM posts after each say."""

    def __init__(self, label, chat, reply_after=None):
        self.label = label
        self.chat = chat  # shared list of message dicts, like the Foundry chat log
        self.reply_after = reply_after  # text the GM answers with, or None for silence
        self.calls = []
        self.fail_move = False

    async def messages(self):
        return list(self.chat)

    async def say(self, text):
        self.calls.append(("say", text))
        self.chat.append({"id": f"msg{len(self.chat)}", "author": self.label, "content": text, "timestamp": len(self.chat)})
        if self.reply_after is not None:
            self.chat.append({"id": f"gm{len(self.chat)}", "author": "ai-gm", "content": self.reply_after,
                              "timestamp": len(self.chat)})
        return {}

    async def roll(self, formula):
        self.calls.append(("roll", formula))
        return {"total": 14}

    async def move_token(self, token, x, y):
        self.calls.append(("move", token, x, y))
        if self.fail_move:
            raise RuntimeError("move refused: not owner")
        return {"ok": True}


def run(steps, bots, **kw):
    return asyncio.run(run_scenario("demo", steps, bots, "ai-gm", settle=0.01, poll=0.001, **kw))


def test_a_step_collects_the_gm_reply_and_its_latency():
    chat = []
    bot = FakeBot("Player A", chat, reply_after="The door creaks open.")

    report = run([Step("Player A", "knock", None, None, True, 5.0)], {"Player A": bot})

    step = report["steps"][0]
    assert step["actions"] == [{"say": "knock"}]
    assert [r["content"] for r in step["replies"]] == ["The door creaks open."]
    assert step["timed_out"] is False and step["latency_s"] is not None
    assert report["failed"] == 0


def test_silence_from_the_gm_times_out_and_counts_as_a_failure():
    chat = []
    bot = FakeBot("Player A", chat, reply_after=None)

    report = run([Step("Player A", "anyone?", None, None, True, 0.05)], {"Player A": bot})

    assert report["steps"][0]["timed_out"] is True
    assert report["failed"] == 1


def test_a_bots_own_messages_never_count_as_a_gm_reply():
    chat = []
    bot = FakeBot("Player A", chat, reply_after=None)
    other = FakeBot("Player B", chat, reply_after=None)

    report = run([Step("Player A", "hello", None, None, True, 0.05)], {"Player A": bot, "Player B": other})

    assert report["steps"][0]["replies"] == []


def test_actions_run_in_order_and_are_recorded():
    chat = []
    bot = FakeBot("Player A", chat)

    report = run([Step("Player A", "look", "1d20", Move("t1", 5, 6), False, 5.0)], {"Player A": bot})

    assert bot.calls == [("say", "look"), ("roll", "1d20"), ("move", "t1", 5.0, 6.0)]
    assert report["steps"][0]["actions"] == [
        {"say": "look"}, {"roll": "1d20", "total": 14}, {"move": {"token": "t1", "x": 5.0, "y": 6.0}}]


def test_a_refused_action_is_recorded_and_the_run_carries_on():
    chat = []
    bot = FakeBot("Player A", chat)
    bot.fail_move = True
    steps = [Step("Player A", None, None, Move("t1", 1, 1), False, 5.0),
             Step("Player A", "still here", None, None, False, 5.0)]

    report = run(steps, {"Player A": bot})

    assert report["steps"][0]["errors"] == ["RuntimeError: move refused: not owner"]
    assert report["steps"][1]["actions"] == [{"say": "still here"}]
    assert report["failed"] == 1
