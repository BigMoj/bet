"""SQLite history of predictions, so accuracy can be measured once results are in."""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .models import Match, Prediction

SCHEMA = """
CREATE TABLE IF NOT EXISTS predictions (
    match_id      INTEGER PRIMARY KEY,
    competition   TEXT,
    kickoff       TEXT,
    home          TEXT,
    away          TEXT,
    p_home        REAL,
    p_draw        REAL,
    p_away        REAL,
    xg_home       REAL,
    xg_away       REAL,
    pick          TEXT,
    confidence    TEXT,
    likely_score  TEXT,
    updated_at    TEXT,
    home_goals    INTEGER,
    away_goals    INTEGER,
    outcome       TEXT
)
"""


@dataclass
class AccuracyReport:
    total: int
    correct: int
    brier: float
    log_loss: float
    by_confidence: dict[str, tuple[int, int]]

    @property
    def hit_rate(self) -> float:
        return self.correct / self.total if self.total else 0.0


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(SCHEMA)

    def save(self, p: Prediction) -> None:
        """Insert or refresh a prediction. Predictions are frozen once the match has started."""
        if not p.match.id or p.match.started:
            return
        m = p.match
        self.conn.execute(
            """INSERT INTO predictions (match_id, competition, kickoff, home, away, p_home, p_draw, p_away,
                   xg_home, xg_away, pick, confidence, likely_score, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(match_id) DO UPDATE SET
                   p_home=excluded.p_home, p_draw=excluded.p_draw, p_away=excluded.p_away,
                   xg_home=excluded.xg_home, xg_away=excluded.xg_away, pick=excluded.pick,
                   confidence=excluded.confidence, likely_score=excluded.likely_score,
                   kickoff=excluded.kickoff, updated_at=excluded.updated_at
               WHERE predictions.outcome IS NULL""",
            (m.id, m.competition_code, m.utc_date.isoformat(), m.home.name, m.away.name,
             p.p_home, p.p_draw, p.p_away, p.xg_home, p.xg_away, p.pick, p.confidence,
             f"{p.likely_score[0]}-{p.likely_score[1]}", datetime.now(timezone.utc).isoformat()),
        )
        self.conn.commit()

    def pending_results(self, now: datetime) -> list[int]:
        """Match ids that should have finished (kick-off > 2.5h ago) but have no result stored."""
        rows = self.conn.execute("SELECT match_id, kickoff FROM predictions WHERE outcome IS NULL").fetchall()
        return [r["match_id"] for r in rows
                if (now - datetime.fromisoformat(r["kickoff"])).total_seconds() > 2.5 * 3600]

    def record_result(self, match: Match) -> None:
        if not match.finished:
            return
        self.conn.execute(
            "UPDATE predictions SET home_goals=?, away_goals=?, outcome=? WHERE match_id=?",
            (match.home_goals, match.away_goals, match.outcome, match.id),
        )
        self.conn.commit()

    def accuracy(self) -> AccuracyReport:
        rows = self.conn.execute("SELECT * FROM predictions WHERE outcome IS NOT NULL").fetchall()
        brier = log_loss = 0.0
        correct = 0
        by_conf: dict[str, list[int]] = {}
        for r in rows:
            probs = {"HOME": r["p_home"], "DRAW": r["p_draw"], "AWAY": r["p_away"]}
            hit = r["pick"] == r["outcome"]
            correct += hit
            bucket = by_conf.setdefault(r["confidence"], [0, 0])
            bucket[0] += 1
            bucket[1] += hit
            brier += sum((probs[k] - (k == r["outcome"])) ** 2 for k in probs)
            log_loss -= math.log(max(probs[r["outcome"]], 1e-9))
        n = len(rows)
        return AccuracyReport(
            total=n, correct=correct,
            brier=brier / n if n else 0.0, log_loss=log_loss / n if n else 0.0,
            by_confidence={k: (v[0], v[1]) for k, v in by_conf.items()},
        )
