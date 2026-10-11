import pytest
from pydantic import ValidationError

from actions.schemas import EnvironmentalSaveAction, RollAction


def _roll(f):
    return RollAction(formula=f, speaker="x")


def _env(f):
    return EnvironmentalSaveAction(ability="dex", dc=10, target_token_ids=["t"], damage_formula=f)


@pytest.mark.parametrize("make", [_roll, _env])
@pytest.mark.parametrize("bad", ["999d999", "101d6", "1d1001", "50d6+51d4", "d9999"])
def test_oversized_formulas_rejected(make, bad):
    with pytest.raises(ValidationError):
        make(bad)


@pytest.mark.parametrize("make", [_roll, _env])
@pytest.mark.parametrize("ok", ["1d20+5", "8d6", "100d6", "1d1000", "2d20kh1", "d20", "5"])
def test_normal_formulas_accepted(make, ok):
    assert make(ok)


def test_env_damage_formula_optional():
    assert _env(None).damage_formula is None
