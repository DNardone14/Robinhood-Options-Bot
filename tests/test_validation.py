"""Data-quality sanity-guard tests — the rule that blocks stale/garbage quotes
like the AMD 525C @ $5.33 (with AMD near $548)."""

from options_engine import validation as V

CFG = {"reject_below_intrinsic": True, "intrinsic_tolerance": 0.02,
       "require_two_sided_quote": True}


def test_below_intrinsic_rejected():
    bad = {"option_type": "call", "strike": 525, "expiration_date": "2026-06-18",
           "bid": 5.30, "ask": 5.36, "last": 5.33}
    ok, reason = V.check_contract(bad, 548.0, CFG)
    assert not ok and "intrinsic" in reason


def test_fairly_priced_itm_call_passes():
    ok = {"option_type": "call", "strike": 525, "expiration_date": "2026-06-18",
          "bid": 25.8, "ask": 26.2, "last": 26.0}
    assert V.check_contract(ok, 548.0, CFG)[0]


def test_missing_and_crossed_quotes_rejected():
    noq = {"option_type": "put", "strike": 540, "expiration_date": "2026-06-27",
           "bid": 0, "ask": 0, "last": 0}
    crossed = {"option_type": "call", "strike": 560, "expiration_date": "2026-06-27",
               "bid": 4.0, "ask": 3.0}
    assert not V.check_contract(noq, 548.0, CFG)[0]
    assert not V.check_contract(crossed, 548.0, CFG)[0]


def test_filter_chain_splits_clean_and_rejected():
    chain = [
        {"option_type": "call", "strike": 525, "expiration_date": "2026-06-18",
         "bid": 5.30, "ask": 5.36},                                   # below intrinsic
        {"option_type": "call", "strike": 560, "expiration_date": "2026-06-27",
         "bid": 3.1, "ask": 3.3},                                     # ok OTM
    ]
    clean, rejected = V.filter_chain(chain, 548.0, CFG)
    assert len(clean) == 1 and len(rejected) == 1


def test_underlying_stale_flag():
    assert V.underlying_sanity({"last": 548, "prev_close": 525})[0]      # normal
    assert not V.underlying_sanity({"last": 548, "prev_close": 300})[0]  # implausible


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"[PASS] {name}")
    print("all validation tests passed")
