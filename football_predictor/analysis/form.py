"""Recent-form metrics with exponential decay (newest match weighs most)."""

from __future__ import annotations

from ..models import Match, TeamForm

POINTS = {"W": 3, "D": 1, "L": 0}


def compute_form(team_id: int, matches: list[Match], last_n: int = 10, decay: float = 0.85) -> TeamForm:
    played = sorted((m for m in matches if m.finished and m.involves(team_id)), key=lambda m: m.utc_date)
    recent = played[-last_n:]
    if not recent:
        return TeamForm(matches=[], team_id=team_id, weighted_ppg=0.0,
                        weighted_goals_for=0.0, weighted_goals_against=0.0)
    weights = [decay ** i for i in range(len(recent) - 1, -1, -1)]  # oldest -> newest
    total = sum(weights)
    return TeamForm(
        matches=recent,
        team_id=team_id,
        weighted_ppg=sum(w * POINTS[m.result_for(team_id)] for w, m in zip(weights, recent)) / total,
        weighted_goals_for=sum(w * m.goals_for(team_id) for w, m in zip(weights, recent)) / total,
        weighted_goals_against=sum(w * m.goals_against(team_id) for w, m in zip(weights, recent)) / total,
    )
