"""Premier League injuries & suspensions from the public Fantasy Premier League API (no key needed).

Each player carries a status (a=available, d=doubtful, i=injured, s=suspended, n=not available,
u=unavailable/left the club), a news line and season stats we use to estimate how important
the player is to their team.
"""

from __future__ import annotations

from ..http import CachedHttp
from ..models import Absence, Team
from ..teams import normalize

BOOTSTRAP_URL = "https://fantasy.premierleague.com/api/bootstrap-static/"
POSITIONS = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}
REASONS = {"i": "injury", "s": "suspension", "d": "doubtful", "n": "unavailable"}

# football-data.org TLA -> FPL short_name where they differ
TLA_TO_FPL = {"NOT": "NFO"}


def _num(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _share(value: float, best: float) -> float:
    return value / best if best > 0 else 0.0


class FplClient:
    def __init__(self, http: CachedHttp):
        self.http = http
        self._data: dict | None = None

    def bootstrap(self) -> dict:
        if self._data is None:
            self._data = self.http.get_json(BOOTSTRAP_URL, ttl=3600)
        return self._data

    def find_team_id(self, team: Team) -> int | None:
        teams = self.bootstrap().get("teams") or []
        tla = TLA_TO_FPL.get(team.tla or "", team.tla or "")
        for t in teams:
            if tla and t.get("short_name") == tla:
                return t["id"]
        wanted = {normalize(team.name), normalize(team.short_name)}
        for t in teams:
            if normalize(t.get("name", "")) in wanted:
                return t["id"]
        return None

    def absences(self, team: Team) -> list[Absence] | None:
        """Unavailable/doubtful players for a Premier League team, or None if the team is not in FPL."""
        team_id = self.find_team_id(team)
        if team_id is None:
            return None
        players = [p for p in self.bootstrap().get("elements") or [] if p.get("team") == team_id]
        if not players:
            return []
        best = {
            "minutes": max(_num(p.get("minutes")) for p in players),
            "ict": max(_num(p.get("ict_index")) for p in players),
            "involvement": max(_num(p.get("goals_scored")) + _num(p.get("assists")) for p in players),
            "cost": max(_num(p.get("now_cost")) for p in players),
        }
        out = []
        for p in players:
            reason = REASONS.get(p.get("status", "a"))
            if reason is None:
                continue
            importance = (
                0.40 * _share(_num(p.get("minutes")), best["minutes"])
                + 0.25 * _share(_num(p.get("ict_index")), best["ict"])
                + 0.15 * _share(_num(p.get("goals_scored")) + _num(p.get("assists")), best["involvement"])
                + 0.20 * _share(_num(p.get("now_cost")), best["cost"])
            )
            chance = p.get("chance_of_playing_next_round")
            out.append(Absence(
                player=p.get("web_name") or f"{p.get('first_name', '')} {p.get('second_name', '')}".strip(),
                reason=reason,
                detail=p.get("news") or "",
                position=POSITIONS.get(p.get("element_type")),
                importance=round(min(1.0, importance), 3),
                chance_of_playing=None if chance is None else chance / 100,
                source="fpl",
            ))
        return sorted(out, key=lambda a: a.importance * a.missing_weight, reverse=True)
