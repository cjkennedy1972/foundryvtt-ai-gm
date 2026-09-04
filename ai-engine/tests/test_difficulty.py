"""Regression tests for encounter power ratings across party sizes."""

from combat.difficulty import DynamicDifficulty, PartyComposition


def test_party_power_rating_covers_solo_through_six():
    ratings = [
        PartyComposition(num_players=size, avg_level=5).party_power_rating
        for size in range(1, 7)
    ]

    assert ratings == [0.5, 0.7, 0.8, 1.0, 1.2, 1.2]


def test_ai_companions_count_as_party_members():
    party = DynamicDifficulty().get_party_composition(
        player_count=1, avg_level=5, ai_companions=1
    )

    assert party.effective_num_players == 2
    assert party.party_power_rating == 0.7


def test_party_power_rating_composition_bonuses_apply_to_tiny_parties():
    party = PartyComposition(num_players=1, avg_level=5, has_healer=True)
    assert party.party_power_rating == 0.5 * 1.15
