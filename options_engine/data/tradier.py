"""
tradier.py — thin Tradier Brokerage API client for market data.

Covers everything the engine needs:
  * get_quote(s)            — last/bid/ask/volume for underlyings or options
  * get_expirations         — option expiry dates for a symbol
  * get_option_chain        — full chain for one expiry, with Greeks + IV
  * get_history             — daily OHLCV bars
  * get_timesales           — intraday OHLCV bars (for VWAP / intraday TA)

Greeks: Tradier returns Greeks/IV on the chain when you pass greeks=true (the
data is supplied by ORATS). When a contract is missing Greeks we optionally fill
them with a local Black-Scholes calc (see indicators/options_metrics.py).

All methods raise TradierError on HTTP/credential problems so callers can decide
whether to fall back to yfinance.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

import requests

try:
    import pandas as pd
except ImportError:  # pragma: no cover
    pd = None


class TradierError(RuntimeError):
    """Raised when the Tradier API returns an error or no token is set."""


class TradierClient:
    def __init__(self, config: dict):
        self.base_url = config["base_url"].rstrip("/")
        self.token = config.get("token", "")
        self.account_id = config.get("account_id", "")
        self.timeout = config.get("timeout", 10)
        self._session = requests.Session()
        self._session.headers.update(
            {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}
        )

    # ------------------------------------------------------------------ #
    #  low-level GET with light retry
    # ------------------------------------------------------------------ #
    def _get(self, path: str, params: dict | None = None, retries: int = 2) -> dict:
        if not self.token:
            raise TradierError(
                "No TRADIER_TOKEN set. Export it or add it to your .env file."
            )
        url = f"{self.base_url}/{path.lstrip('/')}"
        last_err: Exception | None = None
        for attempt in range(retries + 1):
            try:
                resp = self._session.get(url, params=params, timeout=self.timeout)
                if resp.status_code == 401:
                    raise TradierError("Tradier 401 Unauthorized — bad/expired token.")
                if resp.status_code == 429:
                    time.sleep(1.0 * (attempt + 1))  # rate limited; back off
                    continue
                resp.raise_for_status()
                return resp.json() or {}
            except (requests.RequestException, ValueError) as exc:
                last_err = exc
                time.sleep(0.4 * (attempt + 1))
        raise TradierError(f"Tradier GET {path} failed: {last_err}")

    # ------------------------------------------------------------------ #
    #  quotes
    # ------------------------------------------------------------------ #
    def get_quotes(self, symbols: list[str] | str, greeks: bool = False) -> list[dict]:
        if isinstance(symbols, (list, tuple)):
            symbols = ",".join(symbols)
        data = self._get(
            "markets/quotes",
            {"symbols": symbols, "greeks": str(greeks).lower()},
        )
        quotes = (data.get("quotes") or {}).get("quote")
        if quotes is None:
            return []
        return quotes if isinstance(quotes, list) else [quotes]

    def get_quote(self, symbol: str, greeks: bool = False) -> dict | None:
        q = self.get_quotes(symbol, greeks=greeks)
        return q[0] if q else None

    # ------------------------------------------------------------------ #
    #  option chains
    # ------------------------------------------------------------------ #
    def get_expirations(self, symbol: str) -> list[str]:
        data = self._get(
            "markets/options/expirations",
            {"symbol": symbol, "includeAllRoots": "true", "strikes": "false"},
        )
        exps = (data.get("expirations") or {}).get("date")
        if exps is None:
            return []
        return exps if isinstance(exps, list) else [exps]

    def get_option_chain(self, symbol: str, expiration: str, greeks: bool = True) -> list[dict]:
        """Full chain (calls + puts) for one expiry. Each row includes strike,
        bid, ask, last, volume, open_interest, option_type, and (if greeks=True)
        a nested 'greeks' dict with delta/gamma/theta/vega/mid_iv."""
        data = self._get(
            "markets/options/chains",
            {"symbol": symbol, "expiration": expiration, "greeks": str(greeks).lower()},
        )
        opts = (data.get("options") or {}).get("option")
        if opts is None:
            return []
        return opts if isinstance(opts, list) else [opts]

    # ------------------------------------------------------------------ #
    #  historical / intraday bars
    # ------------------------------------------------------------------ #
    def get_history(self, symbol: str, days: int = 252) -> "pd.DataFrame":
        """Daily OHLCV bars as a DataFrame indexed by date."""
        if pd is None:
            raise TradierError("pandas is required for get_history().")
        data = self._get(
            "markets/history",
            {"symbol": symbol, "interval": "daily", "session_filter": "open"},
        )
        rows = (data.get("history") or {}).get("day")
        if not rows:
            return pd.DataFrame()
        rows = rows if isinstance(rows, list) else [rows]
        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").astype(
            {"open": float, "high": float, "low": float, "close": float, "volume": float}
        )
        return df.tail(days)

    def get_timesales(self, symbol: str, interval: str = "5min", session: str = "open") -> "pd.DataFrame":
        """Intraday OHLCV bars for the current session (used for VWAP)."""
        if pd is None:
            raise TradierError("pandas is required for get_timesales().")
        today = datetime.now().strftime("%Y-%m-%d")
        data = self._get(
            "markets/timesales",
            {
                "symbol": symbol,
                "interval": interval,
                "start": f"{today} 09:30",
                "end": f"{today} 16:00",
                "session_filter": session,
            },
        )
        rows = (data.get("series") or {}).get("data")
        if not rows:
            return pd.DataFrame()
        rows = rows if isinstance(rows, list) else [rows]
        df = pd.DataFrame(rows)
        df["time"] = pd.to_datetime(df["time"])
        df = df.set_index("time")
        for col in ("open", "high", "low", "close", "price", "volume", "vwap"):
            if col in df.columns:
                df[col] = df[col].astype(float)
        return df
