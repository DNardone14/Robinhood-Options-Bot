"""
scheduler.py — APScheduler service running every scheduled job (PDR scheduling).

Jobs (all in America/New_York):
  * 09:00 Mon–Fri  -> morning briefing
  * every N sec     -> intraday alert scan (the job itself checks market hours)
  * Fri 16:00       -> weekly performance report
  * 1st 09:00       -> monthly performance report

Run:  python -m options_engine.assistant --schedule
This is the production entry point for the VPS (pair with start/stop scripts).
"""

from __future__ import annotations

import time

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from .assistant import Assistant
from .config import SCHEDULE_CONFIG, ALERT_CONFIG
from .data import market_calendar as cal


def run_scheduler():
    a = Assistant()
    tz = SCHEDULE_CONFIG["timezone"]
    sched = BackgroundScheduler(timezone=tz)

    sched.add_job(
        a.send_briefing, CronTrigger(
            day_of_week="mon-fri",
            hour=SCHEDULE_CONFIG["briefing_hour"],
            minute=SCHEDULE_CONFIG["briefing_minute"], timezone=tz),
        id="briefing", misfire_grace_time=600,
    )

    def _alert_job():
        if cal.is_market_open():
            a.run_alert_scan()

    sched.add_job(_alert_job, IntervalTrigger(seconds=ALERT_CONFIG["scan_interval"]),
                  id="alerts", max_instances=1, coalesce=True)

    sched.add_job(
        a.send_weekly_report, CronTrigger(
            day_of_week=SCHEDULE_CONFIG["weekly_report_day"],
            hour=SCHEDULE_CONFIG["weekly_report_hour"], minute=5, timezone=tz),
        id="weekly",
    )
    sched.add_job(
        a.send_monthly_report, CronTrigger(
            day=SCHEDULE_CONFIG["monthly_report_day"],
            hour=SCHEDULE_CONFIG["monthly_report_hour"], minute=10, timezone=tz),
        id="monthly",
    )

    chans = a.notify.configured_channels() or ["(dry-run console — no creds yet)"]
    print(f"[scheduler] started. provider={a.provider.name} notify={chans}")
    print("[scheduler] jobs: briefing 09:00, alerts every "
          f"{ALERT_CONFIG['scan_interval']}s, weekly Fri 16:05, monthly 1st 09:10 ET")
    sched.start()
    try:
        while True:
            time.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        print("[scheduler] shutting down")
        sched.shutdown()
        a.close()


if __name__ == "__main__":
    run_scheduler()
