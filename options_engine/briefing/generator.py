"""
generator.py — builds the 9:00 AM ET pre-market briefing (PDR Feature 1).

Sections: portfolio summary, per-position analysis (Greeks + recommendation),
market overview (SPY/QQQ/VIX/sentiment), watchlist opportunities, and an
events/earnings block. Returns Telegram-friendly HTML; a plain-text variant is
used for SMS/email and the dashboard.

Data: account state from the PortfolioProvider sync file; market + watchlist from
the Analyzer (provider-backed). Earnings via market_calendar (yfinance).
"""

from __future__ import annotations

from datetime import datetime

from ..recommendation import confidence_band
from ..data import market_calendar as cal
from ..config import ENGINE_CONFIG, RISK_CONFIG, RECO_CONFIG


def _money(v: float) -> str:
    sign = "-" if v < 0 else ""
    return f"{sign}${abs(v):,.2f}"


def _pl(v: float) -> str:
    arrow = "🟢▲" if v > 0 else ("🔴▼" if v < 0 else "⚪")
    return f"{arrow} {_money(v)}"


class BriefingGenerator:
    def __init__(self, analyzer, portfolio_provider, watchlist=None):
        self.analyzer = analyzer
        self.pp = portfolio_provider
        self.watchlist = watchlist or ENGINE_CONFIG["watchlist"]

    def build(self) -> dict:
        """Return {'subject', 'html', 'text'}."""
        now = cal.now_et()
        market = self.analyzer.read_market()
        acct = self.pp.get_snapshot()

        h = []  # html lines
        h.append(f"📊 <b>Morning Briefing</b> — {now.strftime('%a %b %d, %Y %I:%M %p ET')}")
        h.append(self._market_block(market))
        h.append(self._portfolio_block(acct))
        h.append(self._positions_block(acct, market))
        h.append(self._watchlist_block(market))
        h.append(self._events_block(acct))
        html = "\n\n".join(b for b in h if b)
        text = _strip_html(html)
        self.analyzer.save_iv()
        return {"subject": "📊 Morning Briefing", "html": html, "text": text}

    # ------------------------------------------------------------------ #
    def _market_block(self, m) -> str:
        emoji = "🐂" if m.classification == "Bullish" else ("🐻" if m.classification == "Bearish" else "➖")
        vix = f"{m.vix:.1f}" if m.vix is not None else "n/a"
        return (
            f"{emoji} <b>Market: {m.classification}</b>  (sentiment {m.sentiment:.0f}/100)\n"
            f"• SPY: {m.spy_note}\n"
            f"• QQQ: {m.qqq_note}\n"
            f"• VIX: {vix}  →  risk {m.risk_level}"
        )

    def _portfolio_block(self, a) -> str:
        return (
            f"💼 <b>Portfolio</b>  (as of {a.updated_at})\n"
            f"• Value: {_money(a.total_value)}   Cash: {_money(a.cash)}   BP: {_money(a.buying_power)}\n"
            f"• P/L — Day {_pl(a.day_pl)} | Wk {_pl(a.week_pl)} | Mo {_pl(a.month_pl)}\n"
            f"• Open positions: {a.open_positions}  (options: {a.open_options})"
        )

    def _positions_block(self, a, market) -> str:
        if not a.positions:
            return "📈 <b>Positions</b>\n• none open"
        lines = ["📈 <b>Positions</b>"]
        for p in a.positions:
            if p.kind != "option":
                lines.append(f"• {p.symbol} equity x{p.quantity}  {_pl((p.current_price-p.entry_price)*p.quantity)}")
                continue
            ta = None
            try:
                bundle = self.analyzer.analyze(p.symbol, market)
                ta = bundle["ta"] if bundle else None
            except Exception:
                ta = None
            rec = self.analyzer.reco.assess_position(
                p, ta, market, RISK_CONFIG["take_profit_pct"], RISK_CONFIG["stop_loss_pct"]
            )
            dte = p.dte if p.dte is not None else "?"
            tag = {"SELL": "🔴", "REDUCE POSITION": "🟠", "MONITOR CLOSELY": "🟡", "HOLD": "🟢"}.get(
                rec["recommendation"], "⚪")
            lines.append(
                f"• <b>{p.symbol}</b> {p.strike:g}{p.option_type[0].upper()} {p.expiration} "
                f"x{p.quantity}\n"
                f"   entry ${p.entry_price:.2f} → ${p.current_price:.2f} "
                f"({'+' if p.gain_pct>=0 else ''}{p.gain_pct:.0f}%), {dte} DTE\n"
                f"   Δ {p.delta:+.2f}  Θ {p.theta:+.2f}  | conf {rec['confidence']:.0f} "
                f"({confidence_band(rec['confidence'])})\n"
                f"   {tag} <b>{rec['recommendation']}</b> — {rec['reason']}"
            )
        return "\n".join(lines)

    def _watchlist_block(self, market) -> str:
        opps = []
        for sym in self.watchlist:
            try:
                bundle = self.analyzer.analyze(sym, market)
            except Exception:
                bundle = None
            if not bundle or not bundle["signals"]:
                continue
            best = max(bundle["signals"], key=lambda s: s.confidence)
            conf = best.confidence * 100
            if conf < RECO_CONFIG["moderate"]:
                continue
            opps.append((conf, sym, best, bundle["metrics"]))
        opps.sort(reverse=True, key=lambda x: x[0])
        if not opps:
            return "🔎 <b>Watchlist Opportunities</b>\n• none above moderate confidence today"
        lines = ["🔎 <b>Watchlist Opportunities</b>"]
        for conf, sym, s, metrics in opps[:6]:
            lg = s.legs[0]
            em = (metrics.get("expected_move") or {}).get("expected_move_pct")
            em_s = f"{em*100:.1f}%" if em is not None else "n/a"
            risk = s.notes.split("risk:")[-1] if "risk:" in s.notes else "?"
            contract = f"{lg.expiration} {lg.strike:g}{lg.option_type[0].upper()}"
            lines.append(
                f"• <b>{sym}</b> {contract} @ ~${lg.entry_premium:.2f}\n"
                f"   conf {conf:.0f} ({confidence_band(conf)}), {s.direction}, "
                f"exp move {em_s}, risk {risk}\n"
                f"   {s.strategy.replace('_',' ')}: {s.notes.split('|')[0].strip()}"
            )
        return "\n".join(lines)

    def _events_block(self, a) -> str:
        symbols = list({*(p.symbol for p in a.positions), *self.watchlist})
        upcoming = []
        for sym in symbols:
            d = cal.days_to_earnings(sym)
            if d is not None and 0 <= d <= 14:
                upcoming.append((d, sym))
        upcoming.sort()
        lines = ["🗓️ <b>Events</b>"]
        if upcoming:
            lines.append("• Earnings (≤14d): " + ", ".join(f"{s} in {d}d" for d, s in upcoming))
        else:
            lines.append("• No tracked earnings within 14 days")
        lines.append("• Check the economic calendar for CPI/FOMC/jobs prints before sizing up")
        return "\n".join(lines)


def _strip_html(s: str) -> str:
    import re
    return re.sub(r"</?b>", "", s)
