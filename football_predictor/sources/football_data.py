"""Client for the football-data.org v4 API (fixtures, results, standings, head-to-head).

Free tier: top European leagues + Champions League, 10 requests/minute.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from ..http import CachedHttp
from ..models import LeagueTable, Match, StandingRow, Team

BASE_URL = "https://api.football-data.org/v4"
RATE_LIMIT_PER_MIN = 10


def parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def parse_team(data: dict) -> Team:
    return Team(
        id=int(data["id"]),
        name=data.get("name") or "",
        short_name=data.get("shortName") or data.get("name") or "",
        tla=data.get("tla"),
        crest=data.get("crest"),
    )


def parse_match(data: dict) -> Match:
    full_time = (data.get("score") or {}).get("fullTime") or {}
    comp = data.get("competition") or {}
    return Match(
        id=int(data["id"]),
        utc_date=parse_datetime(data["utcDate"]),
        status=data.get("status", ""),
        home=parse_team(data["homeTeam"]),
        away=parse_team(data["awayTeam"]),
        competition_code=comp.get("code") or "",
        competition_name=comp.get("name") or "",
        matchday=data.get("matchday"),
        home_goals=full_time.get("home"),
        away_goals=full_time.get("away"),
    )


def parse_row(data: dict) -> StandingRow:
    return StandingRow(
        position=int(data["position"]),
        team=parse_team(data["team"]),
        played=int(data.get("playedGames") or 0),
        won=int(data.get("won") or 0),
        draw=int(data.get("draw") or 0),
        lost=int(data.get("lost") or 0),
        points=int(data.get("points") or 0),
        goals_for=int(data.get("goalsFor") or 0),
        goals_against=int(data.get("goalsAgainst") or 0),
        form=data.get("form"),
    )


def parse_standings(data: dict) -> LeagueTable:
    comp = data.get("competition") or {}
    table = LeagueTable(competition_code=comp.get("code") or "", competition_name=comp.get("name") or "")
    for block in data.get("standings") or []:
        kind = (block.get("type") or "TOTAL").lower()
        if kind not in ("total", "home", "away"):
            continue
        rows = [parse_row(r) for r in block.get("table") or []]
        # Group stages publish several TOTAL tables; merge them.
        getattr(table, kind).extend(rows)
    return table


class FootballDataClient:
    def __init__(self, api_key: str, http: CachedHttp):
        if not api_key:
            raise ValueError("FOOTBALL_DATA_API_KEY is not set")
        self.api_key = api_key
        self.http = http
        http.rate_limits.setdefault("api.football-data.org", RATE_LIMIT_PER_MIN)

    def _get(self, path: str, params: dict | None = None, ttl: int = 3600) -> dict:
        return self.http.get_json(f"{BASE_URL}{path}", params=params,
                                  headers={"X-Auth-Token": self.api_key}, ttl=ttl)

    def standings(self, competition: str) -> LeagueTable:
        return parse_standings(self._get(f"/competitions/{competition}/standings", ttl=3600))

    def matches(self, date_from: date, date_to: date, competitions: list[str] | None = None) -> list[Match]:
        """Fixtures across competitions. The API caps a window at 10 days, so longer ranges are split."""
        out: list[Match] = []
        start = date_from
        while start <= date_to:
            end = min(start + timedelta(days=9), date_to)
            data = self._get("/matches", params={
                "dateFrom": start.isoformat(),
                "dateTo": end.isoformat(),
                "competitions": ",".join(competitions) if competitions else None,
            }, ttl=900)
            out.extend(parse_match(m) for m in data.get("matches") or [])
            start = end + timedelta(days=1)
        return sorted(out, key=lambda m: m.utc_date)

    def match(self, match_id: int) -> Match:
        return parse_match(self._get(f"/matches/{match_id}", ttl=600))

    def team_matches(self, team_id: int, date_from: date, date_to: date) -> list[Match]:
        data = self._get(f"/teams/{team_id}/matches", params={
            "dateFrom": date_from.isoformat(),
            "dateTo": date_to.isoformat(),
            "status": "FINISHED",
        }, ttl=3 * 3600)
        return sorted((parse_match(m) for m in data.get("matches") or []), key=lambda m: m.utc_date)

    def head_to_head(self, match_id: int, limit: int = 10) -> list[Match]:
        data = self._get(f"/matches/{match_id}/head2head", params={"limit": limit}, ttl=24 * 3600)
        return sorted((parse_match(m) for m in data.get("matches") or []), key=lambda m: m.utc_date)
