"""Goals-based match model.

1. Attack/defence strengths per team, relative to the league average, from
   (a) this season's home/away table splits and (b) recent form, both shrunk toward
   average so a handful of games can't produce extreme ratings.
2. Expected goals = league average x own attack x opponent defence, then adjusted
   for missing players (weighted by position and importance).
3. Scoreline probabilities from independent Poisson distributions with the
   Dixon-Coles low-score correction; summed into home/draw/away probabilities.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..models import Absence, LeagueTable, StandingRow, TeamForm

DEFAULT_HOME_GOALS = 1.55
DEFAULT_AWAY_GOALS = 1.25
SEASON_PRIOR_GAMES = 6
FORM_PRIOR_GAMES = 4
SEASON_WEIGHT = 0.6
DC_RHO = -0.05
MAX_GOALS = 10

# How much a missing player at each position hurts attack / defence
ATTACK_POS_WEIGHT = {"FWD": 1.0, "MID": 0.6, "DEF": 0.15, "GK": 0.0, None: 0.4}
DEFENCE_POS_WEIGHT = {"GK": 1.0, "DEF": 0.8, "MID": 0.3, "FWD": 0.0, None: 0.3}
ATTACK_UNIT = 0.12
DEFENCE_UNIT = 0.10
MAX_PENALTY = 0.25


@dataclass
class Strength:
    attack: float = 1.0
    defence: float = 1.0  # goals conceded relative to average: < 1 is good


def league_averages(table: LeagueTable | None) -> tuple[float, float]:
    """Average goals per game scored by home and away sides."""
    if table and table.home and table.away:
        home_games = sum(r.played for r in table.home)
        away_games = sum(r.played for r in table.away)
        if home_games >= 10 and away_games >= 10:
            return (sum(r.goals_for for r in table.home) / home_games,
                    sum(r.goals_for for r in table.away) / away_games)
    if table and table.total:
        games = sum(r.played for r in table.total)
        if games >= 20:
            per_team = sum(r.goals_for for r in table.total) / games
            ratio = DEFAULT_HOME_GOALS / (DEFAULT_HOME_GOALS + DEFAULT_AWAY_GOALS)
            return per_team * 2 * ratio, per_team * 2 * (1 - ratio)
    return DEFAULT_HOME_GOALS, DEFAULT_AWAY_GOALS


def _shrunk_rate(total: float, games: int, prior_rate: float, prior_games: int) -> float:
    return (total + prior_rate * prior_games) / (games + prior_games)


def season_strength(row: StandingRow | None, avg_for: float, avg_against: float) -> tuple[Strength, int]:
    """Strength from a table row; avg_for/avg_against are the league averages for this venue."""
    if row is None or row.played == 0:
        return Strength(), 0
    attack = _shrunk_rate(row.goals_for, row.played, avg_for, SEASON_PRIOR_GAMES) / avg_for
    defence = _shrunk_rate(row.goals_against, row.played, avg_against, SEASON_PRIOR_GAMES) / avg_against
    return Strength(attack, defence), row.played


def form_strength(form: TeamForm | None, avg_goals: float) -> tuple[Strength, int]:
    if form is None or not form.matches:
        return Strength(), 0
    n = len(form.matches)
    attack = _shrunk_rate(form.weighted_goals_for * n, n, avg_goals, FORM_PRIOR_GAMES) / avg_goals
    defence = _shrunk_rate(form.weighted_goals_against * n, n, avg_goals, FORM_PRIOR_GAMES) / avg_goals
    return Strength(attack, defence), n


def blend(season: tuple[Strength, int], form: tuple[Strength, int]) -> Strength:
    (s, s_n), (f, f_n) = season, form
    if s_n and f_n:
        w = SEASON_WEIGHT
    elif s_n:
        w = 1.0
    elif f_n:
        w = 0.0
    else:
        return Strength()
    return Strength(w * s.attack + (1 - w) * f.attack, w * s.defence + (1 - w) * f.defence)


def absence_penalties(absences: list[Absence]) -> tuple[float, float]:
    """(attack reduction, defence weakening) as fractions, capped."""
    attack = defence = 0.0
    for a in absences:
        weight = a.importance * a.missing_weight
        attack += weight * ATTACK_POS_WEIGHT.get(a.position, 0.4) * ATTACK_UNIT
        defence += weight * DEFENCE_POS_WEIGHT.get(a.position, 0.3) * DEFENCE_UNIT
    return min(attack, MAX_PENALTY), min(defence, MAX_PENALTY)


def expected_goals(
    table: LeagueTable | None,
    home_row: StandingRow | None, away_row: StandingRow | None,
    home_venue_row: StandingRow | None, away_venue_row: StandingRow | None,
    home_form: TeamForm | None, away_form: TeamForm | None,
    home_absences: list[Absence], away_absences: list[Absence],
    neutral: bool = False,
) -> tuple[float, float]:
    avg_home, avg_away = league_averages(table)
    avg_team = (avg_home + avg_away) / 2
    if neutral:
        avg_home = avg_away = avg_team

    # Prefer venue-specific splits; fall back to the overall table.
    if home_venue_row and away_venue_row and not neutral:
        home_season = season_strength(home_venue_row, avg_home, avg_away)
        away_season = season_strength(away_venue_row, avg_away, avg_home)
    else:
        home_season = season_strength(home_row, avg_team, avg_team)
        away_season = season_strength(away_row, avg_team, avg_team)

    home = blend(home_season, form_strength(home_form, avg_team))
    away = blend(away_season, form_strength(away_form, avg_team))

    h_att_pen, h_def_pen = absence_penalties(home_absences)
    a_att_pen, a_def_pen = absence_penalties(away_absences)

    xg_home = avg_home * home.attack * away.defence * (1 - h_att_pen) * (1 + a_def_pen)
    xg_away = avg_away * away.attack * home.defence * (1 - a_att_pen) * (1 + h_def_pen)
    return max(0.15, xg_home), max(0.15, xg_away)


def _poisson(k: int, lam: float) -> float:
    return math.exp(-lam) * lam ** k / math.factorial(k)


def score_matrix(xg_home: float, xg_away: float, rho: float = DC_RHO) -> list[list[float]]:
    m = [[_poisson(h, xg_home) * _poisson(a, xg_away) for a in range(MAX_GOALS + 1)]
         for h in range(MAX_GOALS + 1)]
    # Dixon-Coles correction: real football has slightly more 0-0/1-1 and fewer 1-0/0-1 than Poisson.
    m[0][0] *= 1 - xg_home * xg_away * rho
    m[0][1] *= 1 + xg_home * rho
    m[1][0] *= 1 + xg_away * rho
    m[1][1] *= 1 - rho
    total = sum(map(sum, m))
    return [[p / total for p in row] for row in m]


def outcome_probabilities(xg_home: float, xg_away: float) -> tuple[float, float, float, tuple[int, int]]:
    m = score_matrix(xg_home, xg_away)
    p_home = sum(m[h][a] for h in range(MAX_GOALS + 1) for a in range(MAX_GOALS + 1) if h > a)
    p_draw = sum(m[i][i] for i in range(MAX_GOALS + 1))
    p_away = 1 - p_home - p_draw
    likely = max(((h, a) for h in range(MAX_GOALS + 1) for a in range(MAX_GOALS + 1)), key=lambda s: m[s[0]][s[1]])
    return p_home, p_draw, p_away, likely


def likely_score_for(xg_home: float, xg_away: float, pick: str) -> tuple[int, int]:
    """Most likely exact score among scorelines that match the picked outcome."""
    m = score_matrix(xg_home, xg_away)
    ok = {"HOME": lambda h, a: h > a, "AWAY": lambda h, a: h < a, "DRAW": lambda h, a: h == a}[pick]
    scores = [(h, a) for h in range(MAX_GOALS + 1) for a in range(MAX_GOALS + 1) if ok(h, a)]
    return max(scores, key=lambda s: m[s[0]][s[1]])


def pick_outcome(p_home: float, p_draw: float, p_away: float) -> tuple[str, str]:
    """(pick, confidence). The draw is picked only when it is genuinely the most likely result."""
    probs = {"HOME": p_home, "DRAW": p_draw, "AWAY": p_away}
    pick = max(probs, key=probs.get)
    top = probs[pick]
    second = sorted(probs.values())[-2]
    if top >= 0.55 and top - second >= 0.2:
        confidence = "high"
    elif top >= 0.45 and top - second >= 0.08:
        confidence = "medium"
    else:
        confidence = "low"
    return pick, confidence
