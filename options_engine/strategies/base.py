"""
base.py — shared types and contract-selection helpers for strategies.

Signal is the single object every strategy emits. The risk manager fills in
position size; the execution router turns it into a Robinhood order ticket.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Any


class SignalType(str, Enum):
    CALL = "call"
    PUT = "put"
    DEBIT_SPREAD = "debit_spread"
    CREDIT_SPREAD = "credit_spread"


@dataclass
class Leg:
    """One option leg of a trade."""
    symbol: str            # underlying
    option_symbol: str     # OCC symbol, e.g. AAPL250620C00190000
    strike: float
    expiration: str        # YYYY-MM-DD
    option_type: str       # call / put
    side: str              # buy_to_open / sell_to_open
    quantity: int = 1
    entry_premium: float = 0.0
    delta: float = 0.0
    gamma: float = 0.0
    theta: float = 0.0
    vega: float = 0.0
    iv: float | None = None


@dataclass
class Signal:
    symbol: str
    strategy: str
    signal_type: SignalType
    direction: str                 # "bullish" / "bearish"
    legs: list[Leg] = field(default_factory=list)
    # net trade economics (per 1 spread/contract, in dollars of premium)
    net_premium: float = 0.0       # debit (>0 you pay) or credit (<0 you collect)
    max_risk: float = 0.0          # $ at risk per contract (premium for longs)
    profit_target: float = 0.0     # premium level to take profit
    stop_loss: float = 0.0         # premium level to stop out
    quantity: int = 1              # contracts (set by risk manager)
    # context / scoring
    confidence: float = 0.0        # 0–1 heuristic score
    notes: str = ""
    underlying_price: float = 0.0
    expected_move_pct: float | None = None
    earnings_flag: bool = False
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    @property
    def net_delta(self) -> float:
        return sum((1 if lg.side.startswith("buy") else -1) * lg.delta * lg.quantity for lg in self.legs)

    @property
    def net_theta(self) -> float:
        return sum((1 if lg.side.startswith("buy") else -1) * lg.theta * lg.quantity for lg in self.legs)

    @property
    def net_vega(self) -> float:
        return sum((1 if lg.side.startswith("buy") else -1) * lg.vega * lg.quantity for lg in self.legs)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["signal_type"] = self.signal_type.value
        d["net_delta"] = round(self.net_delta, 4)
        d["net_theta"] = round(self.net_theta, 4)
        d["net_vega"] = round(self.net_vega, 4)
        return d


# --------------------------------------------------------------------------- #
#  contract selection helpers
# --------------------------------------------------------------------------- #
def mid_price(option: dict) -> float:
    bid = float(option.get("bid") or 0)
    ask = float(option.get("ask") or 0)
    if bid and ask:
        return (bid + ask) / 2.0
    return float(option.get("last") or 0)


def bid_ask_spread_pct(option: dict) -> float | None:
    bid = float(option.get("bid") or 0)
    ask = float(option.get("ask") or 0)
    mid = (bid + ask) / 2.0
    if mid <= 0:
        return None
    return (ask - bid) / mid


def passes_liquidity(option: dict, cfg: dict) -> bool:
    vol = float(option.get("volume") or 0)
    oi = float(option.get("open_interest") or 0)
    if vol < cfg["min_option_volume"] and oi < cfg["min_open_interest"]:
        return False
    sp = bid_ask_spread_pct(option)
    if sp is None or sp > cfg["max_bid_ask_spread_pct"]:
        return False
    return True


def select_contract_by_delta(
    chain: list[dict],
    option_type: str,
    target_delta: float,
    delta_band: tuple[float, float],
    cfg: dict,
) -> dict | None:
    """Pick the liquid contract whose |delta| is closest to target within band."""
    lo, hi = delta_band
    best, best_err = None, 1e9
    for o in chain:
        if o["option_type"] != option_type:
            continue
        g = o.get("greeks") or {}
        delta = g.get("delta")
        if delta is None:
            continue
        ad = abs(float(delta))
        if not (lo <= ad <= hi):
            continue
        if not passes_liquidity(o, cfg):
            continue
        err = abs(ad - target_delta)
        if err < best_err:
            best, best_err = o, err
    return best
