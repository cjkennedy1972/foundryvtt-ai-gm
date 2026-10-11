"""Scenario files: what each bot does, in order, and whether to wait for the GM's reply.

Shape:
  {"name": "...", "steps": [
    {"player": "Aria", "say": "I push open the door.", "roll": "1d20", "move": {"token": "tok1", "x": 400, "y": 300},
     "wait_for_gm": true, "timeout": 120}
  ]}
Each step needs at least one of say, roll, move. A step with no wait_for_gm just performs its actions.
"""

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_TIMEOUT = 120.0
MAX_TIMEOUT = 600.0


class ScenarioError(ValueError):
    """The scenario file is malformed."""


@dataclass(frozen=True)
class Move:
    token: str
    x: float
    y: float


@dataclass(frozen=True)
class Step:
    player: str
    say: Optional[str]
    roll: Optional[str]
    move: Optional[Move]
    wait_for_gm: bool
    timeout: float


def _parse_move(raw: Any, where: str) -> Move:
    if not isinstance(raw, dict):
        raise ScenarioError(f"{where}: 'move' must be an object")
    token = raw.get("token")
    if not isinstance(token, str) or not token:
        raise ScenarioError(f"{where}: 'move.token' must be a token id")
    for axis in ("x", "y"):
        if not isinstance(raw.get(axis), (int, float)) or isinstance(raw.get(axis), bool):
            raise ScenarioError(f"{where}: 'move.{axis}' must be a number")
    return Move(token, float(raw["x"]), float(raw["y"]))


def parse_scenario(data: Dict[str, Any]) -> Tuple[str, List[Step]]:
    if not isinstance(data, dict):
        raise ScenarioError("a scenario must be a JSON object")
    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ScenarioError("'name' must be a non-empty string")
    raw_steps = data.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        raise ScenarioError("'steps' must list at least one step")

    steps: List[Step] = []
    for i, raw in enumerate(raw_steps):
        where = f"step {i + 1}"
        if not isinstance(raw, dict):
            raise ScenarioError(f"{where} must be an object")
        player = raw.get("player")
        if not isinstance(player, str) or not player:
            raise ScenarioError(f"{where}: 'player' must name a player label")
        say = raw.get("say")
        roll = raw.get("roll")
        move = raw.get("move")
        if say is not None and (not isinstance(say, str) or not say.strip()):
            raise ScenarioError(f"{where}: 'say' must be non-empty text")
        if roll is not None and (not isinstance(roll, str) or not roll.strip()):
            raise ScenarioError(f"{where}: 'roll' must be a formula")
        parsed_move = _parse_move(move, where) if move is not None else None
        if say is None and roll is None and parsed_move is None:
            raise ScenarioError(f"{where} does nothing: give it say, roll, or move")
        wait = raw.get("wait_for_gm", False)
        if not isinstance(wait, bool):
            raise ScenarioError(f"{where}: 'wait_for_gm' must be true or false")
        timeout = raw.get("timeout", DEFAULT_TIMEOUT)
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or not 1 <= timeout <= MAX_TIMEOUT:
            raise ScenarioError(f"{where}: 'timeout' must be between 1 and {int(MAX_TIMEOUT)} seconds")
        steps.append(Step(player, say, roll, parsed_move, wait, float(timeout)))

    return name.strip(), steps


def load_scenario(path: str) -> Tuple[str, List[Step]]:
    with open(path, encoding="utf-8") as f:
        return parse_scenario(json.load(f))


def new_gm_messages(messages: List[Dict[str, Any]], seen: set, gm_name: str) -> List[Dict[str, Any]]:
    """Messages the engine's GM account posted that were not in `seen`, oldest first."""
    fresh = [m for m in messages if m.get("id") not in seen and m.get("author") == gm_name]
    return sorted(fresh, key=lambda m: m.get("timestamp") or 0)
