"""
robinhood_provider.py — data via Robinhood's MCP connector.

Architecture note: the Robinhood agentic MCP tools (get_option_chains,
get_option_quotes, get_equity_quotes, get_equity_historicals, get_option_positions)
are called by *Claude*, not by a long-running server process. So a headless bot on
your VPS cannot invoke them directly.

This provider therefore reads Robinhood data from a JSON cache that Claude refreshes
on demand (same pattern as the portfolio sync file). When you ask Claude to "refresh
Robinhood market data," it writes the latest chains/quotes to `rh_cache_file`, and
this provider serves them through the standard interface. If the cache is missing or
stale, it transparently falls back to yfinance so nothing breaks.

This keeps execution + data on Robinhood (per your requirement) while remaining
compatible with the human-in-the-loop, Claude-mediated design.
"""

from __future__ import annotations

import json
import os
import time

from .base import MarketDataProvider
from .yfinance_provider import YFinanceProvider
from ..indicators import options_metrics as om


class RobinhoodProvider(MarketDataProvider):
    name = "robinhood"

    def __init__(self, config: dict | None = None):
        config = config or {}
        self.cache_file = config.get("rh_cache_file", "rh_cache.json")
        self.max_age_sec = config.get("rh_cache_max_age_sec", 900)  # 15 min
        self._fallback = YFinanceProvider()  # used when cache absent/stale

    def _load_cache(self) -> dict | None:
        if not os.path.exists(self.cache_file):
            return None
        try:
            data = json.load(open(self.cache_file))
        except (ValueError, OSError):
            return None
        if time.time() - data.get("_ts", 0) > self.max_age_sec:
            return None  # stale -> fall back
        return data

    def get_quote(self, symbol: str) -> dict:
        cache = self._load_cache()
        if cache and symbol in cache.get("quotes", {}):
            return cache["quotes"][symbol]
        return self._fallback.get_quote(symbol)

    def get_daily_bars(self, symbol: str, days: int = 252):
        # Robinhood historicals are coarse; daily indicator history via yfinance
        # is fine and keeps indicators consistent. Override here if you cache bars.
        return self._fallback.get_daily_bars(symbol, days)

    def get_intraday_bars(self, symbol: str, interval: str = "5min"):
        return self._fallback.get_intraday_bars(symbol, interval)

    def get_chains(self, symbol: str, max_dte: int = 30):
        cache = self._load_cache()
        if cache and symbol in cache.get("chains", {}):
            merged = cache["chains"][symbol]
            chains_by_exp: dict[str, list] = {}
            for o in merged:
                chains_by_exp.setdefault(o["expiration_date"], []).append(o)
            exps = [e for e in chains_by_exp if 0 <= om.dte(e) <= max_dte]
            return {e: chains_by_exp[e] for e in exps}, \
                   [o for e in exps for o in chains_by_exp[e]], exps
        return self._fallback.get_chains(symbol, max_dte)
