"""
directional_momentum.py — buy calls/puts on volume-confirmed breakouts.

Bullish setup (buy CALL):
  * EMA alignment: ema_fast > ema_mid > ema_slow (or fast>mid with price>slow)
  * price > VWAP (when intraday available) and price above mid-EMA
  * RSI > dm_rsi_long_min (momentum, not overbought blow-off)
  * MACD histogram > 0
  * volume_mult >= dm_volume_mult  (breakout confirmed by volume)

Bearish setup (buy PUT): mirror image.

Strike selection: contract nearest dm_target_delta within the delta band.
DTE selection: scaled by the expected move — bigger expected move => allow
slightly longer DTE so the move has room to play out; clamped to [min,max].
"""

from __future__ import annotations

from .base import (
    Signal,
    SignalType,
    Leg,
    select_contract_by_delta,
    mid_price,
)
from ..indicators import options_metrics as om


class DirectionalMomentum:
    name = "directional_momentum"

    def __init__(self, cfg: dict):
        self.cfg = cfg

    def _direction(self, ta: dict) -> str | None:
        c = self.cfg
        price = ta["price"]
        ema_f, ema_m, ema_s = ta["ema_fast"], ta["ema_mid"], ta["ema_slow"]
        rsi, hist = ta["rsi"], ta["macd_hist"]
        vmult = ta.get("volume_mult", 0.0)
        vwap = ta.get("vwap")

        if vmult < c["dm_volume_mult"]:
            return None  # no volume confirmation

        bull = (
            ema_f > ema_m > ema_s
            and price > ema_m
            and rsi > c["dm_rsi_long_min"]
            and hist > 0
            and (vwap is None or price >= vwap)
        )
        bear = (
            ema_f < ema_m < ema_s
            and price < ema_m
            and rsi < c["dm_rsi_short_max"]
            and hist < 0
            and (vwap is None or price <= vwap)
        )
        if bull:
            return "bullish"
        if bear:
            return "bearish"
        return None

    def _pick_expiry(self, expirations: list[str], expected_move_pct: float | None) -> list[str]:
        """Return candidate expiries within the DTE window, nearest first."""
        c = self.cfg
        cands = []
        for e in expirations:
            d = om.dte(e)
            if c["dm_min_dte"] <= d <= c["dm_max_dte"]:
                cands.append((d, e))
        cands.sort()
        return [e for _, e in cands]

    def evaluate(self, symbol: str, ta: dict, chains_by_exp: dict, expirations: list[str],
                 expected_move: dict | None, earnings_flag: bool) -> Signal | None:
        direction = self._direction(ta)
        if direction is None:
            return None

        option_type = "call" if direction == "bullish" else "put"
        em_pct = (expected_move or {}).get("expected_move_pct")
        contract = None
        chosen_exp = None
        for exp in self._pick_expiry(expirations, em_pct):
            chain = chains_by_exp.get(exp, [])
            contract = select_contract_by_delta(
                chain, option_type, self.cfg["dm_target_delta"], self.cfg["dm_delta_band"], self.cfg
            )
            if contract:
                chosen_exp = exp
                break
        if not contract:
            return None

        premium = mid_price(contract)
        if premium <= 0:
            return None
        g = contract.get("greeks") or {}
        leg = Leg(
            symbol=symbol,
            option_symbol=contract.get("symbol", ""),
            strike=float(contract["strike"]),
            expiration=chosen_exp,
            option_type=option_type,
            side="buy_to_open",
            entry_premium=premium,
            delta=float(g.get("delta") or 0),
            gamma=float(g.get("gamma") or 0),
            theta=float(g.get("theta") or 0),
            vega=float(g.get("vega") or 0),
            iv=float(g.get("mid_iv")) if g.get("mid_iv") else None,
        )

        # confidence: blend of trend strength, RSI distance, volume surge
        rsi_dist = abs(ta["rsi"] - 50) / 50.0
        vol_score = min(ta.get("volume_mult", 0) / (2 * self.cfg["dm_volume_mult"]), 1.0)
        confidence = round(min(0.4 + 0.3 * rsi_dist + 0.3 * vol_score, 0.99), 3)

        return Signal(
            symbol=symbol,
            strategy=self.name,
            signal_type=SignalType.CALL if option_type == "call" else SignalType.PUT,
            direction=direction,
            legs=[leg],
            net_premium=premium,
            max_risk=premium * 100,  # long option: max loss is the debit
            confidence=confidence,
            underlying_price=ta["price"],
            expected_move_pct=em_pct,
            earnings_flag=earnings_flag,
            notes=(
                f"EMA{'>' if direction=='bullish' else '<'}align, "
                f"RSI {ta['rsi']:.0f}, vol x{ta.get('volume_mult',0):.1f}, "
                f"Δ{leg.delta:+.2f}"
            ),
        )
