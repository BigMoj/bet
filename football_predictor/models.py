"""Plain data structures shared across sources, analysis and reporting."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class Team:
    id: int
    name: str
    short_name: str = ""
    tla: str | None = None
    crest: str | None = None

    @property
    def display(self) -> str:
        return self.short_name or self.name


@dataclass
class Match:
    id: int
    utc_date: datetime
    status: str
    home: Team
    away: Team
    competition_code: str = ""
    competition_name: str = ""
    matchday: int | None = None
    home_goals: int | None = None
    away_goals: int | None = None

    @property
    def finished(self) -> bool:
        return self.status == "FINISHED" and self.home_goals is not None and self.away_goals is not None

    @property
    def started(self) -> bool:
        return self.status in {"IN_PLAY", "PAUSED", "FINISHED", "SUSPENDED", "AWARDED"}

    @property
    def outcome(self) -> str | None:
        """HOME / DRAW / AWAY for finished matches."""
        if not self.finished:
            return None
        if self.home_goals > self.away_goals:
            return "HOME"
        if self.home_goals < self.away_goals:
            return "AWAY"
        return "DRAW"

    def involves(self, team_id: int) -> bool:
        return team_id in (self.home.id, self.away.id)

    def goals_for(self, team_id: int) -> int:
        return self.home_goals if team_id == self.home.id else self.away_goals

    def goals_against(self, team_id: int) -> int:
        return self.away_goals if team_id == self.home.id else self.home_goals

    def result_for(self, team_id: int) -> str:
        """W / D / L from the given team's perspective."""
        gf, ga = self.goals_for(team_id), self.goals_against(team_id)
        return "W" if gf > ga else "L" if gf < ga else "D"

    def opponent(self, team_id: int) -> Team:
        return self.away if team_id == self.home.id else self.home


@dataclass
class StandingRow:
    position: int
    team: Team
    played: int
    won: int
    draw: int
    lost: int
    points: int
    goals_for: int
    goals_against: int
    form: str | None = None

    @property
    def goal_difference(self) -> int:
        return self.goals_for - self.goals_against

    @property
    def ppg(self) -> float:
        return self.points / self.played if self.played else 0.0


@dataclass
class LeagueTable:
    competition_code: str
    competition_name: str
    total: list[StandingRow] = field(default_factory=list)
    home: list[StandingRow] = field(default_factory=list)
    away: list[StandingRow] = field(default_factory=list)

    def row(self, team_id: int, kind: str = "total") -> StandingRow | None:
        for r in getattr(self, kind):
            if r.team.id == team_id:
                return r
        return None

    @property
    def size(self) -> int:
        return len(self.total)


@dataclass
class Absence:
    player: str
    reason: str  # injury | suspension | doubtful | unavailable
    detail: str = ""
    position: str | None = None  # GK | DEF | MID | FWD
    importance: float = 0.3  # 0..1, how much the team relies on the player
    chance_of_playing: float | None = None  # 0..1 when known
    source: str = ""  # fpl | news

    @property
    def missing_weight(self) -> float:
        """Probability the player misses the match."""
        if self.chance_of_playing is not None:
            return max(0.0, min(1.0, 1.0 - self.chance_of_playing))
        return 0.5 if self.reason == "doubtful" else 1.0


@dataclass
class TeamForm:
    matches: list[Match]
    team_id: int
    weighted_ppg: float
    weighted_goals_for: float
    weighted_goals_against: float

    @property
    def results(self) -> list[str]:
        return [m.result_for(self.team_id) for m in self.matches]

    @property
    def form_string(self) -> str:
        """Most recent last, e.g. 'WWDLW'."""
        return "".join(self.results[-5:])

    @property
    def last5_points(self) -> int:
        return sum({"W": 3, "D": 1, "L": 0}[r] for r in self.results[-5:])


@dataclass
class NewsReport:
    home_absences: list[Absence] = field(default_factory=list)
    away_absences: list[Absence] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    summary: str = ""
    sources: list[str] = field(default_factory=list)


@dataclass
class Factor:
    title: str
    detail: str
    favors: str  # HOME | AWAY | NEUTRAL


@dataclass
class Prediction:
    match: Match
    p_home: float
    p_draw: float
    p_away: float
    xg_home: float
    xg_away: float
    likely_score: tuple[int, int]
    pick: str  # HOME | DRAW | AWAY
    confidence: str  # high | medium | low
    factors: list[Factor] = field(default_factory=list)
    home_form: TeamForm | None = None
    away_form: TeamForm | None = None
    home_row: StandingRow | None = None
    away_row: StandingRow | None = None
    table_size: int = 0
    home_absences: list[Absence] = field(default_factory=list)
    away_absences: list[Absence] = field(default_factory=list)
    h2h: list[Match] = field(default_factory=list)
    news: NewsReport | None = None
    warnings: list[str] = field(default_factory=list)
    generated_at: datetime | None = None

    @property
    def pick_team(self) -> Team | None:
        return {"HOME": self.match.home, "AWAY": self.match.away}.get(self.pick)

    @property
    def pick_probability(self) -> float:
        return {"HOME": self.p_home, "DRAW": self.p_draw, "AWAY": self.p_away}[self.pick]
