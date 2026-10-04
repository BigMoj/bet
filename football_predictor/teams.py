"""Team-name normalisation and fuzzy lookup (English and Persian aliases)."""

from __future__ import annotations

import difflib
import re
import unicodedata
from collections.abc import Iterable

from .models import Team

_STOPWORDS = {"fc", "afc", "cf", "sc", "ac", "ssc", "as", "club", "the", "de", "calcio", "1"}

# alias -> canonical normalised name (as football-data.org spells it, minus stopwords)
ALIASES = {
    # English nicknames
    "man city": "manchester city", "city": "manchester city", "mancity": "manchester city",
    "man utd": "manchester united", "man united": "manchester united", "united": "manchester united",
    "spurs": "tottenham hotspur", "tottenham": "tottenham hotspur",
    "wolves": "wolverhampton wanderers", "forest": "nottingham forest", "villa": "aston villa",
    "newcastle": "newcastle united", "west ham": "west ham united", "brighton": "brighton hove albion",
    "barca": "barcelona", "real": "real madrid", "atleti": "atletico madrid",
    "bayern": "bayern munchen", "bayern munich": "bayern munchen", "dortmund": "borussia dortmund",
    "bvb": "borussia dortmund", "leverkusen": "bayer 04 leverkusen", "juve": "juventus",
    "inter": "internazionale milano", "inter milan": "internazionale milano", "milan": "milan",
    "psg": "paris saint-germain", "paris": "paris saint-germain", "napoli": "napoli",
    # Persian
    "منچستر سیتی": "manchester city", "من سیتی": "manchester city", "سیتی": "manchester city",
    "منچستر یونایتد": "manchester united", "من یونایتد": "manchester united", "یونایتد": "manchester united",
    "آرسنال": "arsenal", "ارسنال": "arsenal", "لیورپول": "liverpool", "چلسی": "chelsea",
    "تاتنهام": "tottenham hotspur", "نیوکاسل": "newcastle united", "استون ویلا": "aston villa",
    "وستهام": "west ham united", "برایتون": "brighton hove albion", "اورتون": "everton",
    "ناتینگهام فارست": "nottingham forest", "کریستال پالاس": "crystal palace", "فولام": "fulham",
    "برنتفورد": "brentford", "بورنموث": "bournemouth", "ولورهمپتون": "wolverhampton wanderers",
    "رئال مادرید": "real madrid", "رئال": "real madrid", "بارسلونا": "barcelona", "بارسا": "barcelona",
    "اتلتیکو مادرید": "atletico madrid", "اتلتیکو": "atletico madrid",
    "بایرن مونیخ": "bayern munchen", "بایرن": "bayern munchen", "دورتموند": "borussia dortmund",
    "لورکوزن": "bayer 04 leverkusen", "لایپزیگ": "rb leipzig",
    "یوونتوس": "juventus", "یووه": "juventus", "اینتر": "internazionale milano", "میلان": "milan",
    "ناپولی": "napoli", "رم": "roma", "آاس رم": "roma", "لاتزیو": "lazio",
    "پاری سن ژرمن": "paris saint-germain", "پی اس جی": "paris saint-germain",
    "مارسی": "olympique marseille", "موناکو": "monaco",
}


def normalize(name: str) -> str:
    text = unicodedata.normalize("NFKD", name)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower().replace("&", " ").replace("ي", "ی").replace("ك", "ک").replace("‌", " ")
    text = re.sub(r"[^\w\s-]", " ", text)
    words = [w for w in text.split() if w not in _STOPWORDS]
    return " ".join(words)


_ALIASES_NORM: dict[str, str] = {}


def _alias(query: str) -> str:
    if not _ALIASES_NORM:
        _ALIASES_NORM.update({normalize(k): normalize(v) for k, v in ALIASES.items()})
    q = normalize(query)
    return _ALIASES_NORM.get(q, q)


def _candidates(team: Team) -> set[str]:
    names = {normalize(team.name), normalize(team.short_name)}
    if team.tla:
        names.add(team.tla.lower())
    return {n for n in names if n}


def match_score(query: str, team: Team) -> float:
    q = _alias(query)
    best = 0.0
    for cand in _candidates(team):
        if q == cand:
            return 100.0
        if q and (cand.startswith(q) or q in cand.split()):
            best = max(best, 85.0 - abs(len(cand) - len(q)) * 0.5)
        elif q and q in cand:
            best = max(best, 75.0 - abs(len(cand) - len(q)) * 0.5)
        best = max(best, difflib.SequenceMatcher(None, q, cand).ratio() * 70)
    return best


def find_team(query: str, teams: Iterable[Team], threshold: float = 55.0) -> Team | None:
    scored = sorted(((match_score(query, t), t) for t in teams), key=lambda x: x[0], reverse=True)
    if scored and scored[0][0] >= threshold:
        return scored[0][1]
    return None
