"""Fake football-data.org / FPL responses shaped like the real v4 / bootstrap-static payloads."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest

from football_predictor.config import Settings

NOW = datetime(2026, 10, 4, 9, 0, tzinfo=timezone.utc)

TEAMS = {
    65: ("Manchester City FC", "Man City", "MCI"),
    57: ("Arsenal FC", "Arsenal", "ARS"),
    64: ("Liverpool FC", "Liverpool", "LIV"),
    61: ("Chelsea FC", "Chelsea", "CHE"),
    351: ("Nottingham Forest FC", "Nottingham", "NOT"),
    1044: ("AFC Bournemouth", "Bournemouth", "BOU"),
}


def team(tid):
    name, short, tla = TEAMS[tid]
    return {"id": tid, "name": name, "shortName": short, "tla": tla, "crest": f"https://crests/{tid}.png"}


def match(mid, home, away, when, hg=None, ag=None, comp="PL"):
    finished = hg is not None
    return {
        "id": mid, "utcDate": when.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "status": "FINISHED" if finished else "TIMED", "matchday": 7,
        "competition": {"id": 2021, "name": "Premier League", "code": comp},
        "homeTeam": team(home), "awayTeam": team(away),
        "score": {"winner": None, "duration": "REGULAR",
                  "fullTime": {"home": hg, "away": ag}, "halfTime": {"home": None, "away": None}},
    }


# (position, team, played, won, draw, lost, points, gf, ga) — season so far
TOTAL = [
    (1, 57, 6, 5, 1, 0, 16, 14, 3),
    (2, 64, 6, 4, 1, 1, 13, 12, 6),
    (3, 65, 6, 3, 2, 1, 11, 11, 7),
    (4, 61, 6, 3, 1, 2, 10, 9, 8),
    (5, 1044, 6, 1, 1, 4, 4, 5, 12),
    (6, 351, 6, 0, 2, 4, 2, 3, 18),
]


def standings_payload():
    def rows(scale_home):
        out = []
        for pos, tid, pl, w, d, l, pts, gf, ga in TOTAL:
            h = pl // 2
            if scale_home == "TOTAL":
                vals = (pl, w, d, l, pts, gf, ga)
            elif scale_home == "HOME":
                vals = (h, (w + 1) // 2, d // 2, l // 2, ((w + 1) // 2) * 3 + d // 2, (gf + 1) // 2, ga // 2)
            else:
                aw = w - (w + 1) // 2
                vals = (pl - h, aw, d - d // 2, l - l // 2, aw * 3 + d - d // 2, gf - (gf + 1) // 2, ga - ga // 2)
            out.append({"position": pos, "team": team(tid), "playedGames": vals[0], "form": None,
                        "won": vals[1], "draw": vals[2], "lost": vals[3], "points": vals[4],
                        "goalsFor": vals[5], "goalsAgainst": vals[6], "goalDifference": vals[5] - vals[6]})
        return out
    return {
        "competition": {"id": 2021, "name": "Premier League", "code": "PL"},
        "season": {"id": 2400, "startDate": "2026-08-15"},
        "standings": [{"stage": "REGULAR_SEASON", "type": t, "group": None, "table": rows(t)}
                      for t in ("TOTAL", "HOME", "AWAY")],
    }


def history():
    """Finished matches, oldest first."""
    d = lambda days: NOW - timedelta(days=days)  # noqa: E731
    return [
        match(101, 57, 351, d(42), 4, 0), match(102, 65, 1044, d(42), 3, 1),
        match(103, 64, 61, d(42), 2, 2), match(104, 351, 65, d(35), 1, 1),
        match(105, 1044, 57, d(35), 0, 2), match(106, 61, 64, d(28), 1, 2),
        match(107, 57, 61, d(28), 2, 1), match(108, 65, 64, d(21), 1, 2),
        match(109, 1044, 351, d(21), 2, 0), match(110, 61, 65, d(14), 2, 2),
        match(111, 64, 57, d(14), 1, 1), match(112, 351, 1044, d(7), 1, 1),
        match(113, 57, 64, d(7), 3, 1), match(114, 65, 61, d(7), 3, 2),
        match(90, 57, 65, d(400), 2, 2), match(91, 65, 57, d(220), 1, 0),
    ]


FIXTURE_ID = 500


def fixtures():
    return [match(FIXTURE_ID, 65, 57, NOW + timedelta(hours=6)),
            match(501, 351, 64, NOW + timedelta(days=1))]


def fpl_payload():
    teams = [{"id": 1, "name": "Arsenal", "short_name": "ARS"}, {"id": 13, "name": "Man City", "short_name": "MCI"},
             {"id": 16, "name": "Nott'm Forest", "short_name": "NFO"}]

    def p(pid, name, team_id, pos, status, minutes, ict, goals, assists, cost, news="", chance=None):
        return {"id": pid, "web_name": name, "team": team_id, "element_type": pos, "status": status,
                "minutes": minutes, "ict_index": str(ict), "goals_scored": goals, "assists": assists,
                "now_cost": cost, "news": news, "chance_of_playing_next_round": chance}
    elements = [
        p(1, "Haaland", 13, 4, "a", 540, 80.0, 8, 1, 150),
        p(2, "Rodri", 13, 3, "i", 540, 60.0, 1, 2, 65, "Knee injury - Expected back 01 Dec", 0),
        p(3, "Foden", 13, 3, "d", 300, 40.0, 1, 1, 90, "Knock - 50% chance of playing", 50),
        p(4, "Reserve", 13, 2, "i", 0, 0.0, 0, 0, 40, "Ankle injury"),
        p(10, "Saka", 1, 3, "a", 520, 70.0, 4, 4, 100),
        p(11, "Saliba", 1, 2, "s", 540, 30.0, 0, 0, 60, "Suspended until 05 Oct", 0),
        p(12, "Odegaard", 1, 3, "a", 500, 55.0, 1, 3, 85),
        p(13, "Departed", 1, 4, "u", 0, 0.0, 0, 0, 50, "Joined another club"),
    ]
    return {"teams": teams, "elements": elements}


class FakeHttp:
    def __init__(self):
        self.rate_limits = {}
        self.calls = []

    def get_json(self, url, params=None, headers=None, ttl=0, retries=0):
        parsed = urlparse(url)
        path = parsed.path
        q = {**{k: v[0] for k, v in parse_qs(parsed.query).items()}, **(params or {})}
        self.calls.append(path)
        if "fantasy.premierleague.com" in parsed.netloc:
            return fpl_payload()
        if path.endswith("/standings"):
            return standings_payload()
        if path == "/v4/matches":
            return {"matches": fixtures()}
        if path.startswith("/v4/teams/"):
            tid = int(path.split("/")[3])
            return {"matches": [m for m in history() if tid in (m["homeTeam"]["id"], m["awayTeam"]["id"])]}
        if path.endswith("/head2head"):
            return {"matches": [m for m in history() if {m["homeTeam"]["id"], m["awayTeam"]["id"]} == {65, 57}]}
        if path.startswith("/v4/matches/"):
            mid = int(path.split("/")[3])
            m = next(m for m in fixtures() if m["id"] == mid)
            m["status"], m["score"]["fullTime"] = "FINISHED", {"home": 1, "away": 2}
            return m
        raise AssertionError(f"unexpected url {url}")


@pytest.fixture
def settings(tmp_path):
    return Settings(football_data_key="test", competitions=["PL"], data_dir=tmp_path, news_research="off")


@pytest.fixture
def fake_http():
    return FakeHttp()


@pytest.fixture
def analyzer(settings, fake_http):
    from football_predictor.predictor import Analyzer
    from football_predictor.sources.football_data import FootballDataClient
    from football_predictor.sources.fpl import FplClient
    return Analyzer(settings, FootballDataClient("test", fake_http), FplClient(fake_http), now=NOW)
