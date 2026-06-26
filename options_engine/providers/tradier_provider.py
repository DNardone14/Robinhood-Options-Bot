"""
tradier_provider.py — wraps the existing Tradier client behind the provider API.

Use once your Tradier account is open and you have a Production token. Identical
interface to YFinanceProvider, so switching is just DATA_PROVIDER = "tradier".
"""

from __future__ import annotations

from .base import MarketDataProvider
from ..data.tradier import TradierClient, TradierError
from ..indicators import options_metrics as om
from ..config import TRADIER_CONFIG


class TradierProvider(MarketDataProvider):
    name = "tradier"

    def __init__(self, config: dict | None = None):
        self.client = TradierClient(config or TRADIER_CONFIG)
        self.r = TRADIER_CONFIG.get("risk_free_rate", 0.043)

    def get_quote(self, symbol: str) -> dict:
        try:
            q = self.client.get_quote(symbol)
        except TradierError:
            return {}
        if not q:
            return {}
        last = float(q.get("last") or q.get("close") or 0)
        prev = float(q.get("prevclose") or last)
        return {
            "symbol": symbol, "last": last, "prev_close": prev,
            "change_pct": (100.0 * (last - prev) / prev) if prev else 0.0,
            "volume": float(q.get("volume") or 0),
        }

    def get_daily_bars(self, symbol: str, days: int = 252):
        try:
            df = self.client.get_history(symbol, days)
            return df if (df is not None and not df.empty) else None
        except TradierError:
            return None

    def get_intraday_bars(self, symbol: str, interval: str = "5min"):
        try:
            return self.client.get_timesales(symbol, interval)
        except TradierError:
            return None

    def get_chains(self, symbol: str, max_dte: int = 30):
        try:
            exps = self.client.get_expirations(symbol)
        except TradierError:
            return {}, [], []
        chosen = [e for e in exps if 0 <= om.dte(e) <= max_dte]
        q = self.get_quote(symbol)
        underlying = q.get("last", 0.0)
        chains_by_exp, merged = {}, []
        for exp in chosen:
            try:
                chain = self.client.get_option_chain(symbol, exp, greeks=True)
            except TradierError:
                continue
            for o in chain:
                if TRADIER_CONFIG.get("compute_greeks_fallback", True):
                    g = om.ensure_greeks(o, underlying, self.r)
                    o.setdefault("greeks", {})
                    o["greeks"].update({k: g[k] for k in ("delta", "gamma", "theta", "vega")})
                    if g.get("iv") and not o["greeks"].get("mid_iv"):
                        o["greeks"]["mid_iv"] = g["iv"]
            chains_by_exp[exp] = chain
            merged.extend(chain)
        return chains_by_exp, merged, chosen
