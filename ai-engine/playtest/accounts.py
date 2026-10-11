"""Player accounts for an automated playtest.

They live in their own file, not .env, so the engine's settings never load them. The file must be
readable only by its owner, because it holds passwords.
"""

import json
import os
import stat
from dataclasses import dataclass
from typing import List


class AccountsError(ValueError):
    """The accounts file is missing, unsafe, or malformed."""


@dataclass(frozen=True)
class PlayerAccount:
    label: str  # how scenarios refer to this player
    username: str  # the Foundry user name used to log in
    password: str


@dataclass(frozen=True)
class Accounts:
    world: str  # the world the playtest is allowed to run in
    players: List[PlayerAccount]


def load_accounts(path: str) -> Accounts:
    try:
        mode = os.stat(path).st_mode
    except FileNotFoundError as e:
        raise AccountsError(f"{path} does not exist; copy accounts.example.json and fill it in") from e
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise AccountsError(f"{path} is readable by other users; run chmod 600 on it")

    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise AccountsError("the accounts file must be a JSON object")

    world = data.get("world")
    if not isinstance(world, str) or not world.strip():
        raise AccountsError("'world' must name the test world")

    raw_players = data.get("players")
    if not isinstance(raw_players, list) or not raw_players:
        raise AccountsError("'players' must list at least one player account")

    players: List[PlayerAccount] = []
    labels = set()
    for i, raw in enumerate(raw_players):
        if not isinstance(raw, dict):
            raise AccountsError(f"player {i} must be an object")
        for key in ("label", "username", "password"):
            if not isinstance(raw.get(key), str) or not raw[key].strip():
                raise AccountsError(f"player {i} is missing '{key}'")
        if raw["label"] in labels:
            raise AccountsError(f"player label '{raw['label']}' is used twice")
        labels.add(raw["label"])
        players.append(PlayerAccount(raw["label"], raw["username"], raw["password"]))

    return Accounts(world.strip(), players)
