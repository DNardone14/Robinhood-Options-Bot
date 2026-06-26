"""
yfinance_provider.py — free market data that works with no broker account.

Quotes, daily bars, and option chains from Yahoo Finance. yfinance chains include
implied volatility but NOT Greeks, so we compute delta/gamma/theta/vega locally
with Black-Scholes (indicators.options_metrics) using Yahoo's IV. Output matches
the normalized chain shape the strategies expect.

Note: Yahoo data is delayed ~15 min and intraday history is limited; good enough
to build/test the whole assistant now and validate signals. Swap to Tradier or
Robinhood for production-grade real-time data with no code change elsewhere.
"""

from __future__ import annotations

from datetime import datetime

from .base import MarketDataProvider
from ..indicators import options_metrics as om
from ..config import TRADIER_CONFIG

try:
    import yfinance as yf
    import pandas as pd
except ImportError:  # pragma: no cover
    yf = None
    pd = None


class YFinanceProvider(MarketDataProvider):
    name = "yfinance"

    def __init__(self):
        if yf is None:
            raise ImportError("yfinance + pandas are required for YFinanceProvider")
        self._tickers: dict[str, "yf.Ticker"] = {}
        self.r = TRADIER_CONFIG.get("risk_free_rate", 0.043)

    def _tkr(self, symbol: str):
        if symbol not in self._tickers:
            self._tickers[symbol] = yf.Ticker(symbol)
        return self._tickers[symbol]

    # ----- quotes -------------------------------------------------------- #
    def get_quote(self, symbol: str) -> dict:
        try:
            t = self._tkr(symbol)
            fi = getattr(t, "fast_info", None)
            last = float(fi["lastPrice"]) if fi and "lastPrice" in fi else None
            prev = float(fi["previousClose"]) if fi and "previousClose" in fi else None
            if last is None:
                hist = t.history(period="2d")
                if hist.empty:
                    return {}
                last = float(hist["Close"].iloc[-1])
                prev = float(hist["Close"].iloc[-2]) if len(hist) > 1 else last
            change_pct = (100.0 * (last - prev) / prev) if prev else 0.0
            vol = float(fi["lastVolume"]) if fi and "lastVolume" in fi else 0.0
            return {"symbol": symbol, "last": last, "prev_close": prev,
                    "change_pct": change_pct, "volume": vol}
        except Exception:
            return {}

    # ----- bars ---------------------------------------------------------- #
    def get_daily_bars(self, symbol: str, days: int = 252):
        try:
            df = self._tkr(symbol).history(period="1y", interval="1d", auto_adjust=False)
            if df is None or df.empty:
                return None
            df = df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]]
            return df.tail(days)
        except Exception:
            return None

    def get_intraday_bars(self, symbol: str, interval: str = "5min"):
        iv_map = {"1min": "1m", "5min": "5m", "15min": "15m"}
        yf_int = iv_map.get(interval, "5m")
        try:
            df = self._tkr(symbol).history(period="1d", interval=yf_int, auto_adjust=False)
            if df is None or df.empty:
                return pd.DataFrame()
            return df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]]
        except Exception:
            return pd.DataFrame() if pd is not None else None

    # ----- chains -------------------------------------------------------- #
    def get_chains(self, symbol: str, max_dte: int = 30):
        t = self._tkr(symbol)
        q = self.get_quote(symbol)
        underlying = q.get("last")
        if underlying is None:
            return {}, [], []
        try:
            exps = list(t.options or [])
        except Exception:
            return {}, [], []
        chosen = [e for e in exps if 0 <= om.dte(e) <= max_dte]
        chains_by_exp, merged = {}, []
        for exp in chosen:
            try:
                oc = t.option_chain(exp)
            except Exception:
                continue
            rows = self._normalize(oc.calls, "call", exp, underlying) + \
                   self._normalize(oc.puts, "put", exp, underlying)
            chains_by_exp[exp] = rows
            merged.extend(rows)
        return chains_by_exp, merged, chosen

    def _normalize(self, df, option_type: str, exp: str, underlying: float) -> list[dict]:
        out = []
        T = om.year_fraction(exp)
        for _, row in df.iterrows():
            strike = float(row.get("strike", 0) or 0)
            bid = float(row.get("bid", 0) or 0)
            ask = float(row.get("ask", 0) or 0)
            last = float(row.get("lastPrice", 0) or 0)
            iv = float(row.get("impliedVolatility", 0) or 0) or None
            mid = (bid + ask) / 2.0 or last
            if iv is None and mid:
                iv = om.implied_vol(mid, underlying, strike, T, self.r, option_type)
            greeks = om.bs_greeks(underlying, strike, T, self.r, iv or 0.3, option_type)
            out.append({
                "option_type": option_type,
                "strike": strike,
                "expiration_date": exp,
                "symbol": row.get("contractSymbol", ""),
                "bid": bid, "ask": ask, "last": last,
                "volume": float(row.get("volume", 0) or 0),
                "open_interest": float(row.get("openInterest", 0) or 0),
                "greeks": {
                    "delta": greeks["delta"], "gamma": greeks["gamma"],
                    "theta": greeks["theta"], "vega": greeks["vega"],
                    "mid_iv": iv,
                },
            })
        return out
