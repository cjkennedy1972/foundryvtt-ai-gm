"""DynamicDifficulty: party power composition bonuses, mid-combat recommendations, HP scaling."""

import pytest

from combat.difficulty import (
    DynamicDifficulty, EncounterDifficulty as D, EncounterProfile, PartyComposition,
)


def _party(n=4, lvl=5, **kw):
    return PartyComposition(num_players=n, avg_level=lvl, **kw)


def test_each_role_adds_its_own_multiplier_to_party_power():
    base = _party().party_power_rating
    assert base == 1.0
    assert _party(has_tank=True).party_power_rating == pytest.approx(1.1)
    assert _party(has_damage_dealer=True).party_power_rating == pytest.approx(1.1)
    assert _party(has_controller=True).party_power_rating == pytest.approx(1.05)
    assert _party(has_healer=True).party_power_rating == pytest.approx(1.15)
    everything = _party(has_healer=True, has_tank=True, has_damage_dealer=True, has_controller=True)
    assert everything.party_power_rating == pytest.approx(1.15 * 1.1 * 1.1 * 1.05)


def test_party_size_scales_power_and_ai_companions_count():
    assert _party(1).party_power_rating == 0.5
    assert _party(2).party_power_rating == 0.7
    assert _party(3).party_power_rating == 0.8
    assert _party(5).party_power_rating == 1.2
    assert _party(1, ai_companions=3).effective_num_players == 4
    assert _party(0).effective_num_players == 1          # never zero: budgets must not collapse
    assert _party(2, ai_companions=-5).effective_num_players == 2  # negative companions ignored


def test_roles_drive_composition_flags():
    d = DynamicDifficulty()
    p = d.get_party_composition(4, 5, roles=["druid", "paladin", "ranger", "warlock"])
    assert (p.has_healer, p.has_tank, p.has_damage_dealer, p.has_controller) == (True, True, True, True)
    p = d.get_party_composition(4, 5, roles=["bard"], ai_companions=2)
    assert (p.has_healer, p.has_tank, p.has_damage_dealer, p.has_controller) == (True, False, False, False)
    assert p.ai_companions == 2
    p = d.get_party_composition(4, 5)
    assert not (p.has_healer or p.has_tank or p.has_damage_dealer or p.has_controller)


def test_party_budget_clamps_level_and_adds_trivial_band():
    d = DynamicDifficulty()
    assert d.party_budget(_party(4, 0.4))["easy"] == 25 * 4        # level floor is 1
    assert d.party_budget(_party(4, 99))["deadly"] == 12700 * 4    # level cap is 20
    b = d.party_budget(_party(2, 3))
    assert b["trivial"] == b["easy"] / 2 == 75.0


@pytest.mark.parametrize("count,mult", [(0, 1.0), (1, 1.0), (2, 1.5), (3, 2.0), (6, 2.0), (7, 2.5), (10, 2.5),
                                        (11, 3.0), (14, 3.0), (15, 4.0)])
def test_encounter_multiplier_bands(count, mult):
    assert DynamicDifficulty.encounter_multiplier(count) == mult


def test_a_band_starts_exactly_at_its_threshold():
    d = DynamicDifficulty()
    p = _party(4, 5)  # medium 2000, hard 3000, deadly 4400
    # one CR-5-ish monster: craft XP with CR 20 (25000) too big; use CR 7 (2900) and 8 (3900)
    assert d.calculate_difficulty(EncounterProfile(["m"], [7]), p) == D.MEDIUM   # 2900 in [2000,3000)
    assert d.calculate_difficulty(EncounterProfile(["m"], [8]), p) == D.HARD     # 3900 in [3000,4400)
    assert d.calculate_difficulty(EncounterProfile(["m"], [9]), p) == D.DEADLY   # 5000 >= 4400
    assert d.calculate_difficulty(EncounterProfile(["m"], [4]), p) == D.EASY     # 1100 in [1000,2000)
    assert d.calculate_difficulty(EncounterProfile(["m"], [3]), p) == D.TRIVIAL  # 700 < 1000
    # exactly on a threshold lands in the harder band (level 1 x4: medium 200 == one CR-1 monster)
    assert d.calculate_difficulty(EncounterProfile(["m"], [1]), _party(4, 1)) == D.MEDIUM
    assert d.calculate_difficulty(EncounterProfile(["m"], [0.5]), _party(4, 1)) == D.EASY  # 100 == easy threshold
    assert d.calculate_difficulty(EncounterProfile(["m"], [0.25]), _party(4, 1)) == D.TRIVIAL  # 50 < 100
    # solo level 1 (power 0.5): 50 XP / 0.5 == 100 == the deadly threshold exactly
    assert d.calculate_difficulty(EncounterProfile(["m"], [0.25]), _party(1, 1)) == D.DEADLY


def test_group_multiplier_and_party_power_move_the_rating():
    d = DynamicDifficulty()
    four_cr3 = EncounterProfile(["a"] * 4, [3] * 4)          # 2800 * 2.0 = 5600
    assert d.calculate_difficulty(four_cr3, _party(4, 5)) == D.DEADLY
    strong = _party(4, 5, has_healer=True, has_tank=True, has_damage_dealer=True, has_controller=True)
    # 5600 / 1.46 ~ 3835: the same fight reads easier for a well-rounded party
    assert d.calculate_difficulty(four_cr3, strong) == D.HARD
    assert d.calculate_difficulty(EncounterProfile(["x"], [0]), _party(4, 5)) == D.TRIVIAL
    assert d.calculate_difficulty(EncounterProfile(["x"], [999]), _party(4, 5)) == D.TRIVIAL  # unknown CR = 0 XP


def test_suggestions_scale_with_level_and_difficulty():
    d = DynamicDifficulty()
    p = _party(4, 6)
    s = d.suggest_encounters(p, D.DEADLY)
    assert len(s) == 3
    assert s[0]["suggested_cr"] == 7 and s[0]["xp"] == 1400 * 4
    s = d.suggest_encounters(p, D.HARD, num_suggestions=2)
    assert len(s) == 2 and s[0]["suggested_cr"] == 6
    assert d.suggest_encounters(_party(4, 1), D.TRIVIAL)[1]["suggested_cr_each"] == 0.25  # floor
    assert d.suggest_encounters(p, D.EASY)[1]["suggested_cr_each"] == 4


def test_action_recommendations_by_difficulty_and_action_economy():
    d = DynamicDifficulty()
    p = _party(4, 5)
    trivial = d.get_action_recommendations(EncounterProfile(["x"], [0]), p)
    assert "Consider adding more monsters or a tougher enemy" in trivial
    assert any("minions" in r for r in trivial)                    # 1 monster < 4/2... 1 < 2
    deadly = d.get_action_recommendations(EncounterProfile(["x"], [9]), p)
    assert "Consider removing some monsters to balance difficulty" in deadly
    hard = d.get_action_recommendations(EncounterProfile(["a", "b"], [4, 4]), p)  # 2200*1.5=3300 -> HARD
    assert any("environmental hazards" in r for r in hard)
    assert not any("minions" in r or "reducing" in r for r in hard)  # 2 monsters vs 4 players: balanced
    swarm = d.get_action_recommendations(EncounterProfile(["g"] * 9, [0.25] * 9), p)
    assert any("reducing enemy count" in r for r in swarm)
    eight = d.get_action_recommendations(EncounterProfile(["g"] * 8, [0.25] * 8), p)  # exactly 2x players
    assert not any("reducing enemy count" in r for r in eight)
    medium = d.get_action_recommendations(EncounterProfile(["a", "b", "c"], [1, 1, 1]), p)
    assert medium == []


def test_hp_scaling_compounds_per_step():
    d = DynamicDifficulty()
    assert d.scale_encounter_hp(D.MEDIUM, D.MEDIUM) == 1.0
    assert d.scale_encounter_hp(D.MEDIUM, D.HARD) == pytest.approx(1.4)
    assert d.scale_encounter_hp(D.TRIVIAL, D.DEADLY) == pytest.approx(1.4 ** 4)
    assert d.scale_encounter_hp(D.DEADLY, D.EASY) == pytest.approx(1 / 1.4 ** 3)
    assert d.scale_encounter_hp(D.HARD, D.MEDIUM) < 1.0
