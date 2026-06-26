"""
options_metrics.py — options-specific math.

Two groups of functions:

1. Black-Scholes Greeks + implied vol (SciPy). Used as a FALLBACK when Tradier
   doesn't supply Greeks, and to sanity-check the feed.

2. Chain-level analytics the strategies consume:
     - iv_rank / iv_percentile   (needs a history of ATM IV)
     - put_call_ratio            (volume + OI based)
     - vol_to_oi                 (per-contract unusual-activity gauge)
     - iv_skew                   (25-delta put IV minus call IV proxy)
     - expected_move             (from the ATM straddle price)
     - atm_iv                    (interpolated at-the-money IV)
"""

from __future__ import annotations

import math
from datetime import date, datetime

import numpy as np

try:
    from scipy.stats import norm
    _NORM_CDF = norm.cdf
    _NORM_PDF = norm.pdf
except ImportError:  # pragma: no cover — fall back to math.erf
    def _NORM_CDF(x):  # type: ignore
        return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

    def _NORM_PDF(x):  # type: ignore
        return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


# --------------------------------------------------------------------------- #
#  Black-Scholes
# --------------------------------------------------------------------------- #
def _d1_d2(S, K, T, r, sigma):
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return None, None
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return d1, d2


def bs_price(S, K, T, r, sigma, option_type="call") -> float:
    d1, d2 = _d1_d2(S, K, T, r, sigma)
    if d1 is None:
        # intrinsic value at expiry / degenerate input
        return max(0.0, (S - K) if option_type == "call" else (K - S))
    if option_type == "call":
        return S * _NORM_CDF(d1) - K * math.exp(-r * T) * _NORM_CDF(d2)
    return K * math.exp(-r * T) * _NORM_CDF(-d2) - S * _NORM_CDF(-d1)


def bs_greeks(S, K, T, r, sigma, option_type="call") -> dict:
    """Returns delta, gamma, theta (per day), vega (per 1 vol point), rho."""
    d1, d2 = _d1_d2(S, K, T, r, sigma)
    if d1 is None:
        return {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0, "rho": 0.0}
    pdf = _NORM_PDF(d1)
    sqrtT = math.sqrt(T)
    if option_type == "call":
        delta = _NORM_CDF(d1)
        theta = (-(S * pdf * sigma) / (2 * sqrtT)
                 - r * K * math.exp(-r * T) * _NORM_CDF(d2))
        rho = K * T * math.exp(-r * T) * _NORM_CDF(d2)
    else:
        delta = _NORM_CDF(d1) - 1.0
        theta = (-(S * pdf * sigma) / (2 * sqrtT)
                 + r * K * math.exp(-r * T) * _NORM_CDF(-d2))
        rho = -K * T * math.exp(-r * T) * _NORM_CDF(-d2)
    gamma = pdf / (S * sigma * sqrtT)
    vega = S * pdf * sqrtT
    return {
        "delta": delta,
        "gamma": gamma,
        "theta": theta / 365.0,     # per-calendar-day
        "vega": vega / 100.0,       # per 1 vol point
        "rho": rho / 100.0,
    }


def implied_vol(price, S, K, T, r, option_type="call", tol=1e-5, max_iter=100) -> float | None:
    """Bisection IV solver. Robust enough for chain backfill."""
    if price <= 0 or T <= 0:
        return None
    intrinsic = max(0.0, (S - K) if option_type == "call" else (K - S))
    if price < intrinsic - 1e-6:
        return None
    lo, hi = 1e-4, 5.0
    for _ in range(max_iter):
        mid = 0.5 * (lo + hi)
        diff = bs_price(S, K, T, r, mid, option_type) - price
        if abs(diff) < tol:
            return mid
        if diff > 0:
            hi = mid
        else:
            lo = mid
    return 0.5 * (lo + hi)


def year_fraction(expiration: str, now: datetime | None = None) -> float:
    """ACT/365 time to expiry from an 'YYYY-MM-DD' string."""
    now = now or datetime.now()
    exp = datetime.strptime(expiration, "%Y-%m-%d")
    # day-trading contracts expire at the close; add the trading day
    seconds = (exp - now).total_seconds() + 16 * 3600
    return max(seconds / (365.0 * 24 * 3600), 1e-6)


def dte(expiration: str, now: datetime | None = None) -> int:
    now = now or datetime.now()
    exp = datetime.strptime(expiration, "%Y-%m-%d").date()
    ref = now.date() if isinstance(now, datetime) else now
    return (exp - ref).days


# --------------------------------------------------------------------------- #
#  Greeks backfill on a Tradier chain row
# --------------------------------------------------------------------------- #
def ensure_greeks(option: dict, underlying_price: float, r: float) -> dict:
    """Return a greeks dict for a Tradier chain row, computing via Black-Scholes
    if the feed omitted them. Mutates nothing; returns the greeks dict."""
    g = option.get("greeks") or {}
    have = all(g.get(k) is not None for k in ("delta", "gamma", "theta", "vega"))
    if have:
        return {
            "delta": float(g["delta"]),
            "gamma": float(g["gamma"]),
            "theta": float(g["theta"]),
            "vega": float(g["vega"]),
            "iv": float(g.get("mid_iv") or g.get("smv_vol") or 0.0) or None,
        }
    # backfill
    K = float(option["strike"])
    otype = option["option_type"]
    T = year_fraction(option["expiration_date"])
    bid, ask = option.get("bid") or 0.0, option.get("ask") or 0.0
    mid = (float(bid) + float(ask)) / 2.0 or float(option.get("last") or 0.0)
    iv = implied_vol(mid, underlying_price, K, T, r, otype) if mid else None
    if iv is None:
        iv = float(g.get("mid_iv") or 0.30)  # last resort default
    greeks = bs_greeks(underlying_price, K, T, r, iv, otype)
    greeks["iv"] = iv
    return greeks


# --------------------------------------------------------------------------- #
#  Chain-level analytics
# --------------------------------------------------------------------------- #
def atm_iv(chain: list[dict], underlying_price: float) -> float | None:
    """IV of the strike nearest the underlying (avg of call+put if both present)."""
    if not chain:
        return None
    nearest = min(chain, key=lambda o: abs(float(o["strike"]) - underlying_price))
    strike = float(nearest["strike"])
    ivs = []
    for o in chain:
        if abs(float(o["strike"]) - strike) < 1e-6:
            g = o.get("greeks") or {}
            iv = g.get("mid_iv") or g.get("smv_vol")
            if iv:
                ivs.append(float(iv))
    return float(np.mean(ivs)) if ivs else None


def iv_rank(current_iv: float, iv_history: list[float]) -> float | None:
    """Where current IV sits between its 1-year min and max (0–100)."""
    hist = [v for v in iv_history if v is not None]
    if current_iv is None or len(hist) < 2:
        return None
    lo, hi = min(hist), max(hist)
    if hi <= lo:
        return None
    return float(100.0 * (current_iv - lo) / (hi - lo))


def iv_percentile(current_iv: float, iv_history: list[float]) -> float | None:
    """% of days over the lookback that IV was below today's IV (0–100)."""
    hist = [v for v in iv_history if v is not None]
    if current_iv is None or not hist:
        return None
    below = sum(1 for v in hist if v < current_iv)
    return float(100.0 * below / len(hist))


def put_call_ratio(chain: list[dict]) -> dict:
    """Volume- and OI-based put/call ratios for a chain (or merged chains)."""
    cv = pv = coi = poi = 0.0
    for o in chain:
        vol = float(o.get("volume") or 0)
        oi = float(o.get("open_interest") or 0)
        if o["option_type"] == "call":
            cv += vol
            coi += oi
        else:
            pv += vol
            poi += oi
    return {
        "pcr_volume": (pv / cv) if cv else None,
        "pcr_oi": (poi / coi) if coi else None,
        "call_volume": cv,
        "put_volume": pv,
    }


def vol_to_oi(option: dict) -> float | None:
    oi = float(option.get("open_interest") or 0)
    vol = float(option.get("volume") or 0)
    if oi <= 0:
        return None
    return vol / oi


def iv_skew(chain: list[dict], underlying_price: float) -> float | None:
    """Crude 25-delta skew proxy: OTM put IV minus OTM call IV (in vol points).
    Positive => puts richer than calls (fear / downside demand)."""
    put_ivs, call_ivs = [], []
    for o in chain:
        g = o.get("greeks") or {}
        iv = g.get("mid_iv") or g.get("smv_vol")
        delta = g.get("delta")
        if iv is None or delta is None:
            continue
        iv, delta = float(iv), float(delta)
        if o["option_type"] == "put" and -0.35 <= delta <= -0.15:
            put_ivs.append(iv)
        elif o["option_type"] == "call" and 0.15 <= delta <= 0.35:
            call_ivs.append(iv)
    if not put_ivs or not call_ivs:
        return None
    return float(np.mean(put_ivs) - np.mean(call_ivs))


def expected_move(chain: list[dict], underlying_price: float) -> dict | None:
    """Expected move from the ATM straddle: ~ (ATM call mid + ATM put mid).
    Returns dollar move and percent of spot."""
    if not chain:
        return None
    strike = min(chain, key=lambda o: abs(float(o["strike"]) - underlying_price))["strike"]
    strike = float(strike)
    call_mid = put_mid = None
    for o in chain:
        if abs(float(o["strike"]) - strike) > 1e-6:
            continue
        mid = ((o.get("bid") or 0) + (o.get("ask") or 0)) / 2.0 or (o.get("last") or 0)
        if o["option_type"] == "call":
            call_mid = float(mid)
        else:
            put_mid = float(mid)
    if call_mid is None or put_mid is None:
        return None
    straddle = call_mid + put_mid
    move = 0.85 * straddle  # standard ~0.85x straddle approximation of 1-sigma
    return {
        "straddle": straddle,
        "expected_move_dollars": move,
        "expected_move_pct": (move / underlying_price) if underlying_price else None,
        "atm_strike": strike,
    }
