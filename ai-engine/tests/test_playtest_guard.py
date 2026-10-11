from playtest.guard import WorldUser, precheck

GM = "ai-gm"


def users(*rows):
    return [WorldUser(name, is_gm, active) for name, is_gm, active in rows]


def test_clean_world_with_only_bots_and_gm_online_may_run():
    problems = precheck(
        "Umbraeth", "Umbraeth",
        users((GM, True, True), ("alpha", False, True), ("beta", False, True)),
        {"alpha", "beta"}, GM)

    assert problems == []


def test_wrong_world_is_refused():
    problems = precheck("Krynn", "Umbraeth",
                        users((GM, True, True), ("alpha", False, True)), {"alpha"}, GM)

    assert any("'Krynn'" in p for p in problems)


def test_a_real_player_online_stops_the_run():
    problems = precheck(
        "Umbraeth", "Umbraeth",
        users((GM, True, True), ("alpha", False, True), ("Chris", False, True)),
        {"alpha"}, GM)

    assert any("'Chris' is online" in p for p in problems)


def test_an_offline_real_player_does_not_block_the_run():
    problems = precheck(
        "Umbraeth", "Umbraeth",
        users((GM, True, True), ("alpha", False, True), ("Chris", False, False)),
        {"alpha"}, GM)

    assert problems == []


def test_a_bot_with_the_gm_role_is_refused():
    problems = precheck("Umbraeth", "Umbraeth",
                        users((GM, True, True), ("alpha", True, True)), {"alpha"}, GM)

    assert any("has the GM role" in p for p in problems)


def test_a_missing_bot_account_is_refused():
    problems = precheck("Umbraeth", "Umbraeth",
                        users((GM, True, True)), {"alpha"}, GM)

    assert any("'alpha' does not exist" in p for p in problems)


def test_an_offline_engine_gm_is_refused():
    problems = precheck("Umbraeth", "Umbraeth",
                        users((GM, True, False), ("alpha", False, True)), {"alpha"}, GM)

    assert any("'ai-gm' is not online" in p for p in problems)


def test_the_gm_account_cannot_be_a_bot():
    problems = precheck("Umbraeth", "Umbraeth",
                        users((GM, True, True)), {GM}, GM)

    assert any("cannot also be a bot" in p for p in problems)
