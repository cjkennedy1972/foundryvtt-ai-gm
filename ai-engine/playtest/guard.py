"""Checks before a playtest acts in a live world. Any problem found means the run does not start.

The playtest is meant for a world with nobody else in it. A real player logged in while the bots act
would see the bots' messages and rolls, so a non-bot player online is a hard stop.
"""

from dataclasses import dataclass
from typing import Iterable, List


@dataclass(frozen=True)
class WorldUser:
    name: str
    is_gm: bool
    active: bool


def precheck(
    world_title: str,
    expected_world: str,
    users: Iterable[WorldUser],
    bot_names: Iterable[str],
    gm_name: str,
) -> List[str]:
    """Return the reasons the run must not start. An empty list means it may."""
    problems: List[str] = []
    if world_title != expected_world:
        problems.append(f"Foundry is serving '{world_title}', not '{expected_world}'")

    users = list(users)
    bots = set(bot_names)
    by_name = {u.name: u for u in users}

    if gm_name in bots:
        problems.append(f"'{gm_name}' is the engine's GM account and cannot also be a bot")

    for name in sorted(bots):
        user = by_name.get(name)
        if user is None:
            problems.append(f"player account '{name}' does not exist in this world")
        elif user.is_gm:
            problems.append(f"'{name}' has the GM role; a playtest bot must be a player")

    gm = by_name.get(gm_name)
    if gm is None or not gm.active:
        problems.append(f"the engine's GM '{gm_name}' is not online; start the engine and the campaign first")

    for user in users:
        if user.active and not user.is_gm and user.name not in bots:
            problems.append(f"'{user.name}' is online and is not a bot; refusing to run with real players present")

    return problems
