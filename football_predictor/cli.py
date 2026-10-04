"""Command-line interface: fp predict | upcoming | watch | serve | accuracy."""

from __future__ import annotations

import argparse
import functools
import http.server
import logging
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

from . import report
from .config import Settings
from .http import CachedHttp, HttpError
from .predictor import Analyzer, TeamNotFound
from .sources.football_data import FootballDataClient
from .sources.fpl import FplClient
from .storage import Store

log = logging.getLogger("football_predictor")

NO_KEY_HELP = """کلید API تنظیم نشده است.
۱. در https://www.football-data.org/client/register ثبت‌نام کنید (رایگان) و کلید را از ایمیل بردارید.
۲. فایل .env را از روی .env.example بسازید و مقدار FOOTBALL_DATA_API_KEY را پر کنید."""


def build(settings: Settings, news: bool = True) -> tuple[Analyzer, Store]:
    if not settings.football_data_key:
        sys.exit(NO_KEY_HELP)
    http_client = CachedHttp(settings.cache_dir)
    researcher = None
    if news and settings.news_enabled():
        try:
            from .sources.news import ClaudeResearcher
            researcher = ClaudeResearcher(settings.claude_model)
        except ImportError:
            log.warning("anthropic package not installed; news research disabled (pip install anthropic)")
        except Exception as e:  # e.g. no credentials configured
            log.warning("news research disabled: %s", e)
    analyzer = Analyzer(settings, FootballDataClient(settings.football_data_key, http_client),
                        FplClient(http_client), researcher)
    return analyzer, Store(settings.db_path)


def update_results(analyzer: Analyzer, store: Store) -> int:
    done = 0
    for match_id in store.pending_results(datetime.now(timezone.utc)):
        try:
            match = analyzer.fd.match(match_id)
        except HttpError as e:
            log.warning("could not fetch result for %s: %s", match_id, e)
            continue
        if match.finished:
            store.record_result(match)
            done += 1
    return done


def run_cycle(analyzer: Analyzer, store: Store, settings: Settings, days: int | None = None,
              competitions: list[str] | None = None, verbose: bool = True):
    updated = update_results(analyzer, store)
    if updated:
        log.info("recorded %d new results", updated)
    analyzer.refresh()
    predictions = []
    for match in analyzer.upcoming(days=days, competitions=competitions):
        try:
            prediction = analyzer.analyze(match)
        except Exception:
            log.exception("analysis failed for %s vs %s", match.home.name, match.away.name)
            continue
        store.save(prediction)
        predictions.append(prediction)
        if verbose:
            print(report.to_summary_row(prediction), flush=True)
    settings.dashboard_path.parent.mkdir(parents=True, exist_ok=True)
    settings.dashboard_path.write_text(report.to_html(predictions, store.accuracy()), encoding="utf-8")
    return predictions


def serve(settings: Settings, port: int) -> http.server.ThreadingHTTPServer:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    if not settings.dashboard_path.exists():
        settings.dashboard_path.write_text(report.to_html([]), encoding="utf-8")

    class Handler(http.server.SimpleHTTPRequestHandler):
        def do_GET(self):
            if self.path in ("/", "/index.html"):
                self.path = "/" + settings.dashboard_path.name
            if self.path != "/" + settings.dashboard_path.name:
                self.send_error(404)
                return
            super().do_GET()

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(
        ("0.0.0.0", port), functools.partial(Handler, directory=str(settings.data_dir)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"داشبورد: http://localhost:{port}", flush=True)
    return server


# ---- commands --------------------------------------------------------------------------------

def cmd_predict(args, settings: Settings) -> None:
    analyzer, store = build(settings, news=not args.no_news)
    match = analyzer.find_fixture(args.team_a, args.team_b, days=args.days)
    if match is None:
        try:
            match = analyzer.hypothetical(args.team_a, args.team_b)
        except TeamNotFound as e:
            sys.exit(f"تیم «{e}» پیدا نشد. نام انگلیسی کامل را امتحان کنید (مثلاً \"Manchester City\").")
    prediction = analyzer.analyze(match)
    store.save(prediction)
    print(report.to_text(prediction))


def cmd_upcoming(args, settings: Settings) -> None:
    analyzer, store = build(settings, news=not args.no_news)
    predictions = run_cycle(analyzer, store, settings, days=args.days, competitions=args.competition)
    if not predictions:
        print("بازی‌ای در این بازه پیدا نشد.")
    elif args.details:
        for p in predictions:
            print(report.to_text(p))
    print(f"\nداشبورد ذخیره شد: {settings.dashboard_path}")


def cmd_watch(args, settings: Settings) -> None:
    analyzer, store = build(settings, news=not args.no_news)
    if args.serve:
        serve(settings, args.port)
    interval = args.interval or settings.watch_interval_minutes
    while True:
        started = datetime.now(timezone.utc)
        print(f"\n— به‌روزرسانی {report.kickoff_text(started)} —", flush=True)
        try:
            run_cycle(analyzer, store, settings, days=args.days, competitions=args.competition)
        except Exception:
            log.exception("update cycle failed; retrying next cycle")
        next_run = started + timedelta(minutes=interval)
        print(f"به‌روزرسانی بعدی: {report.kickoff_text(next_run)}", flush=True)
        time.sleep(max(0.0, (next_run - datetime.now(timezone.utc)).total_seconds()))


def cmd_serve(args, settings: Settings) -> None:
    server = serve(settings, args.port)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        server.shutdown()


def cmd_accuracy(args, settings: Settings) -> None:
    store = Store(settings.db_path)
    if settings.football_data_key and not args.offline:
        analyzer, _ = build(settings, news=False)
        update_results(analyzer, store)
    print(report.accuracy_text(store.accuracy()))


def main(argv: list[str] | None = None) -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="fp", description="پیش‌بینی نتیجه‌ی بازی‌های فوتبال")
    parser.add_argument("-v", "--verbose", action="store_true", help="show debug logs")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("predict", help="analyse one match, e.g. fp predict \"Man City\" Arsenal")
    p.add_argument("team_a", help="home team (if no fixture is found)")
    p.add_argument("team_b", help="away team")
    p.add_argument("--days", type=int, default=21, help="how far ahead to look for the fixture")
    p.add_argument("--no-news", action="store_true", help="skip Claude news research")
    p.set_defaults(func=cmd_predict)

    def add_window(sp):
        sp.add_argument("--days", type=int, help="look-ahead window in days (default from settings)")
        sp.add_argument("--competition", "-c", action="append",
                        help="competition code, repeatable: PL, PD, SA, BL1, FL1, CL, ...")
        sp.add_argument("--no-news", action="store_true", help="skip Claude news research")

    p = sub.add_parser("upcoming", help="predict every fixture in the next few days")
    add_window(p)
    p.add_argument("--details", action="store_true", help="print the full analysis for each match")
    p.set_defaults(func=cmd_upcoming)

    p = sub.add_parser("watch", help="keep researching & updating predictions on a schedule")
    add_window(p)
    p.add_argument("--interval", type=int, help="minutes between updates")
    p.add_argument("--serve", action="store_true", help="also serve the dashboard")
    p.add_argument("--port", type=int, default=8000)
    p.set_defaults(func=cmd_watch)

    p = sub.add_parser("serve", help="serve the HTML dashboard")
    p.add_argument("--port", type=int, default=8000)
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("accuracy", help="how accurate past predictions were")
    p.add_argument("--offline", action="store_true", help="don't fetch new results first")
    p.set_defaults(func=cmd_accuracy)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    try:
        args.func(args, Settings.from_env())
    except HttpError as e:
        if e.status in (401, 403):
            sys.exit(f"خطای دسترسی API ({e.status}): کلید را بررسی کنید یا این مسابقه در پلن رایگان نیست.\n{e}")
        sys.exit(f"خطای شبکه/API: {e}")
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
