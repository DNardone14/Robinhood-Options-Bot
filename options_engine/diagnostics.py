"""
diagnostics.py — verify the live data provider before you trust any signal.

Run it on the droplet (where your Tradier token lives) to confirm the feed is
returning fresh, sane data:

    python3 -m options_engine.diagnostics                 # checks the watchlist
    python3 -m options_engine.diagnostics --symbol AMD     # one symbol, verbose

For each symbol it:
  * pulls the underlying quote and flags stale/implausible prices
  * pulls the near-DTE chain and counts contracts that FAIL the sanity guard
    (priced below intrinsic, missing quotes) — i.e. exactly the AMD 525C bug
  * cross-checks: is the near-the-money option premium >= its intrinsic value?
  * reports how many days of IV-rank history have accumulated per symbol

Exit code is non-zero if any hard data problem is found, so you can wire it into
a pre-flight check.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from .config import (
    DATA_PROVIDER, ENGINE_CONFIG, STRATEGY_CONFIG, VALIDATION_CONFIG, RECO_CONFIG,
)
from .providers import get_provider
from . import validation
from .indicators import options_metrics as om

_IV_FILE = "iv_history.json"


def _iv_days(symbol: str) -> int:
    if not os.path.exists(_IV_FILE):
        return 0
    try:
        data = json.load(open(_IV_FILE))
        v = data.get(symbol, {})
        return len(v) if isinstance(v, dict) else 0
    except Exception:
        return 0


def check_symbol(provider, symbol: str, verbose: bool = False) -> dict:
    out = {"symbol": symbol, "problems": [], "ok": True}

    quote = provider.get_quote(symbol) or {}
    last = quote.get("last")
    out["last"] = last
    ok, why = validation.underlying_sanity(quote)
    if not ok:
        out["problems"].append(f"underlying: {why}")
        out["ok"] = False
    if not last:
        out["problems"].append("no underlying price returned")
        out["ok"] = False
        return out

    max_dte = max(STRATEGY_CONFIG["dm_max_dte"], STRATEGY_CONFIG["uf_max_dte"])
    chains_by_exp, merged, expirations = provider.get_chains(symbol, max_dte)
    out["contracts"] = len(merged)
    out["expirations"] = len(expirations)
    if not merged:
        out["problems"].append("no option chain returned")
        out["ok"] = False
        return out

    clean, rejected = validation.filter_chain(merged, last, VALIDATION_CONFIG)
    out["rejected"] = len(rejected)
    out["clean"] = len(clean)
    below_intrinsic = [r for r in rejected if "below intrinsic" in r.get("reject_reason", "")]
    if below_intrinsic:
        out["ok"] = False
        ex = below_intrinsic[0]
        out["problems"].append(
            f"{len(below_intrinsic)} contract(s) priced below intrinsic — e.g. "
            f"{ex['strike']:g}{ex['option_type'][0].upper()} {ex.get('expiration_date','')}: "
            f"{ex['reject_reason']}"
        )

    # near-the-money premium vs intrinsic spot check
    nearest = min(merged, key=lambda o: abs(float(o["strike"]) - last))
    intr = validation.intrinsic_value(nearest, last)
    mid = ((nearest.get("bid") or 0) + (nearest.get("ask") or 0)) / 2.0 or (nearest.get("last") or 0)
    out["atm_check"] = (f"{nearest['strike']:g}{nearest['option_type'][0].upper()} "
                        f"mid ${mid:.2f} vs intrinsic ${intr:.2f}")
    out["iv_history_days"] = _iv_days(symbol)
    out["iv_mature"] = out["iv_history_days"] >= RECO_CONFIG["min_iv_history_days"]

    if verbose:
        out["sample_rejections"] = [
            f"{r['strike']:g}{r['option_type'][0].upper()} {r.get('expiration_date','')}: {r['reject_reason']}"
            for r in rejected[:5]
        ]
    return out


def main():
    ap = argparse.ArgumentParser(description="Verify the market-data provider")
    ap.add_argument("--symbol", help="check a single symbol (verbose)")
    args = ap.parse_args()

    provider = get_provider(DATA_PROVIDER, None)
    symbols = [args.symbol] if args.symbol else ENGINE_CONFIG["watchlist"]
    print(f"Data provider: {provider.name}\n" + "=" * 60)

    any_problem = False
    for sym in symbols:
        try:
            r = check_symbol(provider, sym, verbose=bool(args.symbol))
        except Exception as exc:
            print(f"  {sym:6} ERROR: {exc}")
            any_problem = True
            continue
        status = "OK " if r["ok"] else "BAD"
        if not r["ok"]:
            any_problem = True
        last = f"${r['last']:.2f}" if r.get("last") else "n/a"
        print(f"[{status}] {sym:6} last {last:>10}  "
              f"contracts {r.get('clean','?')} clean / {r.get('rejected','?')} rejected  "
              f"IVdays {r.get('iv_history_days','?')}"
              f"{'' if r.get('iv_mature') else ' (warming up)'}")
        if r.get("atm_check"):
            print(f"        ATM check: {r['atm_check']}")
        for p in r["problems"]:
            print(f"        ⚠ {p}")
        for s in r.get("sample_rejections", []):
            print(f"        rejected: {s}")

    print("=" * 60)
    print("RESULT:", "problems found — do NOT trust signals until fixed" if any_problem
          else "data looks healthy")
    sys.exit(1 if any_problem else 0)


if __name__ == "__main__":
    main()
