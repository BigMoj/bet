from datetime import datetime, timedelta, timezone

import pytest

from updown import core
from updown.cli import main

T0 = datetime(2026, 10, 1, tzinfo=timezone.utc)


def candles(closes, tf_minutes=15, wick=0.1):
    out, prev = [], closes[0]
    for i, c in enumerate(closes):
        out.append(core.Candle(T0 + timedelta(minutes=tf_minutes * i), prev, max(prev, c) + wick,
                               min(prev, c) - wick, c, 1.0))
        prev = c
    return out


def test_rsi_extremes_and_warmup():
    up = core.rsi([float(i) for i in range(30)])
    assert up[:14] == [None] * 14 and up[-1] == 100.0
    down = core.rsi([float(30 - i) for i in range(30)])
    assert down[-1] == pytest.approx(0.0)


def test_streak_and_close_location():
    cs = candles([10, 11, 12, 13, 12.5] + [12.5 + (i % 2) for i in range(14)] + [14, 15, 16])
    ctx = core.contexts(cs)
    assert ctx[-1].streak == 3  # 14, 15 after 13.5 -> three greens before the last candle
    assert 0.75 < ctx[-1].close_location <= 1


def test_overbought_run_signals_down_and_mirror_up():
    rising = candles([100 + i for i in range(30)], wick=0.0)
    s, ctx = core.signal(rising, "15m")
    assert s == -1 and ctx.rsi > 60 and ctx.checklist() == (5, 0)
    falling = candles([100 - i for i in range(30)], wick=0.0)
    assert core.signal(falling, "15m")[0] == 1
    assert core.signal(falling, "1h")[0] == 1


def test_backtest_counts_only_signalled_candles():
    zigzag = candles([100 + i for i in range(20)] + [119, 120, 119, 120])
    b = core.backtest(zigzag, core.RULES["15m"]["main"][1])
    assert b.bets >= 1 and 0 <= b.wins <= b.bets


def test_load_csv_converts_tehran_time(tmp_path):
    f = tmp_path / "x.csv"
    f.write_text("2026.10.05,03:30,1,2,0.5,1.5,10\n2026.10.05,04:30,1.5,2,1,1.2,10\n")
    cs = core.load_csv(str(f))
    assert cs[0].time == datetime(2026, 10, 5, 0, 0, tzinfo=timezone.utc)
    assert core.drop_unfinished(cs, "1h", now=datetime(2026, 10, 5, 1, 30, tzinfo=timezone.utc)) == cs[:1]


def test_cli_signal(tmp_path, capsys):
    f = tmp_path / "x.csv"
    rows = [f"2026.10.01,{h:02d}:{m:02d},{100 + i},{101.2 + i},{99.9 + i},{101 + i},5"
            for i, (h, m) in enumerate((h, m) for h in range(8) for m in (0, 15, 30, 45))]
    f.write_text("\n".join(rows))
    main(["signal", str(f), "--tf", "15m"])
    assert "DOWN" in capsys.readouterr().out
