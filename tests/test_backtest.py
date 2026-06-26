"""Backtester math tests — strike-by-delta selection and metrics."""

from datetime import datetime, timedelta

from options_engine import backtest_options as BT
from options_engine.indicators import options_metrics as om


def test_strike_for_delta_recovers_target():
    # 30-DTE, $400 underlying — discrete-strike rounding stays close to target delta
    S, sigma, T, r = 400.0, 0.40, 30 / 365, 0.043
    for target in (0.30, 0.40, 0.50):
        K = BT.strike_for_delta(S, sigma, T, r, target, "call")
        d = om.bs_greeks(S, K, T, r, sigma, "call")["delta"]
        assert abs(d - target) < 0.05


def test_metrics_basic():
    base = datetime(2025, 1, 1)
    trades = [
        {"pnl": 100, "exit_date": base, "days_held": 3},
        {"pnl": -50, "exit_date": base + timedelta(1), "days_held": 2},
        {"pnl": 200, "exit_date": base + timedelta(2), "days_held": 5},
        {"pnl": -80, "exit_date": base + timedelta(3), "days_held": 1},
    ]
    m = BT.metrics_for(trades)
    assert m["trades"] == 4
    assert abs(m["win_rate"] - 50.0) < 1e-6
    assert abs(m["net_pnl"] - 170) < 1e-6
    assert abs(m["profit_factor"] - 300 / 130) < 1e-3
    assert m["max_drawdown"] <= 0


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"[PASS] {name}")
    print("all backtest tests passed")
