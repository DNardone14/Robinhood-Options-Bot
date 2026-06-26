"""
tracker.py — performance analytics + weekly/monthly summaries (PDR Feature 6).

Reads the signals / trades / decisions / alerts tables from Storage and computes:
  total signals, approved vs rejected, win rate, avg gain, avg loss,
  per-ticker performance, and period (week/month) roll-ups.

summary(days) returns a dict; render_report() formats a Telegram/text message that
the scheduler delivers automatically each Friday (weekly) and on the 1st (monthly).
"""

from __future__ import annotations

from datetime import datetime, timedelta


class PerformanceTracker:
    def __init__(self, storage):
        self.s = storage

    def _since(self, days: int) -> str:
        return (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")

    def summary(self, days: int = 7) -> dict:
        since = self._since(days)

        total_signals = self.s.query(
            "SELECT COUNT(*) FROM signals WHERE ts >= ?", (since,))[0][0]
        approved = self.s.query(
            "SELECT COUNT(*) FROM signals WHERE ts >= ? AND approved = 1", (since,))[0][0]
        rejected = total_signals - approved

        # realized trades = decisions/trades carrying pnl (closes)
        closed = self.s.query(
            "SELECT pnl FROM trades WHERE ts >= ? AND action LIKE '%close%' AND pnl != 0", (since,))
        pnls = [r[0] for r in closed]
        # also fold in explicit decisions with pnl
        pnls += [r[0] for r in self.s.query(
            "SELECT pnl FROM decisions WHERE ts >= ? AND pnl != 0", (since,))]

        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        n = len(pnls)
        win_rate = (len(wins) / n * 100) if n else 0.0
        avg_win = (sum(wins) / len(wins)) if wins else 0.0
        avg_loss = (sum(losses) / len(losses)) if losses else 0.0
        net = sum(pnls)

        # per-ticker
        per_ticker = {}
        for sym, pnl in self.s.query(
            "SELECT symbol, pnl FROM trades WHERE ts >= ? AND pnl != 0", (since,)):
            d = per_ticker.setdefault(sym, {"net": 0.0, "n": 0, "wins": 0})
            d["net"] += pnl
            d["n"] += 1
            d["wins"] += 1 if pnl > 0 else 0

        alerts_by_kind = {}
        for kind, cnt in self.s.query(
            "SELECT kind, COUNT(*) FROM alerts WHERE ts >= ? GROUP BY kind", (since,)):
            alerts_by_kind[kind] = cnt

        return {
            "days": days,
            "total_signals": total_signals,
            "approved": approved,
            "rejected": rejected,
            "closed_trades": n,
            "win_rate": win_rate,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "net_pnl": net,
            "profit_factor": (sum(wins) / abs(sum(losses))) if losses else None,
            "per_ticker": per_ticker,
            "alerts_by_kind": alerts_by_kind,
        }

    def render_report(self, days: int = 7, title: str | None = None) -> dict:
        s = self.summary(days)
        title = title or ("📅 Weekly Performance" if days <= 7 else "🗓️ Monthly Performance")
        pf = f"{s['profit_factor']:.2f}x" if s["profit_factor"] is not None else "n/a"
        lines = [
            f"<b>{title}</b> (last {days}d)",
            f"• Signals: {s['total_signals']}  (approved {s['approved']}, rejected {s['rejected']})",
            f"• Closed trades: {s['closed_trades']}  | Win rate: {s['win_rate']:.0f}%",
            f"• Net P&L: {'+' if s['net_pnl']>=0 else ''}${s['net_pnl']:,.2f}  | PF {pf}",
            f"• Avg win ${s['avg_win']:,.2f}  | Avg loss ${s['avg_loss']:,.2f}",
        ]
        if s["per_ticker"]:
            top = sorted(s["per_ticker"].items(), key=lambda kv: kv[1]["net"], reverse=True)
            lines.append("• By ticker: " + ", ".join(
                f"{sym} {'+' if d['net']>=0 else ''}${d['net']:,.0f}({d['wins']}/{d['n']})"
                for sym, d in top[:6]))
        if s["alerts_by_kind"]:
            lines.append("• Alerts: " + ", ".join(f"{k} {v}" for k, v in s["alerts_by_kind"].items()))
        html = "\n".join(lines)
        import re
        return {"subject": title, "html": html, "text": re.sub(r"</?b>", "", html)}
