"""
market_calendar.py — trading-session checks and earnings-date lookups.

is_market_open()    -> bool, True only during 9:30–16:00 ET on an NYSE session day
next_market_open()  -> datetime of the next session open
get_earnings_date() -> next earnings date for a ticker (yfinance), or None
days_to_earnings()  -> int days until next earnings, or None

pandas_market_calendars gives an accurate NYSE calendar (holidays, half-days).
If it isn't installed we fall back to a simple weekday + clock check.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytz

_ET = pytz.timezone("America/New_York")

try:
    import pandas_market_calendars as mcal
    _NYSE = mcal.get_calendar("XNYS")
except Exception:  # pragma: no cover
    _NYSE = None


def now_et() -> datetime:
    return datetime.now(_ET)


def is_market_open(dt: datetime | None = None) -> bool:
    dt = dt or now_et()
    if dt.tzinfo is None:
        dt = _ET.localize(dt)
    dt = dt.astimezone(_ET)

    if _NYSE is not None:
        sched = _NYSE.schedule(
            start_date=dt.strftime("%Y-%m-%d"), end_date=dt.strftime("%Y-%m-%d")
        )
        if sched.empty:
            return False
        open_ = sched.iloc[0]["market_open"].tz_convert(_ET)
        close_ = sched.iloc[0]["market_close"].tz_convert(_ET)
        return open_ <= dt <= close_

    # fallback: Mon–Fri, 9:30–16:00 ET, ignores holidays
    if dt.weekday() >= 5:
        return False
    open_ = dt.replace(hour=9, minute=30, second=0, microsecond=0)
    close_ = dt.replace(hour=16, minute=0, second=0, microsecond=0)
    return open_ <= dt <= close_


def next_market_open(dt: datetime | None = None) -> datetime:
    dt = dt or now_et()
    if _NYSE is not None:
        sched = _NYSE.schedule(
            start_date=dt.strftime("%Y-%m-%d"),
            end_date=(dt + timedelta(days=7)).strftime("%Y-%m-%d"),
        )
        for _, row in sched.iterrows():
            open_ = row["market_open"].tz_convert(_ET)
            if open_ > dt:
                return open_.to_pydatetime()
    # fallback
    probe = dt
    for _ in range(10):
        probe = (probe + timedelta(days=1)).replace(hour=9, minute=30, second=0, microsecond=0)
        if probe.weekday() < 5:
            return probe
    return dt


# --------------------------------------------------------------------------- #
#  earnings
# --------------------------------------------------------------------------- #
def get_earnings_date(symbol: str) -> datetime | None:
    """Next scheduled earnings date via yfinance, or None if unavailable."""
    try:
        import yfinance as yf
    except ImportError:
        return None
    try:
        tkr = yf.Ticker(symbol)
        cal = getattr(tkr, "calendar", None)
        # yfinance >=0.2.40 returns a dict; older versions a DataFrame
        if isinstance(cal, dict):
            ed = cal.get("Earnings Date")
            if isinstance(ed, (list, tuple)) and ed:
                ed = ed[0]
            if ed:
                return datetime(ed.year, ed.month, ed.day)
        elif cal is not None and hasattr(cal, "loc"):
            ed = cal.loc["Earnings Date"][0]
            return datetime(ed.year, ed.month, ed.day)
    except Exception:
        return None
    return None


def days_to_earnings(symbol: str) -> int | None:
    ed = get_earnings_date(symbol)
    if ed is None:
        return None
    return (ed.date() - now_et().date()).days
