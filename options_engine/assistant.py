"""
assistant.py — the orchestrator for the notification / decision-support layer.

Wires providers + analyzer + recommendation + notifier + briefing + alerts +
performance into one object with simple verbs the scheduler and CLI call:

  send_briefing()        -> build + deliver the 9am report
  run_alert_scan()       -> one intraday alert cycle
  send_weekly_report()   -> Friday performance summary
  send_monthly_report()  -> 1st-of-month performance summary
  alert_loop()           -> blocking market-hours loop (if not using the scheduler)

Nothing here executes a trade. BUY/SELL/HOLD alerts go to your phone; you confirm
and place orders through Claude's Robinhood MCP (review_option_order ->
place_option_order). That's Feature 3 (human-in-the-loop) by construction.
"""

from __future__ import annotations

import time

from .config import (
    DATA_PROVIDER, PROVIDER_CONFIG, NOTIFY_CONFIG, RECO_CONFIG,
    ENGINE_CONFIG, STORAGE_CONFIG,
)
from .providers import get_provider, PortfolioProvider
from .recommendation import RecommendationEngine
from .analysis import Analyzer
from .notifications import NotificationDispatcher
from .briefing import BriefingGenerator
from .alerts import AlertEngine
from .performance import PerformanceTracker
from .storage import Storage
from .data import market_calendar as cal


class Assistant:
    def __init__(self):
        self.provider = get_provider(DATA_PROVIDER, None)
        self.reco = RecommendationEngine(RECO_CONFIG)
        self.analyzer = Analyzer(self.provider, self.reco)
        self.pp = PortfolioProvider(PROVIDER_CONFIG["portfolio_sync_file"])
        self.storage = Storage(STORAGE_CONFIG)
        self.notify = NotificationDispatcher(NOTIFY_CONFIG)
        self.watchlist = ENGINE_CONFIG["watchlist"]
        self.briefing = BriefingGenerator(self.analyzer, self.pp, self.watchlist)
        self.alerts = AlertEngine(self.analyzer, self.pp, self.notify, self.storage, self.watchlist)
        self.perf = PerformanceTracker(self.storage)

    # ----- deliverables --------------------------------------------- #
    def send_briefing(self):
        rep = self.briefing.build()
        body = rep["html"] if self._telegram_primary() else rep["text"]
        res = self.notify.send(body, subject=rep["subject"], kind="briefing")
        print(f"[briefing] delivered via {res.channel} ok={res.ok} {res.detail}")
        return rep

    def run_alert_scan(self) -> int:
        n = self.alerts.scan()
        print(f"[alerts] scan emitted {n} alert(s)")
        return n

    def send_weekly_report(self):
        rep = self.perf.render_report(days=7)
        return self.notify.send(
            rep["html"] if self._telegram_primary() else rep["text"],
            subject=rep["subject"], kind="report")

    def send_monthly_report(self):
        rep = self.perf.render_report(days=30, title="🗓️ Monthly Performance")
        return self.notify.send(
            rep["html"] if self._telegram_primary() else rep["text"],
            subject=rep["subject"], kind="report")

    def _telegram_primary(self) -> bool:
        return "telegram" in self.notify.configured_channels() or not self.notify.any_configured()

    # ----- blocking loop (alternative to scheduler) ----------------- #
    def alert_loop(self, interval: int | None = None):
        from .config import ALERT_CONFIG
        interval = interval or ALERT_CONFIG["scan_interval"]
        print(f"[assistant] alert loop every {interval}s (market-hours only). Ctrl+C to stop.")
        while True:
            try:
                if cal.is_market_open():
                    self.run_alert_scan()
                time.sleep(interval)
            except KeyboardInterrupt:
                break
            except Exception as exc:
                print(f"[assistant] scan error: {exc}")
                time.sleep(interval)

    def close(self):
        self.analyzer.save_iv()
        self.storage.close()


# --------------------------------------------------------------------------- #
#  CLI
# --------------------------------------------------------------------------- #
def main():
    import argparse
    ap = argparse.ArgumentParser(description="Options trading assistant (notifications + decision support)")
    ap.add_argument("--briefing", action="store_true", help="send the morning briefing now")
    ap.add_argument("--alerts", action="store_true", help="run one alert scan now")
    ap.add_argument("--loop", action="store_true", help="run the market-hours alert loop")
    ap.add_argument("--weekly", action="store_true", help="send weekly performance report")
    ap.add_argument("--monthly", action="store_true", help="send monthly performance report")
    ap.add_argument("--schedule", action="store_true", help="run the full APScheduler service")
    ap.add_argument("--dashboard", action="store_true", help="run the FastAPI dashboard")
    ap.add_argument("--status", action="store_true", help="print configured channels + provider")
    args = ap.parse_args()

    if args.dashboard:
        from .webapp.app import main as web_main
        web_main()
        return
    if args.schedule:
        from .scheduler import run_scheduler
        run_scheduler()
        return

    a = Assistant()
    try:
        if args.status:
            chans = a.notify.configured_channels() or ["(none — dry-run console)"]
            print(f"provider={DATA_PROVIDER}  notify={chans}  watchlist={a.watchlist}")
        if args.briefing:
            a.send_briefing()
        if args.alerts:
            a.run_alert_scan()
        if args.weekly:
            a.send_weekly_report()
        if args.monthly:
            a.send_monthly_report()
        if args.loop:
            a.alert_loop()
        if not any([args.status, args.briefing, args.alerts, args.weekly, args.monthly, args.loop]):
            print("nothing to do — try --briefing, --alerts, --loop, or --schedule (see --help)")
    finally:
        a.close()


if __name__ == "__main__":
    main()
