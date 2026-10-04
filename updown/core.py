"""Mean-reversion signals for 5m / 15m / 1h "Up or Down" candle markets.

Every signal uses only information known when the candle opens: RSI(14) of the
previous close, where the previous candle closed inside its high-low range, and
how many same-coloured candles came before it.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

TEHRAN = timezone(timedelta(hours=3, minutes=30))
TIMEFRAMES = {"5m": 5, "15m": 15, "1h": 60}


@dataclass(frozen=True)
class Candle:
    time: datetime  # open time, UTC
    open: float
    high: float
    low: float
    close: float
    volume: float

    @property
    def direction(self) -> int:
        return (self.close > self.open) - (self.close < self.open)

    @property
    def close_location(self) -> float | None:
        """0 = closed at the low, 1 = closed at the high."""
        rng = self.high - self.low
        return (self.close - self.low) / rng if rng > 0 else None


def load_csv(path: str, tz: timezone = TEHRAN) -> list[Candle]:
    """Read `YYYY.MM.DD,HH:MM,open,high,low,close,volume` rows (no header)."""
    out = []
    with open(path, newline="") as f:
        for row in csv.reader(f):
            if len(row) < 7 or not row[0][:1].isdigit():
                continue
            t = datetime.strptime(f"{row[0]} {row[1]}", "%Y.%m.%d %H:%M").replace(tzinfo=tz)
            o, h, lo, c, v = map(float, row[2:7])
            out.append(Candle(t.astimezone(timezone.utc), o, h, lo, c, v))
    return out


def drop_unfinished(candles: list[Candle], tf: str, now: datetime | None = None) -> list[Candle]:
    now = now or datetime.now(timezone.utc)
    end = timedelta(minutes=TIMEFRAMES[tf])
    while candles and candles[-1].time + end > now:
        candles = candles[:-1]
    return candles


def rsi(closes: list[float], n: int = 14) -> list[float | None]:
    """Wilder RSI (same as TradingView's default RSI 14)."""
    out: list[float | None] = [None] * len(closes)
    gain = loss = 0.0
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        g, l = max(d, 0.0), max(-d, 0.0)
        if i <= n:
            gain += g / n
            loss += l / n
            if i < n:
                continue
        else:
            gain += (g - gain) / n
            loss += (l - loss) / n
        out[i] = 100.0 if loss == 0 else 100 - 100 / (1 + gain / loss)
    return out


@dataclass(frozen=True)
class Context:
    """What is known at the open of the next candle."""
    rsi: float
    close_location: float | None
    streak: int  # +k: last k candles green, -k: last k candles red

    def checklist(self) -> tuple[int, int]:
        """(points for Down, points for Up); 0-5 each, only one side is ever non-zero."""
        cl = self.close_location
        down = (self.rsi > 60) + (self.rsi > 70) + (cl is not None and cl > 0.75) + (self.streak >= 2) + (self.streak >= 4)
        up = (self.rsi < 40) + (self.rsi < 30) + (cl is not None and cl < 0.25) + (self.streak <= -2) + (self.streak <= -4)
        return (down, 0) if down and not up else (0, up) if up and not down else (0, 0)


def contexts(candles: list[Candle]) -> list[Context | None]:
    """contexts(c)[i] describes the market just before candle i opens."""
    rs = rsi([c.close for c in candles])
    out: list[Context | None] = [None]
    streak = 0
    for i, prev in enumerate(candles[:-1]):
        s = prev.direction
        streak = (streak + s if streak * s > 0 else s)
        out.append(Context(rs[i], prev.close_location, streak) if rs[i] is not None else None)
    return out


def _rsi_and_close(lo: float, hi: float):
    def rule(x: Context) -> int:
        cl = x.close_location
        if cl is None:
            return 0
        if x.rsi > hi and cl > 0.75:
            return -1
        if x.rsi < lo and cl < 0.25:
            return 1
        return 0
    return rule


def _streak_and_rsi(k: int, lo: float, hi: float):
    def rule(x: Context) -> int:
        if x.streak >= k and x.rsi > hi:
            return -1
        if x.streak <= -k and x.rsi < lo:
            return 1
        return 0
    return rule


def _streak(k: int):
    return lambda x: -1 if x.streak >= k else 1 if x.streak <= -k else 0


# Rules that held up on both halves of 2024-12 .. 2026-10 SOLUSDT data (see README).
RULES = {
    "5m": {"main": ("RSI>65 & close in top 25% → Down (mirror for Up)", _rsi_and_close(35, 65)),
           "more": ("RSI>60 & close in top 25% → Down (mirror for Up)", _rsi_and_close(40, 60))},
    "15m": {"main": ("RSI>60 & close in top 25% → Down (mirror for Up)", _rsi_and_close(40, 60)),
            "strict": ("RSI>65 & close in top 25% → Down (mirror for Up)", _rsi_and_close(35, 65))},
    "1h": {"main": ("3+ green candles & RSI>65 → Down (mirror for Up)", _streak_and_rsi(3, 35, 65)),
           "more": ("3+ same-colour candles → bet the other colour", _streak(3))},
}


def signal(candles: list[Candle], tf: str, rule: str = "main") -> tuple[int, Context | None]:
    """Signal for the candle that opens right after the last (closed) candle."""
    ctx = contexts(candles + [candles[-1]])[-1]
    return (RULES[tf][rule][1](ctx) if ctx else 0), ctx


@dataclass
class Backtest:
    bets: int
    wins: int
    days: float
    first_half: float
    second_half: float

    @property
    def win_rate(self) -> float:
        return self.wins / self.bets if self.bets else 0.0

    @property
    def per_day(self) -> float:
        return self.bets / self.days if self.days else 0.0


def backtest(candles: list[Candle], rule) -> Backtest:
    """Flat candles (close == open) are skipped: the oracle price almost never ties."""
    results = []
    for c, ctx in zip(candles, contexts(candles)):
        if ctx is None or c.direction == 0:
            continue
        s = rule(ctx)
        if s:
            results.append(s == c.direction)
    half = len(results) // 2
    rate = lambda r: sum(r) / len(r) if r else 0.0  # noqa: E731
    days = (candles[-1].time - candles[0].time).total_seconds() / 86400 if candles else 0
    return Backtest(len(results), sum(results), days, rate(results[:half]), rate(results[half:]))
