"""
engine.py — real-time alert system (PDR Feature 2).

scan() runs each intraday cycle and emits four alert kinds with the PDR formats:

  BUY      — new opportunity: confidence >= threshold and acceptable risk
  SELL     — open position: target hit / reversal / breakdown / theta risk
  HOLD     — winner with strong trend: "hold longer" with projected upside
  RISK     — stop hit / vol spike / excessive theta / confidence collapse

Every alert routes through the NotificationDispatcher (Telegram→SMS→email, or
dry-run). De-duped per (symbol, kind) per day so you aren't spammed. Each alert is
also logged to storage for performance tracking and the dashboard.
"""

from __future__ import annotations

from datetime import date

from ..recommendation import confidence_band
from ..config import RECO_CONFIG, ALERT_CONFIG, RISK_CONFIG, EXECUTION_CONFIG


class AlertEngine:
    def __init__(self, analyzer, portfolio_provider, dispatcher, storage=None, watchlist=None):
        self.analyzer = analyzer
        self.pp = portfolio_provider
        self.notify = dispatcher
        self.storage = storage
        self.watchlist = watchlist or []
        self._seen: dict[str, set] = {}   # date -> {(symbol, kind)}

    # ----- de-dupe --------------------------------------------------- #
    def _fresh(self, symbol: str, kind: str) -> bool:
        if not ALERT_CONFIG.get("dedupe_per_day", True):
            return True
        today = date.today().isoformat()
        seen = self._seen.setdefault(today, set())
        key = (symbol, kind)
        if key in seen:
            return False
        seen.add(key)
        # clear old days
        for d in list(self._seen):
            if d != today:
                del self._seen[d]
        return True

    def _emit(self, kind: str, symbol: str, subject: str, body: str):
        if not self._fresh(symbol, kind):
            return
        self.notify.send(body, subject=subject, kind=kind)
        if self.storage:
            self.storage.log_alert(symbol, kind, subject, body)

    # ----- main scan ------------------------------------------------- #
    def scan(self) -> int:
        """Returns number of alerts emitted this cycle."""
        before = len(self.notify.alert_log)
        market = self.analyzer.read_market()

        # 1) open positions -> SELL / HOLD / RISK
        acct = self.pp.get_snapshot()
        for p in acct.positions:
            if p.kind != "option":
                continue
            try:
                bundle = self.analyzer.analyze(p.symbol, market)
                ta = bundle["ta"] if bundle else None
            except Exception:
                ta = None
            self._check_position(p, ta, market)

        # 2) watchlist -> BUY
        for sym in self.watchlist:
            try:
                bundle = self.analyzer.analyze(sym, market)
            except Exception:
                bundle = None
            if not bundle or not bundle["signals"]:
                continue
            best = max(bundle["signals"], key=lambda s: s.confidence)
            self._check_buy(sym, best, bundle["metrics"], market)

        self.analyzer.save_iv()
        return len(self.notify.alert_log) - before

    # ----- position alerts ------------------------------------------ #
    def _check_position(self, p, ta, market):
        rec = self.analyzer.reco.assess_position(
            p, ta, market, RISK_CONFIG["take_profit_pct"], RISK_CONFIG["stop_loss_pct"]
        )
        gain = p.gain_pct
        action = rec["recommendation"]
        dte = p.dte if p.dte is not None else "?"
        head = f"{p.symbol} {p.strike:g}{p.option_type[0].upper()} {p.expiration}"

        # RISK alert: stop hit or theta/vol risk language
        if gain <= -RISK_CONFIG["stop_loss_pct"]:
            body = (
                f"⚠️ RISK — {head} x{p.quantity}\n"
                f"Current Loss: {gain:.0f}%  (${(p.current_price-p.entry_price)*p.quantity*100:,.0f})\n"
                f"Risk: stop-loss breached; {dte} DTE; Θ {p.theta:+.2f}\n"
                f"Recommendation: EXIT POSITION"
            )
            self._emit("risk", p.symbol, "⚠️ RISK ALERT", body)
            return

        # SELL alert
        if action == "SELL":
            body = (
                f"🔴 SELL — {head} x{p.quantity}\n"
                f"Current Gain/Loss: {'+' if gain>=0 else ''}{gain:.0f}%\n"
                f"Confidence: {rec['confidence']:.0f} ({confidence_band(rec['confidence'])})\n"
                f"Reason for Exit: {rec['reason']}\n"
                f"Recommended Action: SELL / PARTIAL SELL / CONTINUE HOLDING"
            )
            self._emit("sell", p.symbol, "🔴 SELL SIGNAL", body)
            return

        if action == "REDUCE POSITION":
            body = (
                f"🟠 REDUCE — {head} x{p.quantity}\n"
                f"Current Gain: +{gain:.0f}%\n"
                f"Confidence: {rec['confidence']:.0f}\n"
                f"Reason: {rec['reason']}\n"
                f"Recommended Action: PARTIAL SELL (lock gains, keep a runner)"
            )
            self._emit("sell", p.symbol, "🟠 REDUCE POSITION", body)
            return

        # HOLD-extension alert: target hit but trend strong
        if gain >= RISK_CONFIG["take_profit_pct"] and action == "HOLD":
            projected = gain + (p.gain_pct * 0.3)  # rough projected upside continuation
            body = (
                f"🟢 HOLD LONGER — {head} x{p.quantity}\n"
                f"Status: target hit, trend intact\n"
                f"Current Gain: +{gain:.0f}%\n"
                f"Projected Upside: ~+{projected:.0f}% if momentum holds\n"
                f"Confidence: {rec['confidence']:.0f} ({confidence_band(rec['confidence'])})\n"
                f"Recommendation: HOLD LONGER — {rec['reason']}"
            )
            self._emit("hold", p.symbol, "🟢 HOLD EXTENSION", body)
            return

        # MONITOR CLOSELY -> light risk nudge
        if action == "MONITOR CLOSELY":
            body = (
                f"🟡 MONITOR — {head} x{p.quantity}\n"
                f"{'+' if gain>=0 else ''}{gain:.0f}%, {dte} DTE\n"
                f"Watch: {rec['reason']}"
            )
            self._emit("risk", p.symbol, "🟡 MONITOR CLOSELY", body)

    # ----- buy alerts ------------------------------------------------ #
    def _check_buy(self, symbol, signal, metrics, market):
        # Don't fire BUY alerts until IV-rank history has matured — early signals
        # rest on incomplete vol context (this is the "let IV build" guard).
        if RECO_CONFIG.get("require_iv_history_for_alerts", True):
            if not metrics.get("iv_mature"):
                return
        conf = signal.confidence * 100
        if conf < RECO_CONFIG["buy_alert_min_confidence"]:
            return
        risk = signal.notes.split("risk:")[-1].strip() if "risk:" in signal.notes else "?"
        if risk == "High":
            return  # risk profile not acceptable
        lg = signal.legs[0]
        em = (metrics.get("expected_move") or {}).get("expected_move_pct")
        em_s = f"{em*100:.1f}%" if em is not None else "n/a"
        indicators = self._supporting(metrics, signal)
        contract = f"{lg.expiration} {lg.strike:g}{lg.option_type[0].upper()}"
        body = (
            f"🚀 BUY — {symbol}\n"
            f"Suggested Contract: {contract} @ ~${lg.entry_premium:.2f} ({signal.direction})\n"
            f"Confidence: {conf:.0f} ({confidence_band(conf)})\n"
            f"Expected Move: {em_s}\n"
            f"Risk Rating: {risk}\n"
            f"Supporting: {indicators}\n"
            f"Recommended Action: BUY or IGNORE\n"
            f"➡️ To execute: ask Claude to review_option_order then place_option_order "
            f"on account {EXECUTION_CONFIG.get('robinhood_account') or 'your agentic account'}"
        )
        self._emit("buy", symbol, "🚀 BUY SIGNAL", body)

    def _supporting(self, metrics, signal) -> str:
        bits = []
        ivr = metrics.get("iv_rank")
        if ivr is not None:
            bits.append(f"IVrank {ivr:.0f}")
        pcr = metrics.get("pcr_volume")
        if pcr is not None:
            bits.append(f"P/C {pcr:.2f}")
        bits.append(signal.notes.split("|")[0].strip())
        return ", ".join(bits)
