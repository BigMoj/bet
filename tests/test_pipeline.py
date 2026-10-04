from datetime import timedelta

from conftest import FIXTURE_ID, NOW

from football_predictor import report
from football_predictor.cli import run_cycle, update_results
from football_predictor.models import Absence, NewsReport
from football_predictor.storage import Store
from football_predictor.teams import find_team


def test_standings_and_form_parsing(analyzer):
    table = analyzer.table("PL")
    assert table.competition_name == "Premier League"
    assert table.row(57).position == 1 and table.row(57).points == 16
    assert table.row(57, "home") is not None and table.row(57, "away") is not None
    form = analyzer._recent(analyzer.resolve("Arsenal"), NOW)
    assert form.form_string == "WWWDW"
    assert form.last5_points == 13


def test_persian_and_nickname_lookup(analyzer):
    teams = analyzer.known_teams()
    assert find_team("منچستر سیتی", teams).id == 65
    assert find_team("آرسنال", teams).id == 57
    assert find_team("forest", teams).id == 351
    assert find_team("Real Madrid", teams) is None


def test_find_fixture_in_either_order(analyzer):
    assert analyzer.find_fixture("Arsenal", "Man City").id == FIXTURE_ID
    assert analyzer.find_fixture("منچستر سیتی", "آرسنال").id == FIXTURE_ID
    assert analyzer.find_fixture("Arsenal", "Chelsea") is None


def test_fpl_absences(analyzer):
    city = analyzer.resolve("Man City")
    absences = analyzer.fpl.absences(city)
    names = [a.player for a in absences]
    assert names[0] == "Rodri"  # most important missing player first
    assert "Haaland" not in names
    rodri = absences[0]
    assert rodri.reason == "injury" and rodri.position == "MID" and rodri.missing_weight == 1.0
    foden = next(a for a in absences if a.player == "Foden")
    assert foden.missing_weight == 0.5
    arsenal = analyzer.fpl.absences(analyzer.resolve("Arsenal"))
    assert [a.player for a in arsenal] == ["Saliba"]  # departed ('u') players are ignored
    # Nottingham Forest: football-data TLA "NOT" maps to FPL "NFO"
    assert analyzer.fpl.find_team_id(analyzer.resolve("Nottingham Forest")) == 16


def test_analyze_fixture(analyzer):
    match = analyzer.find_fixture("Man City", "Arsenal")
    p = analyzer.analyze(match)
    assert abs(p.p_home + p.p_draw + p.p_away - 1) < 1e-9
    assert p.pick in {"HOME", "DRAW", "AWAY"}
    titles = [f.title for f in p.factors]
    assert any("جدول" in t for t in titles)
    assert any("فرم" in t for t in titles)
    assert any("مصدوم" in t for t in titles)
    assert any("رویارویی" in t for t in titles)
    assert len(p.h2h) == 2
    text = report.to_text(p)
    assert "Man City" in text and "پیش‌بینی" in text and "Rodri" in text


def test_weak_team_is_underdog(analyzer):
    p = analyzer.analyze(analyzer.hypothetical("Nottingham Forest", "Arsenal"))
    assert p.match.id == 0
    assert p.pick == "AWAY"
    assert p.p_away > 0.5


class FakeResearcher:
    def research(self, match):
        return NewsReport(
            home_absences=[Absence("Rodri", "injury", importance=0.85, source="news"),
                           Absence("Ruben Dias", "suspension", position="DEF", importance=0.85, source="news")],
            notes=["City played midweek in the Champions League"],
            summary="خلاصه‌ی خبری",
        )


def test_news_merges_with_fpl(analyzer):
    analyzer.researcher = FakeResearcher()
    p = analyzer.analyze(analyzer.find_fixture("Man City", "Arsenal"))
    names = [a.player for a in p.home_absences]
    assert names.count("Rodri") == 1  # deduplicated against FPL
    assert "Ruben Dias" in names
    assert "خلاصه‌ی خبری" in report.to_text(p)


def test_researcher_failure_does_not_block(analyzer):
    class Broken:
        def research(self, match):
            raise RuntimeError("boom")
    analyzer.researcher = Broken()
    p = analyzer.analyze(analyzer.find_fixture("Man City", "Arsenal"))
    assert any("boom" in w for w in p.warnings)


def test_cycle_stores_and_scores(analyzer, settings):
    store = Store(settings.db_path)
    predictions = run_cycle(analyzer, store, settings, verbose=False)
    assert {p.match.id for p in predictions} == {FIXTURE_ID, 501}
    html = settings.dashboard_path.read_text(encoding="utf-8")
    assert 'dir="rtl"' in html and "Man City" in html

    # After kick-off the stored result is fetched and scored.
    later = NOW + timedelta(hours=10)
    assert FIXTURE_ID in store.pending_results(later)
    import football_predictor.cli as cli_mod
    original = cli_mod.datetime

    class FrozenDateTime(original):
        @classmethod
        def now(cls, tz=None):
            return later
    cli_mod.datetime = FrozenDateTime
    try:
        assert update_results(analyzer, store) >= 1
    finally:
        cli_mod.datetime = original
    acc = store.accuracy()
    assert acc.total >= 1
    assert "Brier" in report.accuracy_text(acc)


def test_prediction_frozen_after_result(analyzer, settings):
    store = Store(settings.db_path)
    p = analyzer.analyze(analyzer.find_fixture("Man City", "Arsenal"))
    store.save(p)
    m = analyzer.fd.match(FIXTURE_ID)
    store.record_result(m)
    p.p_home = 0.99
    store.save(p)
    row = store.conn.execute("SELECT p_home FROM predictions WHERE match_id=?", (FIXTURE_ID,)).fetchone()
    assert row["p_home"] != 0.99
