import json
import os

import pytest

from playtest.accounts import AccountsError, load_accounts


def _write(tmp_path, data, mode=0o600):
    p = tmp_path / "accounts.json"
    p.write_text(json.dumps(data))
    os.chmod(p, mode)
    return str(p)


GOOD = {
    "world": "Umbraeth",
    "players": [
        {"label": "Player A", "username": "alpha", "password": "pa"},
        {"label": "Player B", "username": "beta", "password": "pb"},
    ],
}


def test_loads_players_and_world(tmp_path):
    accounts = load_accounts(_write(tmp_path, GOOD))

    assert accounts.world == "Umbraeth"
    assert [(p.label, p.username, p.password) for p in accounts.players] == [
        ("Player A", "alpha", "pa"), ("Player B", "beta", "pb")]


def test_refuses_a_file_other_users_can_read(tmp_path):
    with pytest.raises(AccountsError, match="chmod 600"):
        load_accounts(_write(tmp_path, GOOD, mode=0o644))


def test_missing_file_says_what_to_do(tmp_path):
    with pytest.raises(AccountsError, match="accounts.example.json"):
        load_accounts(str(tmp_path / "nope.json"))


@pytest.mark.parametrize("bad", [
    {"players": GOOD["players"]},
    {"world": "  ", "players": GOOD["players"]},
    {"world": "Umbraeth", "players": []},
    {"world": "Umbraeth", "players": [{"label": "A", "username": "x"}]},
    {"world": "Umbraeth", "players": [{"label": "A", "username": "x", "password": ""}]},
    {"world": "Umbraeth", "players": ["not an object"]},
    {"world": "Umbraeth", "players": [
        {"label": "A", "username": "x", "password": "p"},
        {"label": "A", "username": "y", "password": "q"}]},
    ["not", "an", "object"],
])
def test_rejects_malformed_files(tmp_path, bad):
    with pytest.raises(AccountsError):
        load_accounts(_write(tmp_path, bad))
