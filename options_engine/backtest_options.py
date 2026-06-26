"""
backtest_options.py — historical backtest of the Directional Momentum options
strategy over 60d / 1y / 2y / 5y windows.

IMPORTANT MODELING NOTE
-----------------------
Free historical OPTION-CHAIN data (per-strike bid/ask/IV/Greeks for past dates)
does not exist. yfinance/Tradier only expose *current* chains. So this backtest:
  * uses REAL historical UNDERLYING prices (yfinance, up to ~5y daily), and
  * MODELS each option with Black-Scholes, using trailing realized volatility as
    the implied-vol input (scaled by `iv_premium_mult`).

That means results reflect the quality of the SIGNAL/ENTRY logic and realistic
option convexity + theta decay — but not real bid/ask spreads, IV skew, or vol
crush around events. Treat the numbers as a strategy sanity check, not a promise
of live P&L. Unusual-Options-Flow can't be backtested here (needs historical
options volume/OI), so this covers Directional Momentum only.

Usage (run where you have internet — your PC or the droplet):
    python3 -m options_engine.backtest_options --symbol AMD
    python3 -m options_engine.backtest_options --multi AMD NVDA SPY QQQ PLTR
    python3 -m options_engine.backtest_options --symbol NVDA --dte 7 --verbose
"""

from __future__ import annotations

import argparse
import math
from datetime import datetime
from statistics import NormalDist

from .indicators import technicals as ta_mod
from .indicators import options_metrics as om
from .config import STRATEGY_CONFIG, RISK_CONFIG

_ND = NormalDist()
_PERIODS = [60, 365, 730, 1825]  # 60d, 1y, 2y, 5y (trailing, by entry date)


# --------------------------------------------------------------------------- #
#  data
# --------------------------------------------------------------------------- #
def load_bars(symbol: str, years: int = 6):
    import yfinance as yf
    df = yf.download(symbol, period=f"{years}y", interval="1d",
                     auto_adjust=False, progress=False)
    if df is None or df.empty:
        return None
    if hasattr(df.columns, "nlevels") and df.columns.nlevels > 1:
        df.columns = [c[0].lower() for c in df.columns]
    else:
        df.columns = [str(c).lower() for c in df.columns]
    return df[["open", "high", "low", "close", "volume"]].dropna()


def realized_vol(close, window: int = 20):
    import numpy as np
    logret = (close / close.shift(1)).apply(lambda x: math.log(x) if x and x > 0 else 0.0)
    return logret.rolling(window).std() * math.sqrt(252)


# --------------------------------------------------------------------------- #
#  option modeling
# --------------------------------------------------------------------------- #
def _round_strike(k: float, spot: float) -> float:
    inc = 1.0 if spot < 100 else (2.5 if spot < 250 else 5.0)
    return round(round(k / inc) * inc, 2)


def strike_for_delta(S, sigma, T, r, target_delta, option_type) -> float:
    if sigma <= 0 or T <= 0:
        return _round_strike(S, S)
    d1 = _ND.inv_cdf(target_delta if option_type == "call" else 1 - target_delta)
    k = S * math.exp((r + 0.5 * sigma ** 2) * T - d1 * sigma * math.sqrt(T))
    return _round_strike(k, S)


# --------------------------------------------------------------------------- #
#  backtest core
# --------------------------------------------------------------------------- #
def _direction(row, cfg) -> str | None:
    if row["volume_mult"] < cfg["dm_volume_mult"]:
        return None
    bull = (row["ema_fast"] > row["ema_mid"] > row["ema_slow"]
            and row["close"] > row["ema_mid"]
            and row["rsi"] > cfg["dm_rsi_long_min"] and row["macd_hist"] > 0)
    bear = (row["ema_fast"] < row["ema_mid"] < row["ema_slow"]
            and row["close"] < row["ema_mid"]
            and row["rsi"] < cfg["dm_rsi_short_max"] and row["macd_hist"] < 0)
    return "bullish" if bull else ("bearish" if bear else None)


def run_backtest(symbol: str, dte: int = 7, iv_premium_mult: float = 1.0,
                 r: float = 0.043, years: int = 6) -> dict:
    import pandas as pd
    bars = load_bars(symbol, years)
    if bars is None or len(bars) < 80:
        return {"symbol": symbol, "error": "no/insufficient data", "trades": []}

    close = bars["close"]
    ema_f = ta_mod.ema(close, STRATEGY_CONFIG["ema_fast"])
    ema_m = ta_mod.ema(close, STRATEGY_CONFIG["ema_mid"])
    ema_s = ta_mod.ema(close, STRATEGY_CONFIG["ema_slow"])
    rsi = ta_mod.rsi(close, STRATEGY_CONFIG["rsi_period"])
    macd = ta_mod.macd(close, STRATEGY_CONFIG["macd_fast"], STRATEGY_CONFIG["macd_slow"],
                       STRATEGY_CONFIG["macd_signal"])
    vol = bars["volume"]
    vmult = vol / vol.rolling(20).mean()
    rv = realized_vol(close).clip(lower=0.05)

    feat = pd.DataFrame({
        "close": close, "ema_fast": ema_f, "ema_mid": ema_m, "ema_slow": ema_s,
        "rsi": rsi, "macd_hist": macd["macd_hist"], "volume_mult": vmult, "rv": rv,
    }).dropna()

    tp = 1 + RISK_CONFIG["take_profit_pct"] / 100.0
    sl = 1 - RISK_CONFIG["stop_loss_pct"] / 100.0
    target_delta = STRATEGY_CONFIG["dm_target_delta"]

    dates = list(feat.index)
    trades = []
    open_pos = None

    for i, dt in enumerate(dates):
        row = feat.loc[dt]
        S = float(row["close"])

        # manage an open position first
        if open_pos:
            days_held = (dt - open_pos["entry_date"]).days
            T = max(open_pos["T0"] - days_held / 365.0, 1e-6)
            sigma = float(row["rv"]) * iv_premium_mult
            prem = om.bs_price(S, open_pos["strike"], T, r, sigma, open_pos["type"])
            exit_reason = None
            if T <= 1e-5 or days_held >= dte:
                prem = max(0.0, (S - open_pos["strike"]) if open_pos["type"] == "call"
                           else (open_pos["strike"] - S))  # settle at intrinsic
                exit_reason = "expiry"
            elif prem >= open_pos["entry"] * tp:
                exit_reason = "take_profit"
            elif prem <= open_pos["entry"] * sl:
                exit_reason = "stop_loss"
            if exit_reason:
                exitp = round(prem, 2)
                pnl = round((exitp - open_pos["entry"]) * 100, 2)
                trades.append({**open_pos, "exit_date": dt, "exit": exitp,
                               "pnl": pnl,
                               "pnl_pct": round(100 * (exitp - open_pos["entry"]) / open_pos["entry"], 1),
                               "days_held": days_held, "reason": exit_reason})
                open_pos = None

        # consider a new entry (one position at a time)
        if open_pos is None:
            direction = _direction(row, STRATEGY_CONFIG)
            if direction:
                otype = "call" if direction == "bullish" else "put"
                sigma = float(row["rv"]) * iv_premium_mult
                T0 = dte / 365.0
                K = strike_for_delta(S, sigma, T0, r, target_delta, otype)
                entry = om.bs_price(S, K, T0, r, sigma, otype)
                if entry > 0.05:  # ignore degenerate near-zero premiums
                    open_pos = {"symbol": symbol, "type": otype, "direction": direction,
                                "strike": K, "entry_date": dt, "entry": round(entry, 2),
                                "T0": T0, "entry_spot": round(S, 2)}

    # close any still-open position at last bar (mark to model)
    if open_pos:
        dt = dates[-1]
        S = float(feat.loc[dt, "close"])
        days_held = (dt - open_pos["entry_date"]).days
        T = max(open_pos["T0"] - days_held / 365.0, 1e-6)
        sigma = float(feat.loc[dt, "rv"]) * iv_premium_mult
        prem = om.bs_price(S, open_pos["strike"], T, r, sigma, open_pos["type"])
        exitp = round(prem, 2)
        pnl = round((exitp - open_pos["entry"]) * 100, 2)
        trades.append({**open_pos, "exit_date": dt, "exit": exitp,
                       "pnl": pnl,
                       "pnl_pct": round(100 * (exitp - open_pos["entry"]) / open_pos["entry"], 1),
                       "days_held": days_held, "reason": "open(marked)"})

    return {"symbol": symbol, "trades": trades, "last_date": dates[-1], "bars": len(feat)}


# --------------------------------------------------------------------------- #
#  metrics + reporting
# --------------------------------------------------------------------------- #
def metrics_for(trades: list[dict]) -> dict:
    if not trades:
        return {"trades": 0}
    pnls = [t["pnl"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    # max drawdown on cumulative equity (by exit date)
    eq, peak, mdd = 0.0, 0.0, 0.0
    for t in sorted(trades, key=lambda x: x["exit_date"]):
        eq += t["pnl"]
        peak = max(peak, eq)
        mdd = min(mdd, eq - peak)
    return {
        "trades": len(trades),
        "win_rate": 100 * len(wins) / len(trades),
        "net_pnl": sum(pnls),
        "avg_win": (sum(wins) / len(wins)) if wins else 0.0,
        "avg_loss": (sum(losses) / len(losses)) if losses else 0.0,
        "profit_factor": (sum(wins) / abs(sum(losses))) if losses else float("inf"),
        "max_drawdown": mdd,
        "avg_days": sum(t["days_held"] for t in trades) / len(trades),
    }


def report(result: dict, periods: list[int], verbose: bool = False) -> str:
    sym = result["symbol"]
    if result.get("error"):
        return f"{sym}: {result['error']}"
    trades = result["trades"]
    last = result["last_date"]
    lines = [f"\n══ {sym}  ({result['bars']} bars, through {last.date()}) ══"]
    lines.append(f"{'Window':>8} | {'Trades':>6} | {'Win%':>5} | {'Net P&L':>10} | "
                 f"{'PF':>5} | {'AvgWin':>8} | {'AvgLoss':>8} | {'MaxDD':>9} | {'Days':>4}")
    lines.append("-" * 92)
    for days in periods:
        cutoff = last - _timedelta(days)
        sub = [t for t in trades if t["entry_date"] >= cutoff]
        m = metrics_for(sub)
        label = _period_label(days)
        if m["trades"] == 0:
            lines.append(f"{label:>8} | {'0':>6} |   —   |        —   |   —   |       — |       — |        — |   —")
            continue
        pf = "inf" if m["profit_factor"] == float("inf") else f"{m['profit_factor']:.2f}"
        lines.append(
            f"{label:>8} | {m['trades']:>6} | {m['win_rate']:>4.0f}% | "
            f"{m['net_pnl']:>+9.0f}$ | {pf:>5} | {m['avg_win']:>+7.0f}$ | "
            f"{m['avg_loss']:>+7.0f}$ | {m['max_drawdown']:>+8.0f}$ | {m['avg_days']:>3.0f}"
        )
    if verbose:
        lines.append("\n  Last 10 trades:")
        for t in trades[-10:]:
            lines.append(f"   {t['entry_date'].date()} {t['type']:>4} {t['strike']:g} "
                         f"entry ${t['entry']:.2f} -> ${t['exit']:.2f} "
                         f"({t['pnl_pct']:+.0f}%, ${t['pnl']:+.0f}) {t['reason']}")
    return "\n".join(lines)


def _timedelta(days):
    from datetime import timedelta
    return timedelta(days=days)


def _period_label(days: int) -> str:
    return {60: "60d", 365: "1y", 730: "2y", 1825: "5y"}.get(days, f"{days}d")


def main():
    ap = argparse.ArgumentParser(description="Backtest the Directional Momentum options strategy")
    ap.add_argument("--symbol", help="single symbol")
    ap.add_argument("--multi", nargs="+", help="multiple symbols")
    ap.add_argument("--dte", type=int, default=7, help="modeled days-to-expiry per trade")
    ap.add_argument("--iv-mult", type=float, default=1.0, help="IV = realized_vol * this")
    ap.add_argument("--periods", type=int, nargs="+", default=_PERIODS,
                    help="trailing windows in days (default 60 365 730 1825)")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    symbols = args.multi or ([args.symbol] if args.symbol else STRATEGY_CONFIG.get("watchlist", ["AMD"]))
    print("Backtest — Directional Momentum (Black-Scholes-modeled options on real underlying prices)")
    print("NOTE: modeled premiums (no historical chains). Signal/strategy sanity check, not live P&L.")
    agg = []
    for sym in symbols:
        res = run_backtest(sym, dte=args.dte, iv_premium_mult=args.iv_mult)
        print(report(res, args.periods, args.verbose))
        if not res.get("error"):
            agg.extend(res["trades"])

    if len(symbols) > 1 and agg:
        print("\n══ PORTFOLIO (all symbols combined) ══")
        last = max(t["exit_date"] for t in agg)
        for days in args.periods:
            cutoff = last - _timedelta(days)
            sub = [t for t in agg if t["entry_date"] >= cutoff]
            m = metrics_for(sub)
            if m["trades"] == 0:
                continue
            pf = "inf" if m["profit_factor"] == float("inf") else f"{m['profit_factor']:.2f}"
            print(f"  {_period_label(days):>4}: {m['trades']} trades, win {m['win_rate']:.0f}%, "
                  f"net ${m['net_pnl']:+.0f}, PF {pf}, maxDD ${m['max_drawdown']:+.0f}")


if __name__ == "__main__":
    main()
