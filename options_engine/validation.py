"""
validation.py — data-quality guards for option chains.

The AMD incident (a 525C on a $548 stock quoted at $5.33) was a contract priced
BELOW its intrinsic value — a textbook stale/garbage quote. A real option can
never trade below intrinsic, so that single rule catches the worst data errors.

These guards run on the chain BEFORE any strategy sees it, so a bad quote can
never become a signal, a confidence score, or an alert.

Checks per contract:
  * two-sided quote present (bid > 0 and ask > 0)         [require_two_sided_quote]
  * ask >= bid (not crossed/inverted)
  * mid price >= intrinsic value (within a small tolerance) [reject_below_intrinsic]
  * strike > 0 and a usable price exists

filter_chain() returns (clean_contracts, rejections) where rejections explain why
each dropped contract failed — surfaced by the diagnostics tool.
"""

from __future__ import annotations


def intrinsic_value(option: dict, underlying_price: float) -> float:
    K = float(option.get("strike", 0) or 0)
    if option.get("option_type") == "call":
        return max(0.0, underlying_price - K)
    return max(0.0, K - underlying_price)


def _mid(option: dict) -> float:
    bid = float(option.get("bid") or 0)
    ask = float(option.get("ask") or 0)
    if bid > 0 and ask > 0:
        return (bid + ask) / 2.0
    return float(option.get("last") or 0)


def check_contract(option: dict, underlying_price: float, cfg: dict) -> tuple[bool, str]:
    """Return (ok, reason). reason is '' when ok."""
    bid = float(option.get("bid") or 0)
    ask = float(option.get("ask") or 0)
    strike = float(option.get("strike") or 0)
    if strike <= 0:
        return False, "non-positive strike"

    if cfg.get("require_two_sided_quote", True):
        if bid <= 0 or ask <= 0:
            return False, "missing two-sided quote (bid/ask)"
    if bid > 0 and ask > 0 and ask < bid:
        return False, f"crossed quote (ask {ask} < bid {bid})"

    mid = _mid(option)
    if mid <= 0:
        return False, "no usable price"

    if cfg.get("reject_below_intrinsic", True) and underlying_price > 0:
        intrinsic = intrinsic_value(option, underlying_price)
        tol = cfg.get("intrinsic_tolerance", 0.02)
        # allow a small tolerance for rounding / quote noise
        if mid < intrinsic * (1.0 - tol) - 0.01:
            return False, (f"priced below intrinsic: mid ${mid:.2f} < "
                           f"intrinsic ${intrinsic:.2f} (likely stale quote)")
    return True, ""


def filter_chain(chain: list[dict], underlying_price: float, cfg: dict) -> tuple[list[dict], list[dict]]:
    """Split a chain into (clean, rejected). Each rejected row gets a 'reject_reason'."""
    clean, rejected = [], []
    for o in chain:
        ok, reason = check_contract(o, underlying_price, cfg)
        if ok:
            clean.append(o)
        else:
            r = dict(o)
            r["reject_reason"] = reason
            rejected.append(r)
    return clean, rejected


def underlying_sanity(quote: dict) -> tuple[bool, str]:
    """Coarse freshness/validity check on an underlying quote dict
    ({'last','prev_close','change_pct'})."""
    last = quote.get("last")
    prev = quote.get("prev_close")
    if not last or last <= 0:
        return False, "no/zero last price"
    if prev and prev > 0:
        move = abs(last - prev) / prev
        if move > 0.40:
            return False, f"implausible {move*100:.0f}% move vs prev close (stale price?)"
    return True, ""
