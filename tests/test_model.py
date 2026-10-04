import pytest

from football_predictor.analysis.model import (
    absence_penalties, expected_goals, outcome_probabilities, pick_outcome,
)
from football_predictor.models import Absence, StandingRow, Team


def row(gf, ga, played=10, points=15):
    return StandingRow(1, Team(1, "X"), played, 0, 0, 0, points, gf, ga)


def test_probabilities_sum_to_one():
    ph, pd, pa, score = outcome_probabilities(1.6, 1.1)
    assert ph + pd + pa == pytest.approx(1.0)
    assert ph > pa
    assert score in {(1, 0), (1, 1), (2, 1)}


def test_equal_teams_are_balanced():
    ph, pd, pa, _ = outcome_probabilities(1.3, 1.3)
    assert ph == pytest.approx(pa, abs=1e-9)
    assert pick_outcome(ph, pd, pa)[1] == "low"


def test_stronger_team_gets_higher_xg():
    strong, weak = row(25, 5), row(6, 22)
    xh, xa = expected_goals(None, strong, weak, None, None, None, None, [], [])
    assert xh > 2 * xa


def test_absences_reduce_attack():
    strong, weak = row(20, 10), row(15, 12)
    base, _ = expected_goals(None, strong, weak, None, None, None, None, [], [])
    star = Absence("Striker", "injury", position="FWD", importance=0.9)
    hurt, _ = expected_goals(None, strong, weak, None, None, None, None, [star], [])
    assert hurt < base


def test_absence_penalty_is_capped_and_weighted():
    many = [Absence(f"P{i}", "injury", position="FWD", importance=1.0) for i in range(20)]
    assert absence_penalties(many)[0] == pytest.approx(0.25)
    doubtful = Absence("D", "doubtful", position="FWD", importance=1.0, chance_of_playing=0.75)
    out = Absence("O", "injury", position="FWD", importance=1.0)
    assert absence_penalties([doubtful])[0] == pytest.approx(absence_penalties([out])[0] / 4)


def test_pick_confidence_levels():
    assert pick_outcome(0.65, 0.2, 0.15) == ("HOME", "high")
    assert pick_outcome(0.30, 0.25, 0.45) == ("AWAY", "medium")
    assert pick_outcome(0.36, 0.30, 0.34)[1] == "low"
