"""Run a scenario: each step has one bot act, then the run waits for the engine's GM to reply.

The GM is recognised by its Foundry user name, so a bot's own messages never count as a reply. Replies
are collected until the chat has been quiet for `settle` seconds, since the GM can post several
messages for one player action.
"""

import asyncio
import time
from dataclasses import asdict
from typing import Any, Dict, List, Mapping

from playtest.bot import PlayerBot
from playtest.scenario import Step, new_gm_messages


async def _perform(bot: PlayerBot, step: Step) -> List[Dict[str, Any]]:
    done: List[Dict[str, Any]] = []
    if step.say is not None:
        await bot.say(step.say)
        done.append({"say": step.say})
    if step.roll is not None:
        result = await bot.roll(step.roll)
        done.append({"roll": step.roll, "total": result.get("total")})
    if step.move is not None:
        await bot.move_token(step.move.token, step.move.x, step.move.y)
        done.append({"move": asdict(step.move)})
    return done


async def _await_gm(bot: PlayerBot, seen: set, gm_name: str, step: Step, started: float,
                    settle: float, poll: float) -> Dict[str, Any]:
    deadline = started + step.timeout
    first_reply_at = None
    replies: Dict[str, Dict[str, Any]] = {}
    while True:
        for m in new_gm_messages(await bot.messages(), seen, gm_name):
            replies.setdefault(m["id"], {"id": m["id"], "content": m["content"]})
            if first_reply_at is None:
                first_reply_at = time.monotonic()
        now = time.monotonic()
        if first_reply_at is not None and now - first_reply_at >= settle:
            break
        if first_reply_at is None and now >= deadline:
            return {"replies": [], "timed_out": True, "latency_s": None}
        await asyncio.sleep(poll)
    return {
        "replies": list(replies.values()),
        "timed_out": False,
        "latency_s": round(first_reply_at - started, 2),
    }


async def run_scenario(name: str, steps: List[Step], bots: Mapping[str, PlayerBot], gm_name: str,
                       *, settle: float = 5.0, poll: float = 2.0) -> Dict[str, Any]:
    report: Dict[str, Any] = {"scenario": name, "steps": [], "failed": 0}
    for index, step in enumerate(steps, 1):
        bot = bots[step.player]
        entry: Dict[str, Any] = {"step": index, "player": step.player, "actions": [], "errors": [],
                                 "replies": [], "timed_out": False, "latency_s": None}
        try:
            seen = {m["id"] for m in await bot.messages()}
            started = time.monotonic()
            entry["actions"] = await _perform(bot, step)
            if step.wait_for_gm:
                entry.update(await _await_gm(bot, seen, gm_name, step, started, settle, poll))
        except Exception as e:
            entry["errors"].append(f"{type(e).__name__}: {e}")
        if entry["errors"] or entry["timed_out"]:
            report["failed"] += 1
        report["steps"].append(entry)
    return report
