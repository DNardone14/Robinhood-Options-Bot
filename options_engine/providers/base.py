"""
base.py — provider interfaces shared by every data source.

MarketDataProvider normalizes quotes / chains / bars into the SAME dict shapes
the strategy engine already consumes, so strategies are source-agnostic:

  chain row -> {option_type, strike, expiration_date, symbol, bid, ask, last,
                volume, open_interest, greeks:{delta,gamma,theta,vega,mid_iv}}

PortfolioProvider returns account state (cash/buying power/positions) from a
local sync file the broker (Robinhood, via Claude) updates.
"""

from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from datetime import datetime


# --------------------------------------------------------------------------- #
#  market data
# --------------------------------------------------------------------------- #
class MarketDataProvider(ABC):
    name = "base"

    @abstractmethod
    def get_quote(self, symbol: str) -> dict:
        """{'symbol','last','prev_close','change_pct','volume'} or {} if unavailable."""

    @abstractmethod
    def get_daily_bars(self, symbol: str, days: int = 252):
        """pandas DataFrame indexed by date with open/high/low/close/volume."""

    @abstractmethod
    def get_intraday_bars(self, symbol: str, interval: str = "5min"):
        """pandas DataFrame of today's intraday bars, or empty DataFrame."""

    @abstractmethod
    def get_chains(self, symbol: str, max_dte: int = 30):
        """Return (chains_by_exp: dict[str,list], merged: list, expirations: list)."""


# --------------------------------------------------------------------------- #
#  portfolio / account
# --------------------------------------------------------------------------- #
@dataclass
class PositionRow:
    symbol: str                 # underlying
    option_symbol: str = ""     # OCC symbol (blank for equity)
    kind: str = "option"        # "option" | "equity"
    option_type: str = ""       # call/put
    strike: float = 0.0
    expiration: str = ""
    quantity: int = 0           # contracts (or shares for equity)
    entry_price: float = 0.0    # per-contract premium at entry
    current_price: float = 0.0
    delta: float = 0.0
    theta: float = 0.0
    gamma: float = 0.0
    vega: float = 0.0
    iv: float | None = None

    @property
    def dte(self) -> int | None:
        if not self.expiration:
            return None
        try:
            exp = datetime.strptime(self.expiration, "%Y-%m-%d").date()
            return (exp - datetime.now().date()).days
        except ValueError:
            return None

    @property
    def gain_pct(self) -> float:
        if self.entry_price == 0:
            return 0.0
        return 100.0 * (self.current_price - self.entry_price) / self.entry_price

    @property
    def market_value(self) -> float:
        mult = 100 if self.kind == "option" else 1
        return self.current_price * self.quantity * mult


@dataclass
class AccountSnapshot:
    total_value: float = 0.0
    cash: float = 0.0
    buying_power: float = 0.0
    day_pl: float = 0.0
    week_pl: float = 0.0
    month_pl: float = 0.0
    positions: list[PositionRow] = field(default_factory=list)
    updated_at: str = ""

    @property
    def open_options(self) -> int:
        return sum(1 for p in self.positions if p.kind == "option")

    @property
    def open_positions(self) -> int:
        return len(self.positions)

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


class PortfolioProvider:
    """Reads account state from a JSON sync file. The file is broker-agnostic;
    Robinhood data (via Claude/MCP) or a mock file both populate it."""

    def __init__(self, sync_file: str):
        self.sync_file = sync_file

    def get_snapshot(self) -> AccountSnapshot:
        if not os.path.exists(self.sync_file):
            return AccountSnapshot(updated_at="(no sync file)")
        try:
            raw = json.load(open(self.sync_file))
        except (ValueError, OSError):
            return AccountSnapshot(updated_at="(unreadable sync file)")
        positions = [PositionRow(**p) for p in raw.get("positions", [])]
        return AccountSnapshot(
            total_value=raw.get("total_value", 0.0),
            cash=raw.get("cash", 0.0),
            buying_power=raw.get("buying_power", 0.0),
            day_pl=raw.get("day_pl", 0.0),
            week_pl=raw.get("week_pl", 0.0),
            month_pl=raw.get("month_pl", 0.0),
            positions=positions,
            updated_at=raw.get("updated_at", "(no timestamp)"),
        )

    def write_snapshot(self, snapshot: AccountSnapshot) -> None:
        data = snapshot.to_dict()
        data["updated_at"] = data.get("updated_at") or datetime.now().isoformat(timespec="seconds")
        json.dump(data, open(self.sync_file, "w"), indent=2)
