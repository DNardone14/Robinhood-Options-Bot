"""
spreads.py — convert a single-leg directional Signal into a defined-risk
vertical spread (debit or credit), capping risk and cutting net IV/vega.

Debit vertical (directional, lower cost than long single leg):
  bullish  -> buy lower-strike call,  sell higher-strike call  (call debit spread)
  bearish  -> buy higher-strike put,  sell lower-strike put    (put debit spread)

Credit vertical (sell premium in the opposite direction, defined risk):
  bullish  -> sell higher-strike put, buy lower-strike put      (put credit spread)
  bearish  -> sell lower-strike call, buy higher-strike call     (call credit spread)

We pick the short strike `spread_width` dollars away from the long strike and
find the nearest listed strike in the same expiry.
"""

from __future__ import annotations

from .base import Signal, SignalType, Leg, mid_price


def _nearest_strike_contract(chain: list[dict], option_type: str, target_strike: float) -> dict | None:
    cands = [o for o in chain if o["option_type"] == option_type]
    if not cands:
        return None
    return min(cands, key=lambda o: abs(float(o["strike"]) - target_strike))


def build_vertical(base_signal: Signal, chain: list[dict], cfg: dict) -> Signal | None:
    """Return a new 2-leg Signal, or None if the offsetting strike isn't listed."""
    if not base_signal.legs:
        return None
    long_leg = base_signal.legs[0]
    width = cfg["spread_width"]
    spread_type = cfg["spread_type"]
    otype = long_leg.option_type
    exp_chain = [o for o in chain if o["expiration_date"] == long_leg.expiration]
    if not exp_chain:
        return None

    bullish = base_signal.direction == "bullish"

    if spread_type == "debit":
        # long the base leg; short one width further OTM (same type)
        long_contract = _nearest_strike_contract(exp_chain, otype, long_leg.strike)
        short_strike = long_leg.strike + width if otype == "call" else long_leg.strike - width
        short_contract = _nearest_strike_contract(exp_chain, otype, short_strike)
        if not short_contract or short_contract["strike"] == long_contract["strike"]:
            return None
        long_prem, short_prem = mid_price(long_contract), mid_price(short_contract)
        net = long_prem - short_prem               # debit you pay
        if net <= 0:
            return None
        max_risk = net * 100
        sig_type = SignalType.DEBIT_SPREAD
        legs = [
            _leg(long_contract, otype, "buy_to_open"),
            _leg(short_contract, otype, "sell_to_open"),
        ]
    else:  # credit
        otype = "put" if bullish else "call"        # sell premium opposite the move
        exp_chain = [o for o in chain if o["expiration_date"] == long_leg.expiration
                     and o["option_type"] == otype]
        if not exp_chain:
            return None
        short_strike = long_leg.strike
        short_contract = _nearest_strike_contract(exp_chain, otype, short_strike)
        long_strike = short_strike - width if otype == "put" else short_strike + width
        long_contract = _nearest_strike_contract(exp_chain, otype, long_strike)
        if not short_contract or not long_contract or short_contract["strike"] == long_contract["strike"]:
            return None
        short_prem, long_prem = mid_price(short_contract), mid_price(long_contract)
        net = short_prem - long_prem                # credit you collect (positive)
        if net <= 0:
            return None
        spread_w = abs(float(short_contract["strike"]) - float(long_contract["strike"]))
        max_risk = (spread_w - net) * 100           # defined risk on a credit spread
        sig_type = SignalType.CREDIT_SPREAD
        legs = [
            _leg(short_contract, otype, "sell_to_open"),
            _leg(long_contract, otype, "buy_to_open"),
        ]
        net = -net  # represent credit as negative premium (you receive cash)

    return Signal(
        symbol=base_signal.symbol,
        strategy=base_signal.strategy + "+vertical",
        signal_type=sig_type,
        direction=base_signal.direction,
        legs=legs,
        net_premium=net,
        max_risk=max_risk,
        confidence=base_signal.confidence,
        underlying_price=base_signal.underlying_price,
        expected_move_pct=base_signal.expected_move_pct,
        earnings_flag=base_signal.earnings_flag,
        notes=base_signal.notes + f" | {spread_type} vertical w={width}",
    )


def _leg(contract: dict, otype: str, side: str) -> Leg:
    g = contract.get("greeks") or {}
    return Leg(
        symbol=contract.get("underlying", contract.get("root_symbol", "")),
        option_symbol=contract.get("symbol", ""),
        strike=float(contract["strike"]),
        expiration=contract["expiration_date"],
        option_type=otype,
        side=side,
        entry_premium=mid_price(contract),
        delta=float(g.get("delta") or 0),
        gamma=float(g.get("gamma") or 0),
        theta=float(g.get("theta") or 0),
        vega=float(g.get("vega") or 0),
        iv=float(g.get("mid_iv")) if g.get("mid_iv") else None,
    )
