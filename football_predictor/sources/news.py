"""Optional live news research with Claude + web search.

Step 1 lets Claude search the web for the latest team news (injuries, suspensions, expected
line-ups, rotation, manager comments). Step 2 turns that free-text briefing into structured data.
Requires the `anthropic` package and an ANTHROPIC_API_KEY (or an `ant auth login` profile).
"""

from __future__ import annotations

import logging
import time
from typing import Literal

from pydantic import BaseModel, Field

from ..models import Absence, Match, NewsReport

log = logging.getLogger(__name__)

IMPORTANCE = {"key": 0.85, "regular": 0.5, "squad": 0.15}
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class _Player(BaseModel):
    name: str
    reason: Literal["injury", "suspension", "doubtful", "unavailable"]
    position: Literal["GK", "DEF", "MID", "FWD", "unknown"]
    role: Literal["key", "regular", "squad"] = Field(description="key = star/regular starter, squad = rarely plays")
    detail: str = Field(description="One short sentence, e.g. 'hamstring, out until November'")


class _Briefing(BaseModel):
    home_absences: list[_Player]
    away_absences: list[_Player]
    notes: list[str] = Field(description="Other relevant facts: rotation, new manager, fixture congestion, motivation")
    summary_fa: str = Field(description="3-5 sentence summary of the team news, written in Persian (Farsi)")


RESEARCH_PROMPT = """You are a football analyst preparing a pre-match team-news briefing.

Match: {home} (home) vs {away} (away)
Competition: {competition}
Kick-off (UTC): {kickoff}

Search the web for the most recent, reliable information (club sites, BBC, Sky Sports, ESPN,
The Athletic, Marca, Kicker, Gazzetta, L'Equipe, reputable beat reporters). Report:
1. Every player confirmed or likely to miss this match for each team (injury, suspension,
   international duty, illness, personal reasons), plus doubtful players, with how central each
   player is to the team.
2. Anything else that changes the teams' strength for this specific match: expected rotation,
   manager changes, fixture congestion, motivation, weather.
Prefer news from the last 7 days; ignore players who have already returned to fitness.
Be factual and say when something is only a rumour. Keep the briefing concise."""

EXTRACT_PROMPT = """Convert this pre-match briefing into the requested structure.
Home team: {home}. Away team: {away}.
Only include players the briefing says are out or doubtful for this match.

Briefing:
{briefing}"""


class ClaudeResearcher:
    def __init__(self, model: str, cache_hours: float = 6):
        import anthropic  # optional dependency

        self.client = anthropic.Anthropic()
        self.model = model
        self.cache_seconds = cache_hours * 3600
        self._cache: dict[int, tuple[float, NewsReport]] = {}

    def _research(self, match: Match) -> tuple[str, list[str]]:
        prompt = RESEARCH_PROMPT.format(
            home=match.home.name, away=match.away.name,
            competition=match.competition_name or match.competition_code or "unknown",
            kickoff=match.utc_date.strftime("%Y-%m-%d %H:%M"),
        )
        messages = [{"role": "user", "content": prompt}]
        text_parts: list[str] = []
        sources: list[str] = []
        for _ in range(4):  # resume server-side pause_turn a few times at most
            with self.client.beta.messages.stream(
                model=self.model,
                max_tokens=16000,
                thinking={"type": "adaptive"},
                output_config={"effort": "medium"},
                tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": 8}],
                betas=[FALLBACK_BETA],
                fallbacks="default",
                messages=messages,
            ) as stream:
                response = stream.get_final_message()
            if response.stop_reason == "refusal":
                raise RuntimeError("news research request was declined")
            for block in response.content:
                if block.type == "text":
                    text_parts.append(block.text)
                elif block.type == "web_search_tool_result" and isinstance(block.content, list):
                    sources.extend(r.url for r in block.content if getattr(r, "url", None))
            if response.stop_reason != "pause_turn":
                break
            messages = [messages[0], {"role": "assistant", "content": response.content}]
        return "".join(text_parts).strip(), list(dict.fromkeys(sources))

    def _extract(self, match: Match, briefing: str) -> _Briefing:
        response = self.client.beta.messages.parse(
            model=self.model,
            max_tokens=8000,
            output_config={"effort": "low"},
            betas=[FALLBACK_BETA],
            fallbacks="default",
            output_format=_Briefing,
            messages=[{"role": "user", "content": EXTRACT_PROMPT.format(
                home=match.home.name, away=match.away.name, briefing=briefing)}],
        )
        if response.stop_reason == "refusal" or response.parsed_output is None:
            raise RuntimeError(f"could not structure news briefing (stop_reason={response.stop_reason})")
        return response.parsed_output

    def research(self, match: Match) -> NewsReport:
        """News for a match; cached per match so the watch loop doesn't re-search every cycle."""
        cached = self._cache.get(match.id)
        if match.id and cached and time.time() - cached[0] < self.cache_seconds:
            return cached[1]
        report = self._research_uncached(match)
        if match.id:
            self._cache[match.id] = (time.time(), report)
        return report

    def _research_uncached(self, match: Match) -> NewsReport:
        briefing, sources = self._research(match)
        if not briefing:
            return NewsReport(sources=sources)
        data = self._extract(match, briefing)

        def convert(players: list[_Player]) -> list[Absence]:
            return [Absence(
                player=p.name,
                reason=p.reason,
                detail=p.detail,
                position=None if p.position == "unknown" else p.position,
                importance=IMPORTANCE[p.role],
                source="news",
            ) for p in players]

        return NewsReport(
            home_absences=convert(data.home_absences),
            away_absences=convert(data.away_absences),
            notes=data.notes,
            summary=data.summary_fa,
            sources=sources[:10],
        )
