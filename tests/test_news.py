from types import SimpleNamespace as NS

from conftest import NOW

from football_predictor.models import Match, Team
from football_predictor.sources import news


class FakeStream:
    def __init__(self, message):
        self.message = message

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self.message


class FakeMessages:
    def __init__(self):
        self.stream_calls = []
        self.replies = [
            NS(stop_reason="pause_turn", content=[
                NS(type="web_search_tool_result", content=[NS(url="https://bbc.co.uk/a"), NS(url="https://bbc.co.uk/a")]),
                NS(type="text", text="Rodri is out with a knee injury. "),
            ]),
            NS(stop_reason="end_turn", content=[NS(type="text", text="Saliba is suspended.")]),
        ]

    def stream(self, **kwargs):
        self.stream_calls.append(kwargs)
        return FakeStream(self.replies.pop(0))

    def parse(self, **kwargs):
        assert "Rodri is out" in kwargs["messages"][0]["content"]
        return NS(stop_reason="end_turn", parsed_output=news._Briefing(
            home_absences=[news._Player(name="Rodri", reason="injury", position="MID", role="key", detail="knee")],
            away_absences=[news._Player(name="Saliba", reason="suspension", position="DEF",
                                        role="regular", detail="red card")],
            notes=["City played on Wednesday"],
            summary_fa="خلاصه",
        ))


def make_researcher():
    r = news.ClaudeResearcher.__new__(news.ClaudeResearcher)
    r.client = NS(beta=NS(messages=FakeMessages()))
    r.model = "claude-opus-5-5"
    r.cache_seconds = 3600
    r._cache = {}
    return r


def test_research_resumes_pause_and_structures_output():
    r = make_researcher()
    match = Match(500, NOW, "TIMED", Team(65, "Manchester City FC"), Team(57, "Arsenal FC"), "PL", "Premier League")
    report = r.research(match)
    calls = r.client.beta.messages.stream_calls
    assert len(calls) == 2  # resumed after pause_turn
    assert calls[0]["tools"][0]["type"] == "web_search_20260209"
    assert calls[0]["fallbacks"] == "default"
    assert [a.player for a in report.home_absences] == ["Rodri"]
    assert report.home_absences[0].importance == news.IMPORTANCE["key"]
    assert report.away_absences[0].reason == "suspension"
    assert report.sources == ["https://bbc.co.uk/a"]
    # cached per match: no further API calls
    assert r.research(match) is report
    assert len(calls) == 2
