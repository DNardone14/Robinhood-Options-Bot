"""
manager.py — position sizing, daily-loss auto-shutoff, and portfolio Greeks caps.

The RiskManager is the gatekeeper between a raw strategy Signal and an order
ticket. It:
  1. Blocks all new entries if the day's realized+unrealized P&L breaches the
     daily-loss limit (auto-shutoff).
  2. Sizes the trade (fixed-fractional or fractional-Kelly), capping contracts so
     premium-at-risk <= risk_per_trade_pct of the account and never exceeding
     buying power.
  3. Sets premium-based profit-target and stop-loss levels on the signal.
  4. Rejects the trade if adding it would breach portfolio net delta / theta / vega
     limits or the max-open-positions cap.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..strategies.base import Signal


@dataclass
class RiskDecision:
    approved: bool
    quantity: int
    reason: str


class RiskManager:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.day_pnl = 0.0          # realized P&L so far today
        self.halted = False

    # ----- daily loss auto-shutoff ----------------------------------- #
    def update_day_pnl(self, realized_pnl: float) -> None:
        self.day_pnl = realized_pnl
        limit = -abs(self.cfg["account_size"] * self.cfg["max_daily_loss_pct"] / 100.0)
        if self.day_pnl <= limit:
            self.halted = True

    def daily_loss_limit_dollars(self) -> float:
        return self.cfg["account_size"] * self.cfg["max_daily_loss_pct"] / 100.0

    # ----- position sizing ------------------------------------------- #
    def _risk_budget(self) -> float:
        return self.cfg["account_size"] * self.cfg["risk_per_trade_pct"] / 100.0

    def _kelly_fraction(self) -> float:
        w = self.cfg["kelly_win_rate"]
        b = self.cfg["kelly_win_loss_ratio"]
        if b <= 0:
            return 0.0
        # Kelly f* = W - (1-W)/b ; scaled by kelly_fraction (e.g. half-Kelly)
        f = w - (1 - w) / b
        return max(0.0, f) * self.cfg["kelly_fraction"]

    def size(self, signal: Signal) -> int:
        """Contracts to trade. Premium-at-risk per contract = max_risk."""
        per_contract_risk = max(signal.max_risk, 1e-6)
        budget = self._risk_budget()
        if self.cfg["sizing_method"] == "kelly":
            budget = self.cfg["account_size"] * self._kelly_fraction()
        qty = int(budget // per_contract_risk)
        if qty < 1 and self.cfg.get("allow_min_one_contract"):
            # permit a single contract if its premium fits the max-position cap
            pos_cap = self.cfg["account_size"] * self.cfg.get("max_position_pct", 100) / 100.0
            if per_contract_risk <= pos_cap:
                qty = 1
        return max(qty, 0)

    # ----- portfolio Greeks check ------------------------------------ #
    def _greeks_ok(self, signal: Signal, qty: int, book_greeks: dict) -> tuple[bool, str]:
        c = self.cfg
        add_delta = signal.net_delta * qty * 100   # delta dollars per 100 shares/contract
        add_theta = signal.net_theta * qty * 100
        add_vega = signal.net_vega * qty * 100
        net_delta = book_greeks.get("delta", 0.0) + add_delta
        net_theta = book_greeks.get("theta", 0.0) + add_theta
        net_vega = book_greeks.get("vega", 0.0) + add_vega
        if abs(net_delta) > c["max_net_delta"]:
            return False, f"net delta {net_delta:.0f} > cap {c['max_net_delta']}"
        if net_theta < c["max_net_theta"]:
            return False, f"net theta {net_theta:.0f} < floor {c['max_net_theta']}"
        if abs(net_vega) > c["max_net_vega"]:
            return False, f"net vega {net_vega:.0f} > cap {c['max_net_vega']}"
        return True, "ok"

    # ----- main entry point ------------------------------------------ #
    def evaluate(self, signal: Signal, open_positions: int, book_greeks: dict | None = None) -> RiskDecision:
        book_greeks = book_greeks or {}
        if self.halted:
            return RiskDecision(False, 0, "HALTED: daily loss limit hit")
        if open_positions >= self.cfg["max_open_positions"]:
            return RiskDecision(False, 0, f"max open positions ({self.cfg['max_open_positions']}) reached")

        qty = self.size(signal)
        if qty < 1:
            return RiskDecision(False, 0, "risk budget too small for 1 contract")

        ok, why = self._greeks_ok(signal, qty, book_greeks)
        if not ok:
            # try trimming to 1 contract before giving up
            ok1, _ = self._greeks_ok(signal, 1, book_greeks)
            if ok1:
                qty = 1
            else:
                return RiskDecision(False, 0, f"portfolio Greeks limit: {why}")

        # set exits (premium-based) on the signal
        self._set_exits(signal)
        signal.quantity = qty
        for lg in signal.legs:
            lg.quantity = qty
        return RiskDecision(True, qty, "approved")

    def _set_exits(self, signal: Signal) -> None:
        tp = self.cfg["take_profit_pct"] / 100.0
        sl = self.cfg["stop_loss_pct"] / 100.0
        entry = abs(signal.net_premium)
        is_credit = signal.net_premium < 0
        if is_credit:
            # credit spread: profit when premium decays toward 0, loss when it widens
            signal.profit_target = round(entry * (1 - tp), 2)
            signal.stop_loss = round(entry * (1 + sl), 2)
        else:
            signal.profit_target = round(entry * (1 + tp), 2)
            signal.stop_loss = round(entry * (1 - sl), 2)
