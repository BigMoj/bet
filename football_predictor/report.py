"""Render predictions as terminal text and as an RTL HTML dashboard."""

from __future__ import annotations

import html
from datetime import datetime, timedelta, timezone

from . import fa
from .models import Absence, Factor, Prediction
from .storage import AccuracyReport

ARROW = {"HOME": "⬅", "AWAY": "➡", "NEUTRAL": "⚖"}
TEHRAN = timezone(timedelta(hours=3, minutes=30))


def kickoff_text(dt: datetime) -> str:
    local = dt.astimezone(TEHRAN)
    return f"{fa.num(local.strftime('%Y-%m-%d %H:%M'))} (به وقت تهران)"


def verdict(p: Prediction) -> str:
    m = p.match
    score = (f"{m.home.display} {fa.num(p.likely_score[0])} گل، "
             f"{m.away.display} {fa.num(p.likely_score[1])} گل")
    if p.pick == "DRAW":
        head = f"پیش‌بینی: تساوی ({fa.pct(p.p_draw)})"
    else:
        head = f"پیش‌بینی: برد {p.pick_team.display} ({fa.pct(p.pick_probability)})"
    return (f"{head} — نتیجه‌ی محتمل: {score} — "
            f"اطمینان: {fa.CONFIDENCE[p.confidence]}")


def favors_label(p: Prediction, f: Factor) -> str:
    if f.favors == "HOME":
        return f"به نفع {p.match.home.display}"
    if f.favors == "AWAY":
        return f"به نفع {p.match.away.display}"
    return "متعادل"


def _absence_line(a: Absence) -> str:
    pos = fa.POSITION.get(a.position, "")
    chance = f"، شانس بازی {fa.pct(a.chance_of_playing)}" if a.chance_of_playing is not None else ""
    detail = f" — {a.detail}" if a.detail else ""
    return f"{a.player} ({fa.REASON.get(a.reason, a.reason)}{'، ' + pos if pos else ''}{chance}){detail}"


def notable(absences: list[Absence], limit: int = 8) -> list[Absence]:
    """Players worth listing: anyone from the news, or FPL players who actually feature."""
    return [a for a in absences if a.source == "news" or a.importance >= 0.1][:limit]


def to_text(p: Prediction) -> str:
    m = p.match
    lines = [
        "═" * 60,
        f"⚽ {m.home.display} 🆚 {m.away.display}",
        f"{m.competition_name or m.competition_code} | {kickoff_text(m.utc_date)}" if m.id
        else "(بازی فرضی — در برنامه‌ی پیش رو پیدا نشد؛ تیم اول میزبان فرض شد)",
        "─" * 60,
        f"احتمالات: {m.home.display} {fa.pct(p.p_home)} | مساوی {fa.pct(p.p_draw)} | "
        f"{m.away.display} {fa.pct(p.p_away)}",
        f"گل مورد انتظار (xG مدل): {m.home.display} {fa.dec(p.xg_home, 2)} — "
        f"{m.away.display} {fa.dec(p.xg_away, 2)}",
        "",
        "عوامل بررسی‌شده:",
    ]
    for f in p.factors:
        lines.append(f"  • {f.title} [{favors_label(p, f)}]")
        lines.append(f"    {f.detail}")
    for team, absences in ((m.home, notable(p.home_absences)), (m.away, notable(p.away_absences))):
        if absences:
            lines.append(f"\nغایبان {team.display}:")
            lines.extend(f"  - {_absence_line(a)}" for a in absences)
    if p.news and (p.news.summary or p.news.notes):
        lines.append("\nاخبار تیم‌ها:")
        if p.news.summary:
            lines.append(f"  {p.news.summary}")
        lines.extend(f"  - {n}" for n in p.news.notes)
    for w in p.warnings:
        lines.append(f"⚠ {w}")
    lines += ["─" * 60, f"🏆 {verdict(p)}", "═" * 60]
    return "\n".join(lines)


def to_summary_row(p: Prediction) -> str:
    m = p.match
    pick = "مساوی" if p.pick == "DRAW" else p.pick_team.display
    return (f"{kickoff_text(m.utc_date)} | {m.home.display} - {m.away.display} | "
            f"{fa.pct(p.p_home)}/{fa.pct(p.p_draw)}/{fa.pct(p.p_away)} | ⇐ {pick} "
            f"({fa.CONFIDENCE[p.confidence]})")


def accuracy_text(r: AccuracyReport) -> str:
    if not r.total:
        return "هنوز نتیجه‌ی هیچ بازی پیش‌بینی‌شده‌ای ثبت نشده است."
    lines = [
        f"تعداد بازی‌های ارزیابی‌شده: {fa.num(r.total)}",
        f"پیش‌بینی درست: {fa.num(r.correct)} ({fa.pct(r.hit_rate)})",
        f"Brier score: {fa.dec(r.brier, 3)} (کمتر بهتر است؛ حدس تصادفی ≈ ۰٫۶۷)",
        f"Log loss: {fa.dec(r.log_loss, 3)} (حدس تصادفی ≈ ۱٫۱۰)",
    ]
    for conf in ("high", "medium", "low"):
        if conf in r.by_confidence:
            n, hit = r.by_confidence[conf]
            lines.append(f"  اطمینان {fa.CONFIDENCE[conf]}: {fa.num(hit)} از {fa.num(n)} ({fa.pct(hit / n)})")
    return "\n".join(lines)


# ---- HTML ------------------------------------------------------------------------------------

CSS = """
:root{--bg:#f6f7f9;--card:#fff;--text:#1d2330;--muted:#667085;--border:#e4e7ec;
--home:#2563eb;--draw:#98a2b3;--away:#dc2626;--good:#16a34a;--warn:#b45309}
@media (prefers-color-scheme:dark){:root{--bg:#0f1218;--card:#181c24;--text:#e6e8ec;--muted:#98a2b3;
--border:#2a303b;--home:#60a5fa;--draw:#667085;--away:#f87171;--good:#4ade80;--warn:#fbbf24}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);
font-family:Vazirmatn,Tahoma,system-ui,sans-serif;line-height:1.7}
main{max-width:960px;margin:0 auto;padding:16px}h1{font-size:1.4rem;margin:8px 0}
.meta{color:var(--muted);font-size:.85rem}
.card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:16px;margin:16px 0}
.teams{display:flex;justify-content:space-between;align-items:center;gap:8px;font-weight:700;font-size:1.1rem}
.teams img{width:28px;height:28px;object-fit:contain;vertical-align:middle;margin:0 6px}
.bar{display:flex;height:28px;border-radius:8px;overflow:hidden;margin:12px 0 4px}
.bar div{display:flex;align-items:center;justify-content:center;color:#fff;font-size:.8rem;min-width:0}
.legend{display:flex;justify-content:space-between;font-size:.8rem;color:var(--muted)}
.verdict{font-weight:700;margin-top:10px;padding:10px;border-radius:8px;background:var(--bg)}
details{margin-top:10px}summary{cursor:pointer;color:var(--muted)}
ul{padding-inline-start:20px;margin:6px 0}li{margin:4px 0}
.tag{display:inline-block;font-size:.75rem;padding:0 8px;border-radius:999px;border:1px solid var(--border);color:var(--muted)}
.warn{color:var(--warn);font-size:.85rem}
"""


def _e(s) -> str:
    return html.escape(str(s))


def _card(p: Prediction) -> str:
    m = p.match
    crest = lambda t: f'<img src="{_e(t.crest)}" alt="" onerror="this.remove()">' if t.crest else ""  # noqa: E731
    seg = lambda prob, color, label: (  # noqa: E731
        f'<div style="width:{prob * 100:.1f}%;background:var(--{color})" title="{_e(label)}">'
        f'{fa.pct(prob) if prob >= 0.08 else ""}</div>')
    factors = "".join(
        f"<li><b>{_e(f.title)}</b> <span class='tag'>{_e(favors_label(p, f))}</span><br>{_e(f.detail)}</li>"
        for f in p.factors)
    absences = ""
    for team, items in ((m.home, notable(p.home_absences)), (m.away, notable(p.away_absences))):
        if items:
            absences += f"<p><b>غایبان {_e(team.display)}</b></p><ul>" + "".join(
                f"<li>{_e(_absence_line(a))}</li>" for a in items) + "</ul>"
    news = ""
    if p.news and (p.news.summary or p.news.notes):
        news = (f"<p><b>اخبار</b></p><p>{_e(p.news.summary)}</p><ul>"
                + "".join(f"<li>{_e(n)}</li>" for n in p.news.notes) + "</ul>")
        if p.news.sources:
            news += "<p class='meta'>منابع: " + " · ".join(
                f'<a href="{_e(u)}" target="_blank" rel="noopener">{i + 1}</a>'
                for i, u in enumerate(p.news.sources)) + "</p>"
    warnings = "".join(f"<p class='warn'>⚠ {_e(w)}</p>" for w in p.warnings)
    return f"""
<section class="card">
  <div class="meta">{_e(m.competition_name)} · {_e(kickoff_text(m.utc_date))}</div>
  <div class="teams"><span>{crest(m.home)}{_e(m.home.display)}</span><span class="meta">در برابر</span>
    <span>{_e(m.away.display)}{crest(m.away)}</span></div>
  <div class="bar">{seg(p.p_home, 'home', m.home.display)}{seg(p.p_draw, 'draw', 'مساوی')}{seg(p.p_away, 'away', m.away.display)}</div>
  <div class="legend"><span>برد {_e(m.home.display)} {fa.pct(p.p_home)}</span><span>مساوی {fa.pct(p.p_draw)}</span>
    <span>برد {_e(m.away.display)} {fa.pct(p.p_away)}</span></div>
  <div class="verdict">🏆 {_e(verdict(p))}</div>
  <details><summary>جزئیات تحلیل</summary>
    <p class="meta">گل مورد انتظار: {_e(m.home.display)} {fa.dec(p.xg_home, 2)} — {_e(m.away.display)} {fa.dec(p.xg_away, 2)}</p>
    <ul>{factors}</ul>{absences}{news}{warnings}
  </details>
</section>"""


def to_html(predictions: list[Prediction], accuracy: AccuracyReport | None = None,
            refresh_seconds: int = 300) -> str:
    updated = kickoff_text(datetime.now(timezone.utc))
    cards = "".join(_card(p) for p in predictions) or "<p class='card'>بازی‌ای در بازه‌ی انتخابی پیدا نشد.</p>"
    acc = ""
    if accuracy and accuracy.total:
        acc = (f"<p class='meta'>دقت تاکنون: {fa.num(accuracy.correct)} از {fa.num(accuracy.total)} "
               f"({fa.pct(accuracy.hit_rate)}) · Brier {fa.dec(accuracy.brier, 3)}</p>")
    return f"""<!doctype html>
<html lang="fa" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="{refresh_seconds}">
<title>پیش‌بینی فوتبال</title>
<link href="https://fonts.googleapis.com/css2?family=Vazirmatn:wght@400;700&display=swap" rel="stylesheet">
<style>{CSS}</style></head>
<body><main>
<h1>⚽ پیش‌بینی بازی‌های پیش رو</h1>
<p class="meta">آخرین به‌روزرسانی: {_e(updated)}</p>{acc}
{cards}
<p class="meta">این پیش‌بینی‌ها از یک مدل آماری بر پایه‌ی داده‌های عمومی ساخته شده‌اند و احتمالی‌اند، نه قطعی.</p>
</main></body></html>"""
