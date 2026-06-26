"""
engine.py — the orchestrator that ties every layer together for one scan.

scan_symbol():
  1. pull daily + intraday bars (Tradier, yfinance fallback) -> technicals
  2. pull option expirations + chains -> backfill Greeks, compute chain metrics
  3. run Directional Momentum + Unusual Flow strategies
  4. (optional) convert to vertical spreads
  5. apply earnings filter
  6. risk-size + Greeks-check each signal
  7. write tickets, log, open paper positions

scan_all() loops the watchlist; mark_and_manage() marks open positions and emits
exits. The TUI reads engine state between scans.

IV history (for IV rank/percentile) is kept per-symbol in memory and persisted
to a small json so rank survives restarts.
"""

from __future__ import annotations

import json
import os
from collections import defaultdict, deque
from datetime import datetime

from .config import (
    TRADIER_CONFIG, ENGINE_CONFIG, STRATEGY_CONFIG, RISK_CONFIG,
    EXECUTION_CONFIG, STORAGE_CONFIG,
)
from .data.tradier import TradierClient, TradierError
from .data import market_calendar as cal
from .indicators import technicals as ta_mod
from .indicators import options_metrics as om
from .strategies import DirectionalMomentum, UnusualFlow, build_vertical
from .risk import RiskManager
from .execution import RobinhoodRouter
from .portfolio import PortfolioTracker
from .storage import Storage

_IV_HISTORY_FILE = "iv_history.json"


class Engine:
    def __init__(self):
        self.tradier = TradierClient(TRADIER_CONFIG)
        self.dm = DirectionalMomentum(STRATEGY_CONFIG)
        self.uf = UnusualFlow(STRATEGY_CONFIG)
        self.risk = RiskManager(RISK_CONFIG)
        self.router = RobinhoodRouter(EXECUTION_CONFIG)
        self.portfolio = PortfolioTracker()
        self.storage = Storage(STORAGE_CONFIG)
        self.iv_history: dict[str, deque] = defaultdict(lambda: deque(maxlen=ENGINE_CONFIG["history_days"]))
        self._load_iv_history()
        # surfaced to the dashboard
        self.active_signals: list = []
        self.last_scan: datetime | None = None
        self.last_error: str | None = None
        self.metrics_by_symbol: dict[str, dict] = {}

    # ------------------------------------------------------------------ #
    #  data helpers (Tradier primary, yfinance fallback)
    # ------------------------------------------------------------------ #
    def _daily_bars(self, symbol: str):
        try:
            df = self.tradier.get_history(symbol, ENGINE_CONFIG["history_days"])
            if df is not None and not df.empty:
                return df
        except TradierError:
            pass
        try:
            import yfinance as yf
            df = yf.download(symbol, period="1y", interval="1d", progress=False, auto_adjust=False)
            if df is None or df.empty:
                return None
            df = df.rename(columns=str.lower)
            if isinstance(df.columns[0], tuple):  # flatten yfinance multiindex
                df.columns = [c[0].lower() for c in df.columns]
            return df[["open", "high", "low", "close", "volume"]]
        except Exception as exc:
            self.last_error = f"{symbol} bars: {exc}"
            return None

    def _intraday_bars(self, symbol: str):
        try:
            df = self.tradier.get_timesales(symbol, ENGINE_CONFIG["intraday_interval"])
            return df if (df is not None and not df.empty) else None
        except TradierError:
            return None

    def _chains(self, symbol: str, underlying_price: float):
        """Returns (chains_by_exp, merged_chain, expirations) with Greeks ensured."""
        try:
            expirations = self.tradier.get_expirations(symbol)
        except TradierError as exc:
            self.last_error = f"{symbol} expirations: {exc}"
            return {}, [], []
        max_dte = max(STRATEGY_CONFIG["dm_max_dte"], STRATEGY_CONFIG["uf_max_dte"])
        expirations = [e for e in expirations if 0 <= om.dte(e) <= max_dte]
        chains_by_exp, merged = {}, []
        r = TRADIER_CONFIG["risk_free_rate"]
        for exp in expirations:
            try:
                chain = self.tradier.get_option_chain(symbol, exp, greeks=True)
            except TradierError:
                continue
            for o in chain:
                if TRADIER_CONFIG["compute_greeks_fallback"]:
                    g = om.ensure_greeks(o, underlying_price, r)
                    o.setdefault("greeks", {})
                    o["greeks"].update({k: g[k] for k in ("delta", "gamma", "theta", "vega")})
                    if g.get("iv") and not o["greeks"].get("mid_iv"):
                        o["greeks"]["mid_iv"] = g["iv"]
            chains_by_exp[exp] = chain
            merged.extend(chain)
        return chains_by_exp, merged, expirations

    # ------------------------------------------------------------------ #
    #  single-symbol scan
    # ------------------------------------------------------------------ #
    def scan_symbol(self, symbol: str) -> list:
        daily = self._daily_bars(symbol)
        if daily is None or len(daily) < 50:
            return []
        intraday = self._intraday_bars(symbol)
        ta = ta_mod.compute_all(daily, STRATEGY_CONFIG, intraday)
        price = ta["price"]

        chains_by_exp, merged, expirations = self._chains(symbol, price)
        if not merged:
            self.metrics_by_symbol[symbol] = {"price": price, "rsi": ta["rsi"], "note": "no chain"}
            return []

        # chain analytics
        atm = om.atm_iv(merged, price)
        if atm:
            self.iv_history[symbol].append(atm)
        iv_hist = list(self.iv_history[symbol])
        metrics = {
            "price": price,
            "rsi": ta["rsi"],
            "vwap": ta.get("vwap"),
            "atm_iv": atm,
            "iv_rank": om.iv_rank(atm, iv_hist),
            "iv_pct": om.iv_percentile(atm, iv_hist),
            "skew": om.iv_skew(merged, price),
            "expected_move": om.expected_move(merged, price),
            **om.put_call_ratio(merged),
        }
        self.metrics_by_symbol[symbol] = metrics

        # earnings filter
        dte_earn = cal.days_to_earnings(symbol)
        earnings_flag = False
        skip_days = STRATEGY_CONFIG["skip_earnings_within_days"]
        if dte_earn is not None and 0 <= dte_earn <= skip_days:
            if STRATEGY_CONFIG["earnings_iv_crush_mode"]:
                earnings_flag = True  # keep but flag
            else:
                return []  # skip to avoid IV crush

        # strategies
        raw = []
        if STRATEGY_CONFIG["dm_enabled"]:
            s = self.dm.evaluate(symbol, ta, chains_by_exp, expirations,
                                 metrics["expected_move"], earnings_flag)
            if s:
                raw.append(s)
        if STRATEGY_CONFIG["uf_enabled"]:
            raw.extend(self.uf.evaluate(symbol, ta, merged, atm, earnings_flag))

        # optional spread conversion
        if STRATEGY_CONFIG["spread_enabled"]:
            converted = []
            for s in raw:
                if s.signal_type.value in ("call", "put"):
                    v = build_vertical(s, merged, STRATEGY_CONFIG)
                    converted.append(v or s)
                else:
                    converted.append(s)
            raw = converted

        # risk gate
        approved = []
        for s in raw:
            decision = self.risk.evaluate(
                s, len(self.portfolio.open_positions()), self.portfolio.book_greeks()
            )
            self.storage.log_signal(s, decision.approved, decision.reason)
            if decision.approved:
                s.quantity = decision.quantity
                ticket = self.router.build_ticket(s)
                path = self.router.write_ticket(ticket)
                s.notes += f" | ticket:{os.path.basename(path)}"
                pos = self.portfolio.open_from_signal(s)
                self.storage.log_trade(
                    pos.position_id, s.symbol, s.strategy, "open_intent",
                    s.net_premium, s.quantity, 0.0, decision.reason
                )
                approved.append(s)
        return approved

    # ------------------------------------------------------------------ #
    #  full scan + position management
    # ------------------------------------------------------------------ #
    def scan_all(self) -> None:
        if ENGINE_CONFIG["market_hours_only"] and not cal.is_market_open():
            self.last_scan = cal.now_et()
            return
        found = []
        for sym in ENGINE_CONFIG["watchlist"]:
            try:
                found.extend(self.scan_symbol(sym))
            except Exception as exc:
                self.last_error = f"{sym}: {exc}"
        self.active_signals = found
        self.mark_and_manage()
        self.risk.update_day_pnl(self.portfolio.realized_day_pnl)
        self.last_scan = cal.now_et()
        self._save_iv_history()

    def mark_and_manage(self) -> list:
        """Re-quote open positions, mark P&L, emit exit intents as tickets."""
        exits = []
        for pos in self.portfolio.open_positions():
            try:
                syms = [lg["option_symbol"] for lg in pos.legs if lg["option_symbol"]]
                if not syms:
                    continue
                quotes = {q["symbol"]: q for q in self.tradier.get_quotes(syms, greeks=True)}
                net = 0.0
                leg_greeks = []
                for lg in pos.legs:
                    q = quotes.get(lg["option_symbol"], {})
                    mid = ((q.get("bid") or 0) + (q.get("ask") or 0)) / 2.0 or (q.get("last") or 0)
                    sign = 1 if lg["side"].startswith("buy") else -1
                    net += sign * float(mid)
                    g = q.get("greeks") or {}
                    leg_greeks.append({k: float(g.get(k) or 0) for k in ("delta", "theta", "vega")})
                self.portfolio.mark(pos.position_id, abs(net) if not pos.is_credit else net, leg_greeks)
            except TradierError:
                continue
        for intent in self.portfolio.check_exits():
            exits.append(intent)
            self.storage.log_trade(
                intent["position_id"], intent["symbol"], "manage", "close_intent",
                intent["current_premium"], 0, intent["unrealized_pnl"], intent["reason"]
            )
        return exits

    # ------------------------------------------------------------------ #
    #  IV history persistence
    # ------------------------------------------------------------------ #
    def _load_iv_history(self) -> None:
        if os.path.exists(_IV_HISTORY_FILE):
            try:
                data = json.load(open(_IV_HISTORY_FILE))
                for sym, vals in data.items():
                    self.iv_history[sym] = deque(vals, maxlen=ENGINE_CONFIG["history_days"])
            except Exception:
                pass

    def _save_iv_history(self) -> None:
        try:
            json.dump({k: list(v) for k, v in self.iv_history.items()},
                      open(_IV_HISTORY_FILE, "w"))
        except Exception:
            pass
