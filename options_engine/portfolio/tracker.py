"""
tracker.py — in-memory book of open option positions + P&L and Greeks rollups.

Positions are opened from approved signals (or synced from the broker) and marked
to market with fresh option quotes each scan. Handles:
  * unrealized / realized P&L
  * net portfolio Greeks (delta/theta/vega in dollar terms)
  * auto stop-loss / take-profit triggers -> returns exit intents
  * expiry / early-assignment risk flags (short ITM legs near expiry)

This mirrors positions; it does not place closing orders itself — it emits exit
intents that flow back through the Robinhood router, same as entries.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from ..indicators import options_metrics as om


@dataclass
class Position:
    position_id: str
    symbol: str
    strategy: str
    signal_type: str
    legs: list[dict]                 # snapshot of Leg dicts at entry
    quantity: int
    entry_premium: float             # net, per contract
    max_risk: float                  # $ per contract
    profit_target: float
    stop_loss: float
    opened_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    current_premium: float = 0.0
    unrealized_pnl: float = 0.0
    status: str = "open"             # open / closed
    realized_pnl: float = 0.0
    net_delta: float = 0.0
    net_theta: float = 0.0
    net_vega: float = 0.0

    @property
    def is_credit(self) -> bool:
        return self.entry_premium < 0


class PortfolioTracker:
    def __init__(self):
        self.positions: dict[str, Position] = {}
        self._seq = 0
        self.realized_day_pnl = 0.0

    # ----- lifecycle ------------------------------------------------- #
    def open_from_signal(self, signal) -> Position:
        self._seq += 1
        pid = f"{signal.symbol}-{self._seq}"
        pos = Position(
            position_id=pid,
            symbol=signal.symbol,
            strategy=signal.strategy,
            signal_type=signal.signal_type.value,
            legs=[self._leg_dict(lg) for lg in signal.legs],
            quantity=signal.quantity,
            entry_premium=signal.net_premium,
            max_risk=signal.max_risk,
            profit_target=signal.profit_target,
            stop_loss=signal.stop_loss,
            current_premium=signal.net_premium,
        )
        self.positions[pid] = pos
        return pos

    @staticmethod
    def _leg_dict(lg) -> dict:
        return {
            "option_symbol": lg.option_symbol,
            "strike": lg.strike,
            "expiration": lg.expiration,
            "option_type": lg.option_type,
            "side": lg.side,
            "quantity": lg.quantity,
            "entry_premium": lg.entry_premium,
            "delta": lg.delta,
            "gamma": lg.gamma,
            "theta": lg.theta,
            "vega": lg.vega,
        }

    def close(self, position_id: str, exit_premium: float, reason: str = "manual") -> float:
        pos = self.positions.get(position_id)
        if not pos or pos.status == "closed":
            return 0.0
        sign = 1.0  # long premium: profit = (exit - entry)
        if pos.is_credit:
            # credit: profit when premium shrinks; pnl = (entry_abs - exit_abs)
            pnl = (abs(pos.entry_premium) - abs(exit_premium)) * 100 * pos.quantity
        else:
            pnl = (exit_premium - pos.entry_premium) * 100 * pos.quantity * sign
        pos.status = "closed"
        pos.realized_pnl = pnl
        pos.current_premium = exit_premium
        self.realized_day_pnl += pnl
        return pnl

    # ----- mark to market -------------------------------------------- #
    def mark(self, position_id: str, current_premium: float,
             leg_greeks: list[dict] | None = None) -> None:
        pos = self.positions.get(position_id)
        if not pos or pos.status == "closed":
            return
        pos.current_premium = current_premium
        if pos.is_credit:
            pos.unrealized_pnl = (abs(pos.entry_premium) - abs(current_premium)) * 100 * pos.quantity
        else:
            pos.unrealized_pnl = (current_premium - pos.entry_premium) * 100 * pos.quantity
        if leg_greeks:
            d = t = v = 0.0
            for lg_meta, g in zip(pos.legs, leg_greeks):
                s = 1 if lg_meta["side"].startswith("buy") else -1
                d += s * g.get("delta", 0) * pos.quantity * 100
                t += s * g.get("theta", 0) * pos.quantity * 100
                v += s * g.get("vega", 0) * pos.quantity * 100
            pos.net_delta, pos.net_theta, pos.net_vega = d, t, v

    # ----- exit logic ------------------------------------------------ #
    def check_exits(self, now: datetime | None = None) -> list[dict]:
        """Return exit intents for positions hitting TP/SL or expiry/assignment risk."""
        now = now or datetime.now()
        intents = []
        for pos in self.open_positions():
            prem = abs(pos.current_premium)
            reason = None
            if pos.is_credit:
                if prem <= pos.profit_target:
                    reason = "take_profit"
                elif prem >= pos.stop_loss:
                    reason = "stop_loss"
            else:
                if prem >= pos.profit_target:
                    reason = "take_profit"
                elif prem <= pos.stop_loss:
                    reason = "stop_loss"
            # expiry / assignment risk: any short leg within 1 DTE and ITM
            for lg in pos.legs:
                if om.dte(lg["expiration"], now) <= 1 and lg["side"].startswith("sell"):
                    reason = reason or "expiry_assignment_risk"
            if reason:
                intents.append({
                    "position_id": pos.position_id,
                    "symbol": pos.symbol,
                    "reason": reason,
                    "current_premium": pos.current_premium,
                    "unrealized_pnl": pos.unrealized_pnl,
                })
        return intents

    # ----- rollups --------------------------------------------------- #
    def open_positions(self) -> list[Position]:
        return [p for p in self.positions.values() if p.status == "open"]

    def book_greeks(self) -> dict:
        d = t = v = 0.0
        for p in self.open_positions():
            d += p.net_delta
            t += p.net_theta
            v += p.net_vega
        return {"delta": d, "theta": t, "vega": v}

    def unrealized_pnl(self) -> float:
        return sum(p.unrealized_pnl for p in self.open_positions())

    def win_rate(self) -> tuple[float, int, int]:
        closed = [p for p in self.positions.values() if p.status == "closed"]
        wins = sum(1 for p in closed if p.realized_pnl > 0)
        n = len(closed)
        return (wins / n if n else 0.0), wins, n
