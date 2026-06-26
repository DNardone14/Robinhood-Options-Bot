"""
technicals.py — pure-pandas technical indicators (no TA-Lib needed).

All functions take a DataFrame with columns: open, high, low, close, volume
(daily or intraday). Each returns a pandas Series (or DataFrame for multi-line
indicators) aligned to the input index.

Indicators: EMA, RSI(Wilder), VWAP, Bollinger Bands, ATR(Wilder), MACD,
plus a lightweight volume-profile (POC / value area).
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's RSI."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    # Wilder smoothing == EMA with alpha = 1/period
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100 - (100 / (1 + rs))
    return out.fillna(100.0)  # all-gain window -> RSI 100


def vwap(df: pd.DataFrame) -> pd.Series:
    """Session VWAP from intraday bars. Uses typical price * volume cumsum.
    Pass a single session's bars; resets are the caller's responsibility."""
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    cum_vol = df["volume"].cumsum().replace(0.0, np.nan)
    return (tp * df["volume"]).cumsum() / cum_vol


def bollinger(close: pd.Series, period: int = 20, num_std: float = 2.0) -> pd.DataFrame:
    mid = close.rolling(period).mean()
    sd = close.rolling(period).std(ddof=0)
    return pd.DataFrame(
        {
            "bb_mid": mid,
            "bb_upper": mid + num_std * sd,
            "bb_lower": mid - num_std * sd,
            "bb_width": (2 * num_std * sd) / mid,  # normalized width (squeeze gauge)
        }
    )


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range (Wilder)."""
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    macd_line = ema(close, fast) - ema(close, slow)
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return pd.DataFrame(
        {"macd": macd_line, "macd_signal": signal_line, "macd_hist": macd_line - signal_line}
    )


def volume_profile(df: pd.DataFrame, bins: int = 24, value_area: float = 0.70) -> dict:
    """Coarse volume-by-price profile. Returns the point-of-control (price level
    with most traded volume) and the value-area high/low containing `value_area`
    of total volume. Useful as intraday support/resistance context."""
    if df.empty:
        return {"poc": None, "va_high": None, "va_low": None}
    prices = ((df["high"] + df["low"] + df["close"]) / 3.0).to_numpy()
    vols = df["volume"].to_numpy()
    lo, hi = prices.min(), prices.max()
    if hi <= lo:
        return {"poc": float(lo), "va_high": float(hi), "va_low": float(lo)}
    edges = np.linspace(lo, hi, bins + 1)
    idx = np.clip(np.digitize(prices, edges) - 1, 0, bins - 1)
    hist = np.zeros(bins)
    for i, v in zip(idx, vols):
        hist[i] += v
    centers = (edges[:-1] + edges[1:]) / 2.0
    poc = float(centers[hist.argmax()])

    # grow value area outward from POC until it holds `value_area` of volume
    total = hist.sum()
    order = np.argsort(hist)[::-1]
    cum, chosen = 0.0, []
    for k in order:
        cum += hist[k]
        chosen.append(centers[k])
        if cum >= value_area * total:
            break
    return {"poc": poc, "va_high": float(max(chosen)), "va_low": float(min(chosen))}


def compute_all(daily: pd.DataFrame, cfg: dict, intraday: pd.DataFrame | None = None) -> dict:
    """Convenience: compute the latest value of every indicator for a symbol.
    Returns a flat dict of scalars used by the strategy engine."""
    close = daily["close"]
    out: dict = {}
    out["price"] = float(close.iloc[-1])
    out["ema_fast"] = float(ema(close, cfg["ema_fast"]).iloc[-1])
    out["ema_mid"] = float(ema(close, cfg["ema_mid"]).iloc[-1])
    out["ema_slow"] = float(ema(close, cfg["ema_slow"]).iloc[-1])
    out["rsi"] = float(rsi(close, cfg["rsi_period"]).iloc[-1])
    bb = bollinger(close, cfg["bb_period"], cfg["bb_std"]).iloc[-1]
    out.update({k: (float(v) if pd.notna(v) else None) for k, v in bb.items()})
    out["atr"] = float(atr(daily, cfg["atr_period"]).iloc[-1])
    m = macd(close, cfg["macd_fast"], cfg["macd_slow"], cfg["macd_signal"]).iloc[-1]
    out.update({k: float(v) for k, v in m.items()})

    # volume confirmation: last bar vs 20-bar average
    vol = daily["volume"]
    out["volume"] = float(vol.iloc[-1])
    out["avg_volume_20"] = float(vol.tail(20).mean())
    out["volume_mult"] = (
        out["volume"] / out["avg_volume_20"] if out["avg_volume_20"] else 0.0
    )

    if intraday is not None and not intraday.empty:
        out["vwap"] = float(vwap(intraday).iloc[-1])
        out["volume_profile"] = volume_profile(intraday)
    else:
        out["vwap"] = None
        out["volume_profile"] = {"poc": None, "va_high": None, "va_low": None}
    return out
