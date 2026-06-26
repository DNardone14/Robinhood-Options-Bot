"""
unusual_flow.py — scan a chain for unusual options activity.

A contract is "unusual" when several of these line up:
  * volume / open_interest >= uf_vol_oi_ratio  (fresh positioning, not existing OI)
  * day's traded notional (volume * mid * 100) >= uf_min_premium  (large block)
  * IV elevated vs the underlying's ATM baseline (IV expansion)
  * trade printing at/above the ask (aggressive buyer) — approximated by
    last >= mid, since we don't have time-and-sales tape here.

Direction is inferred from option_type: heavy call buying -> bullish,
heavy put buying -> bearish. The single most notable contract per side is
returned as a signal.
"""

from __future__ import annotations

from .base import Signal, SignalType, Leg, mid_price, passes_liquidity
from ..indicators import options_metrics as om


class UnusualFlow:
    name = "unusual_flow"

    def __init__(self, cfg: dict):
        self.cfg = cfg

    def _score_contract(self, o: dict, atm_baseline_iv: float | None) -> float | None:
        c = self.cfg
        d = om.dte(o["expiration_date"])
        if not (c["uf_min_dte"] <= d <= c["uf_max_dte"]):
            return None
        voi = om.vol_to_oi(o)
        if voi is None or voi < c["uf_vol_oi_ratio"]:
            return None
        mid = mid_price(o)
        if mid <= 0:
            return None
        notional = float(o.get("volume") or 0) * mid * 100
        if notional < c["uf_min_premium"]:
            return None

        score = 0.0
        score += min(voi / (3 * c["uf_vol_oi_ratio"]), 1.0) * 0.4
        score += min(notional / (4 * c["uf_min_premium"]), 1.0) * 0.3
        # IV expansion vs baseline
        g = o.get("greeks") or {}
        iv = g.get("mid_iv") or g.get("smv_vol")
        if iv is not None and atm_baseline_iv:
            if float(iv) - atm_baseline_iv >= c["uf_iv_expansion"]:
                score += 0.2
        # aggressive print proxy: last trade at/above mid
        last = float(o.get("last") or 0)
        if last and last >= mid:
            score += 0.1
        return score

    def evaluate(self, symbol: str, ta: dict, merged_chain: list[dict],
                 atm_baseline_iv: float | None, earnings_flag: bool) -> list[Signal]:
        signals: list[Signal] = []
        best = {"call": (None, 0.0), "put": (None, 0.0)}
        for o in merged_chain:
            if not passes_liquidity(o, self.cfg):
                continue
            s = self._score_contract(o, atm_baseline_iv)
            if s is None:
                continue
            otype = o["option_type"]
            if s > best[otype][1]:
                best[otype] = (o, s)

        for otype, (o, score) in best.items():
            if o is None or score <= 0:
                continue
            premium = mid_price(o)
            g = o.get("greeks") or {}
            direction = "bullish" if otype == "call" else "bearish"
            leg = Leg(
                symbol=symbol,
                option_symbol=o.get("symbol", ""),
                strike=float(o["strike"]),
                expiration=o["expiration_date"],
                option_type=otype,
                side="buy_to_open",
                entry_premium=premium,
                delta=float(g.get("delta") or 0),
                gamma=float(g.get("gamma") or 0),
                theta=float(g.get("theta") or 0),
                vega=float(g.get("vega") or 0),
                iv=float(g.get("mid_iv")) if g.get("mid_iv") else None,
            )
            voi = om.vol_to_oi(o)
            signals.append(
                Signal(
                    symbol=symbol,
                    strategy=self.name,
                    signal_type=SignalType.CALL if otype == "call" else SignalType.PUT,
                    direction=direction,
                    legs=[leg],
                    net_premium=premium,
                    max_risk=premium * 100,
                    confidence=round(min(score, 0.99), 3),
                    underlying_price=ta["price"],
                    earnings_flag=earnings_flag,
                    notes=(
                        f"vol/OI {voi:.1f}x, "
                        f"${float(o.get('volume') or 0)*premium*100:,.0f} notional, "
                        f"{om.dte(o['expiration_date'])}DTE"
                    ),
                )
            )
        return signals
