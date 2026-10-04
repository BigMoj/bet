"""python -m updown signal|backtest <csv> --tf 5m|15m|1h"""

from __future__ import annotations

import argparse

from . import core

ARROW = {1: "⬆️  UP", -1: "⬇️  DOWN", 0: "—  بدون سیگنال (شرط نبند)"}


def cmd_signal(candles, tf, rule):
    s, ctx = core.signal(candles, tf, rule)
    last = candles[-1]
    nxt = (last.time + core.timedelta(minutes=core.TIMEFRAMES[tf])).astimezone(core.TEHRAN)
    print(f"آخرین کندل بسته‌شده: {last.time.astimezone(core.TEHRAN):%Y-%m-%d %H:%M} (تهران)  close={last.close}")
    if ctx is None:
        print("داده کافی نیست (حداقل ۱۵ کندل لازم است).")
        return
    cl = "-" if ctx.close_location is None else f"{ctx.close_location:.2f}"
    print(f"RSI14={ctx.rsi:.1f}  محل کلوز در رنج={cl}  توالی رنگ={ctx.streak:+d}")
    down, up = ctx.checklist()
    print(f"امتیاز چک‌لیست: Down={down}/5  Up={up}/5")
    print(f"قانون: {core.RULES[tf][rule][0]}")
    print(f"کندل {nxt:%H:%M} ({tf}): {ARROW[s]}")


def cmd_backtest(candles, tf):
    print(f"{len(candles)} کندل {tf} از {candles[0].time:%Y-%m-%d} تا {candles[-1].time:%Y-%m-%d} (UTC)")
    for name, (label, rule) in core.RULES[tf].items():
        b = core.backtest(candles, rule)
        print(f"[{name}] {label}\n   شرط‌ها={b.bets}  در روز≈{b.per_day:.1f}  برد={b.win_rate:.1%}"
              f"  (نیمه‌ی اول {b.first_half:.1%} / نیمه‌ی دوم {b.second_half:.1%})")


def main(argv=None):
    p = argparse.ArgumentParser(prog="updown", description="Up/Down candle signals (SOLUSDT CSV export)")
    p.add_argument("command", choices=["signal", "backtest"])
    p.add_argument("csv", help="CSV: YYYY.MM.DD,HH:MM,open,high,low,close,volume (Tehran time)")
    p.add_argument("--tf", choices=list(core.TIMEFRAMES), required=True)
    p.add_argument("--rule", default="main", help="main (default) or the alternative listed by backtest")
    p.add_argument("--keep-last", action="store_true", help="don't drop a still-forming last candle")
    a = p.parse_args(argv)
    candles = core.load_csv(a.csv)
    if not a.keep_last:
        candles = core.drop_unfinished(candles, a.tf)
    if not candles:
        p.error("no candles in file")
    if a.rule not in core.RULES[a.tf]:
        p.error(f"rules for {a.tf}: {', '.join(core.RULES[a.tf])}")
    if a.command == "signal":
        cmd_signal(candles, a.tf, a.rule)
    else:
        cmd_backtest(candles, a.tf)
