from combat.loop import _limit_multiattack_actions


def _attack(name):
    return {"type": "attack_with_item", "item_name": name}


def test_multiattack_limit_keeps_non_attack_actions_and_first_attacks():
    actions = [_attack("Claw"), {"type": "move_token"}, _attack("Bite"), _attack("Tail")]
    assert _limit_multiattack_actions(actions, 2) == actions[:3]


def test_multiattack_limit_never_allows_zero_or_negative_limit():
    actions = [_attack("Claw"), _attack("Bite"), {"type": "narrate"}]
    assert _limit_multiattack_actions(actions, 0) == actions[:1] + [actions[2]]
    assert _limit_multiattack_actions(actions, -2) == actions[:1] + [actions[2]]
