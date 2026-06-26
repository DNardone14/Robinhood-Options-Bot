"""Black-Scholes pricing / Greeks / IV-solver tests.

Run from the repo root:  python -m pytest tests/   (or)  python tests/test_options_math.py
"""

import math
from options_engine.indicators import options_metrics as om


def test_atm_call_price_matches_textbook():
    # S=100, K=100, T=1, r=5%, sigma=20%  ->  call ≈ 10.4506
    price = om.bs_price(100, 100, 1.0, 0.05, 0.20, "call")
    assert abs(price - 10.4506) < 0.01


def test_put_call_parity():
    c = om.bs_price(100, 100, 1.0, 0.05, 0.20, "call")
    p = om.bs_price(100, 100, 1.0, 0.05, 0.20, "put")
    # C - P == S - K e^{-rT}
    assert abs((c - p) - (100 - 100 * math.exp(-0.05))) < 1e-6


def test_atm_call_delta():
    g = om.bs_greeks(100, 100, 1.0, 0.05, 0.20, "call")
    assert abs(g["delta"] - 0.6368) < 0.005
    assert g["gamma"] > 0
    assert g["theta"] < 0      # long option decays
    assert g["vega"] > 0


def test_iv_solver_round_trip():
    price = om.bs_price(100, 100, 1.0, 0.05, 0.20, "call")
    iv = om.implied_vol(price, 100, 100, 1.0, 0.05, "call")
    assert abs(iv - 0.20) < 1e-3


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"[PASS] {name}")
    print("all option-math tests passed")
