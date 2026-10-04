"""Gathers everything about a fixture and turns it into a Prediction."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from . import fa
from .analysis.form import compute_form
from .analysis.model import (
    absence_penalties, expected_goals, likely_score_for, outcome_probabilities, pick_outcome,
)
from .config import Settings
from .http import HttpError
from .models import Absence, Factor, LeagueTable, Match, NewsReport, Prediction, StandingRow, Team, TeamForm
from .teams import find_team, normalize

log = logging.getLogger(__name__)

FORM_LOOKBACK_DAYS = 200


class TeamNotFound(LookupError):
    pass


def _surname(name: str) -> str:
    parts = normalize(name).split()
    return parts[-1] if parts else ""


def merge_absences(primary: list[Absence] | None, extra: list[Absence]) -> list[Absence]:
    """FPL data is primary when available; news adds players it doesn't list."""
    merged = list(primary or [])
    known = {_surname(a.player) for a in merged}
    for a in extra:
        if _surname(a.player) not in known:
            merged.append(a)
            known.add(_surname(a.player))
    return merged


class Analyzer:
    def __init__(self, settings: Settings, football_data, fpl=None, researcher=None,
                 now: datetime | None = None):
        self.settings = settings
        self.fd = football_data
        self.fpl = fpl
        self.researcher = researcher
        self._now = now
        self._tables: dict[str, LeagueTable | None] = {}

    @property
    def now(self) -> datetime:
        return self._now or datetime.now(timezone.utc)

    # ---- data access -------------------------------------------------------------------------

    def refresh(self) -> None:
        """Forget standings fetched this cycle (the HTTP cache still limits real requests)."""
        self._tables.clear()

    def table(self, competition: str) -> LeagueTable | None:
        if competition not in self._tables:
            try:
                self._tables[competition] = self.fd.standings(competition)
            except HttpError as e:
                log.warning("no standings for %s: %s", competition, e)
                self._tables[competition] = None
        return self._tables[competition]

    def known_teams(self) -> list[Team]:
        teams: dict[int, Team] = {}
        for comp in self.settings.competitions:
            table = self.table(comp)
            for row in table.total if table else []:
                teams[row.team.id] = row.team
        return list(teams.values())

    def upcoming(self, days: int | None = None, competitions: list[str] | None = None) -> list[Match]:
        start = self.now.date()
        end = start + timedelta(days=days if days is not None else self.settings.lookahead_days)
        matches = self.fd.matches(start, end, competitions or self.settings.competitions)
        return [m for m in matches if not m.started and m.utc_date >= self.now - timedelta(hours=2)]

    def resolve(self, query: str, pool: list[Team] | None = None) -> Team:
        team = find_team(query, pool if pool is not None else self.known_teams())
        if team is None:
            raise TeamNotFound(query)
        return team

    def find_fixture(self, query_a: str, query_b: str, days: int = 21) -> Match | None:
        fixtures = self.upcoming(days=days)
        pool = {t.id: t for m in fixtures for t in (m.home, m.away)}
        a, b = find_team(query_a, pool.values()), find_team(query_b, pool.values())
        if a and b:
            for m in fixtures:
                if {m.home.id, m.away.id} == {a.id, b.id}:
                    return m
        return None

    def hypothetical(self, query_home: str, query_away: str) -> Match:
        home, away = self.resolve(query_home), self.resolve(query_away)
        comp = next((c for c in self.settings.competitions
                     if (t := self.table(c)) and t.row(home.id)), "")
        table = self.table(comp) if comp else None
        return Match(id=0, utc_date=self.now, status="SCHEDULED", home=home, away=away,
                     competition_code=comp, competition_name=table.competition_name if table else "")

    def _standing(self, team: Team, competition: str) -> tuple[LeagueTable | None, StandingRow | None]:
        """Row in the match's competition, else in the team's domestic league."""
        order = [competition] + [c for c in self.settings.competitions if c != competition]
        for comp in order:
            if not comp:
                continue
            table = self.table(comp)
            row = table.row(team.id) if table else None
            if row:
                return table, row
        return None, None

    def _recent(self, team: Team, kickoff: datetime) -> TeamForm:
        try:
            matches = self.fd.team_matches(
                team.id, kickoff.date() - timedelta(days=FORM_LOOKBACK_DAYS), kickoff.date())
        except HttpError as e:
            log.warning("no recent matches for %s: %s", team.name, e)
            matches = []
        return compute_form(team.id, [m for m in matches if m.utc_date < kickoff], self.settings.recent_matches)

    def _absences(self, team: Team) -> list[Absence] | None:
        if not self.fpl:
            return None
        try:
            return self.fpl.absences(team)
        except (HttpError, OSError) as e:
            log.warning("FPL lookup failed for %s: %s", team.name, e)
            return None

    # ---- analysis -------------------------------------------------------------------------

    def analyze(self, match: Match) -> Prediction:
        warnings: list[str] = []
        home_table, home_row = self._standing(match.home, match.competition_code)
        away_table, away_row = self._standing(match.away, match.competition_code)
        same_table = home_table is not None and home_table is away_table
        if not same_table and (home_table or away_table):
            warnings.append("دو تیم در یک لیگ نیستند؛ مقایسه‌ی قدرت بین لیگ‌ها تقریبی است.")
        table = home_table or away_table

        home_form = self._recent(match.home, match.utc_date)
        away_form = self._recent(match.away, match.utc_date)

        h2h: list[Match] = []
        if match.id:
            try:
                h2h = [m for m in self.fd.head_to_head(match.id) if m.finished][-6:]
            except HttpError as e:
                log.warning("no head-to-head for %s: %s", match.id, e)

        home_abs = self._absences(match.home)
        away_abs = self._absences(match.away)
        if home_abs is None and away_abs is None and not self.researcher:
            warnings.append("اطلاعات مصدومان/محرومان در دسترس نیست (فقط برای لیگ برتر رایگان است؛ "
                            "برای سایر لیگ‌ها کلید ANTHROPIC_API_KEY را تنظیم کنید).")

        news: NewsReport | None = None
        if self.researcher:
            try:
                news = self.researcher.research(match)
            except Exception as e:  # research is best-effort; never block a prediction
                log.warning("news research failed: %s", e)
                warnings.append(f"تحقیق خبری ناموفق بود: {e}")
        home_abs = merge_absences(home_abs, news.home_absences if news else [])
        away_abs = merge_absences(away_abs, news.away_absences if news else [])

        xg_home, xg_away = expected_goals(
            table if same_table else None,
            home_row, away_row,
            home_table.row(match.home.id, "home") if same_table else None,
            away_table.row(match.away.id, "away") if same_table else None,
            home_form, away_form, home_abs, away_abs,
        )
        p_home, p_draw, p_away, _ = outcome_probabilities(xg_home, xg_away)
        pick, confidence = pick_outcome(p_home, p_draw, p_away)
        likely = likely_score_for(xg_home, xg_away, pick)

        prediction = Prediction(
            match=match, p_home=p_home, p_draw=p_draw, p_away=p_away,
            xg_home=xg_home, xg_away=xg_away, likely_score=likely, pick=pick, confidence=confidence,
            home_form=home_form, away_form=away_form, home_row=home_row, away_row=away_row,
            table_size=table.size if table else 0,
            home_absences=home_abs, away_absences=away_abs, h2h=h2h, news=news,
            warnings=warnings, generated_at=self.now,
        )
        prediction.factors = build_factors(prediction, same_table, home_table, away_table)
        return prediction


# ---- explanation ---------------------------------------------------------------------------

def _favor(diff: float, threshold: float) -> str:
    if diff > threshold:
        return "HOME"
    if diff < -threshold:
        return "AWAY"
    return "NEUTRAL"


def _row_text(team: Team, row: StandingRow | None, table: LeagueTable | None, show_league: bool) -> str:
    if not row:
        return f"{team.display}: در جدول نیست"
    league = f" در {table.competition_name}" if show_league and table and table.competition_name else ""
    return (f"{team.display}: رتبه {fa.num(row.position)} با {fa.num(row.points)} امتیاز "
            f"از {fa.num(row.played)} بازی{league}")


def _absence_list(team: Team, absences: list[Absence]) -> str:
    key = [a for a in absences if a.importance * a.missing_weight >= 0.25]
    if not absences:
        return f"{team.display}: غایب مهمی گزارش نشده"
    names = "، ".join(f"{a.player} ({fa.REASON.get(a.reason, a.reason)})" for a in (key or absences)[:5])
    label = "غایبان کلیدی" if key else "غایبان"
    return f"{team.display} — {label}: {names}"


def build_factors(p: Prediction, same_table: bool, home_table, away_table) -> list[Factor]:
    m = p.match
    factors: list[Factor] = []

    if p.home_row or p.away_row:
        if same_table and p.home_row and p.away_row:
            diff = (p.away_row.position - p.home_row.position) / max(p.table_size, 1) * 10
            favors = _favor(diff, 1.0)
        elif p.home_row and p.away_row:
            favors = _favor(p.home_row.ppg - p.away_row.ppg, 0.3)
        else:
            favors = "NEUTRAL"
        factors.append(Factor("جایگاه در جدول",
                              f"{_row_text(m.home, p.home_row, home_table, not same_table)} | "
                              f"{_row_text(m.away, p.away_row, away_table, not same_table)}",
                              favors))

    hf, af = p.home_form, p.away_form
    if hf and af and (hf.matches or af.matches):
        factors.append(Factor(
            "فرم اخیر (۵ بازی آخر، جدیدترین در انتها)",
            f"{m.home.display}: {fa.form_icons(hf.form_string)} ({fa.num(hf.last5_points)} امتیاز) | "
            f"{m.away.display}: {fa.form_icons(af.form_string)} ({fa.num(af.last5_points)} امتیاز)",
            _favor(hf.weighted_ppg - af.weighted_ppg, 0.35),
        ))
        factors.append(Factor(
            f"گل زده/خورده در {fa.num(max(len(hf.matches), len(af.matches)))} بازی اخیر (وزن‌دار)",
            f"{m.home.display}: {fa.dec(hf.weighted_goals_for)} زده، {fa.dec(hf.weighted_goals_against)} خورده | "
            f"{m.away.display}: {fa.dec(af.weighted_goals_for)} زده، {fa.dec(af.weighted_goals_against)} خورده",
            _favor((hf.weighted_goals_for - hf.weighted_goals_against)
                   - (af.weighted_goals_for - af.weighted_goals_against), 0.4),
        ))

    if same_table and home_table:
        hv, av = home_table.row(m.home.id, "home"), away_table.row(m.away.id, "away")
        if hv and av and hv.played and av.played:
            factors.append(Factor(
                "عملکرد در خانه / بیرون",
                f"{m.home.display} در خانه: {fa.dec(hv.ppg)} امتیاز در هر بازی | "
                f"{m.away.display} بیرون از خانه: {fa.dec(av.ppg)} امتیاز در هر بازی",
                _favor(hv.ppg - av.ppg, 0.4),
            ))

    if p.home_absences or p.away_absences:
        h_pen = sum(absence_penalties(p.home_absences))
        a_pen = sum(absence_penalties(p.away_absences))
        factors.append(Factor(
            "مصدومان و محرومان",
            f"{_absence_list(m.home, p.home_absences)} | {_absence_list(m.away, p.away_absences)}",
            _favor(a_pen - h_pen, 0.03),
        ))

    if p.h2h:
        wins = {m.home.id: 0, m.away.id: 0}
        draws = 0
        for g in p.h2h:
            if g.outcome == "DRAW":
                draws += 1
            elif g.outcome == "HOME" and g.home.id in wins:
                wins[g.home.id] += 1
            elif g.outcome == "AWAY" and g.away.id in wins:
                wins[g.away.id] += 1
        factors.append(Factor(
            f"رویارویی مستقیم ({fa.num(len(p.h2h))} دیدار اخیر؛ فقط جهت اطلاع)",
            f"{m.home.display}: {fa.num(wins[m.home.id])} برد | مساوی: {fa.num(draws)} | "
            f"{m.away.display}: {fa.num(wins[m.away.id])} برد",
            _favor(wins[m.home.id] - wins[m.away.id], 1),
        ))
    return factors
