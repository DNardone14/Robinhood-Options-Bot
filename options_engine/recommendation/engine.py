"""
engine.py — the AI decision layer: 0–100 confidence + position recommendations.

Two jobs:

1. score_setup(ta, metrics, signal) -> 0–100 confidence for a NEW opportunity,
   blending technicals (RSI, MACD, EMA alignment, volume, Bollinger, VWAP) with
   options metrics (IV rank, expected move, vol/OI) and the market regime.

2. assess_position(pos, ta, market) -> recommendation for an OPEN position:
   HOLD / SELL / REDUCE POSITION / MONITOR CLOSELY, with a confidence and reason.

3. read_market(spy_ta, qqq_ta, vix) -> MarketRead: sentiment 0–100, bullish/bearish
   classification, and trend notes for SPY/QQQ. Used by the briefing + as a regime
   input to setup scoring.

All thresholds come from RECO_CONFIG so they're tunable without code changes.
"""

from __future__ import annotations

from dataclasses import dataclass


def confidence_band(score: float) -> str:
    if score >= 90:
        return "Very High"
    if score >= 75:
        return "High"
    if score >= 60:
        return "Moderate"
    return "Low"


@dataclass
class MarketRead:
    sentiment: float            # 0–100 (50 neutral)
    classification: str         # "Bullish" / "Bearish" / "Neutral"
    vix: float | None
    spy_note: str
    qqq_note: str
    risk_level: str             # "Low" / "Elevated" / "High" (from VIX)


def _trend_note(ta: dict) -> tuple[str, float]:
    """Return (human note, directional score -1..+1) for an underlying's TA."""
    price = ta.get("price", 0)
    ef, em, es = ta.get("ema_fast", 0), ta.get("ema_mid", 0), ta.get("ema_slow", 0)
    rsi = ta.get("rsi", 50)
    score = 0.0
    if ef > em > es:
        score += 0.5
    elif ef < em < es:
        score -= 0.5
    if price > es:
        score += 0.2
    else:
        score -= 0.2
    score += (rsi - 50) / 100.0  # +/-0.5 at RSI extremes
    score = max(-1.0, min(1.0, score))
    if score > 0.25:
        note = f"uptrend (EMAs aligned up, RSI {rsi:.0f})"
    elif score < -0.25:
        note = f"downtrend (EMAs aligned down, RSI {rsi:.0f})"
    else:
        note = f"choppy/sideways (RSI {rsi:.0f})"
    return note, score


class RecommendationEngine:
    def __init__(self, cfg: dict):
        self.cfg = cfg

    # ------------------------------------------------------------------ #
    #  market regime
    # ------------------------------------------------------------------ #
    def read_market(self, spy_ta: dict | None, qqq_ta: dict | None, vix: float | None) -> MarketRead:
        spy_note, spy_s = _trend_note(spy_ta) if spy_ta else ("no data", 0.0)
        qqq_note, qqq_s = _trend_note(qqq_ta) if qqq_ta else ("no data", 0.0)
        trend = (spy_s + qqq_s) / 2.0

        # VIX pulls sentiment down as it rises; ~12 calm, ~20 normal, >28 fearful
        vix_adj = 0.0
        risk = "Low"
        if vix is not None:
            if vix >= 28:
                vix_adj, risk = -0.35, "High"
            elif vix >= 20:
                vix_adj, risk = -0.15, "Elevated"
            elif vix <= 13:
                vix_adj, risk = +0.10, "Low"
        sentiment = max(0.0, min(100.0, 50 + (trend * 45) + (vix_adj * 100)))
        if sentiment >= 60:
            cls = "Bullish"
        elif sentiment <= 40:
            cls = "Bearish"
        else:
            cls = "Neutral"
        return MarketRead(round(sentiment, 1), cls, vix, spy_note, qqq_note, risk)

    # ------------------------------------------------------------------ #
    #  new-opportunity confidence (0–100)
    # ------------------------------------------------------------------ #
    def score_setup(self, ta: dict, metrics: dict, signal, market: MarketRead | None = None) -> float:
        score = 50.0
        bullish = getattr(signal, "direction", "bullish") == "bullish"

        # --- technicals ---
        ef, em, es = ta.get("ema_fast", 0), ta.get("ema_mid", 0), ta.get("ema_slow", 0)
        if (ef > em > es) == bullish or (ef < em < es) == (not bullish):
            score += 10
        rsi = ta.get("rsi", 50)
        if bullish and 50 < rsi < 70:
            score += 8
        elif (not bullish) and 30 < rsi < 50:
            score += 8
        elif rsi > 78 or rsi < 22:
            score -= 6  # exhaustion
        if (ta.get("macd_hist", 0) > 0) == bullish:
            score += 6
        vmult = ta.get("volume_mult", 1.0)
        if vmult >= 1.5:
            score += min((vmult - 1.5) * 8 + 6, 12)
        vwap = ta.get("vwap")
        if vwap:
            if (ta.get("price", 0) >= vwap) == bullish:
                score += 4

        # --- options metrics ---
        ivr = metrics.get("iv_rank")
        if ivr is not None:
            # buying premium: prefer lower IV rank (cheaper vol)
            score += (50 - ivr) / 10.0  # +/-5
        em_pct = (metrics.get("expected_move") or {}).get("expected_move_pct")
        if em_pct is not None and em_pct > 0:
            score += 3 if em_pct >= 0.02 else 1
        # strategy's own confidence (vol/OI strength etc.)
        score += getattr(signal, "confidence", 0.0) * 10

        # --- market regime alignment ---
        if market is not None:
            if (market.classification == "Bullish") == bullish and market.classification != "Neutral":
                score += 6
            elif market.classification != "Neutral":
                score -= 6
            if market.risk_level == "High":
                score -= 5

        return float(max(0.0, min(100.0, round(score, 1))))

    def risk_level(self, ta: dict, metrics: dict, signal) -> str:
        """Coarse risk rating for an opportunity."""
        ivr = metrics.get("iv_rank") or 0
        dte = None
        legs = getattr(signal, "legs", [])
        if legs:
            from ..indicators.options_metrics import dte as _dte
            dte = _dte(legs[0].expiration)
        score = 0
        if ivr and ivr > 70:
            score += 1            # buying expensive vol
        if dte is not None and dte <= 1:
            score += 1            # 0–1 DTE gamma risk
        if (metrics.get("iv_rank") is None):
            score += 1            # unknown vol context
        return ["Low", "Moderate", "High"][min(score, 2)]

    # ------------------------------------------------------------------ #
    #  open-position assessment
    # ------------------------------------------------------------------ #
    def assess_position(self, pos, ta: dict | None, market: MarketRead | None,
                        take_profit_pct: float, stop_loss_pct: float) -> dict:
        """Return {recommendation, confidence, reason} for an open option position.
        `pos` is a providers.base.PositionRow."""
        c = self.cfg
        gain = pos.gain_pct
        dte = pos.dte
        is_call = pos.option_type == "call"
        rsi = (ta or {}).get("rsi", 50)
        reasons = []
        rec = "HOLD"
        conf = 60.0

        # ---- loss / stop ----
        if gain <= -stop_loss_pct:
            return {"recommendation": "SELL", "confidence": 80.0,
                    "reason": f"down {gain:.0f}%, at/below stop (-{stop_loss_pct:.0f}%)"}

        # ---- profit handling ----
        if gain >= take_profit_pct:
            trend_strong = (is_call and rsi > 55 and (ta or {}).get("macd_hist", 0) > 0) or \
                           ((not is_call) and rsi < 45 and (ta or {}).get("macd_hist", 0) < 0)
            if trend_strong:
                rec, conf = "HOLD", 70.0
                reasons.append(f"+{gain:.0f}% but trend still strong — let it run")
            else:
                rec, conf = "SELL", 78.0
                reasons.append(f"+{gain:.0f}% target hit, momentum cooling")
        elif gain >= c["reduce_gain_pct"]:
            rec, conf = "REDUCE POSITION", 68.0
            reasons.append(f"+{gain:.0f}% — take partial, hold a runner")

        # ---- time decay / expiry ----
        if dte is not None and dte <= c["monitor_dte"]:
            if rec == "HOLD":
                rec = "MONITOR CLOSELY"
            reasons.append(f"{dte} DTE — theta/expiry risk")
            conf = max(conf, 65.0)

        # excessive theta relative to premium
        if pos.current_price > 0 and abs(pos.theta) >= (pos.current_price * c["theta_burn_pct_of_premium"] / 100.0):
            if rec in ("HOLD", "MONITOR CLOSELY"):
                rec = "MONITOR CLOSELY"
            reasons.append("theta burn elevated vs premium")

        # ---- trend reversal against the position ----
        if ta:
            against = (is_call and rsi < 45 and ta.get("macd_hist", 0) < 0) or \
                      ((not is_call) and rsi > 55 and ta.get("macd_hist", 0) > 0)
            if against and gain < c["reduce_gain_pct"]:
                rec, conf = "SELL", 72.0
                reasons.append("momentum turning against the position")

        # market regime conflict
        if market and market.risk_level == "High" and rec == "HOLD":
            rec = "MONITOR CLOSELY"
            reasons.append("high market volatility (VIX)")

        if not reasons:
            reasons.append(f"{'+' if gain>=0 else ''}{gain:.0f}%, thesis intact")
        return {"recommendation": rec, "confidence": round(conf, 1),
                "reason": "; ".join(reasons)}
