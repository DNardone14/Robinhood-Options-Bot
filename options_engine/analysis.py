"""
analysis.py — provider-backed per-symbol analysis shared by briefing + alerts.

Analyzer.analyze(symbol) returns:
  {"ta": {...}, "metrics": {...}, "signals": [...], "rejected": [...]}

Hardening added after the bad-AMD-signal incident:
  1. SANITY FILTER — every chain is run through validation.filter_chain() before
     strategies touch it, so contracts priced below intrinsic value or with broken
     quotes are dropped (and recorded in `rejected`).
  2. DAILY IV HISTORY — IV is recorded at most ONCE per calendar day per symbol
     (not every 60s scan), so IV-rank is a real ~year-long percentile. Until
     `min_iv_history_days` distinct days accumulate, IV-rank/percentile return None
     and confidence does not lean on them.
"""

from __future__ import annotations

import json
import os
from datetime import datetime

from .indicators import technicals as ta_mod
from .indicators import options_metrics as om
from .strategies import DirectionalMomentum, UnusualFlow, build_vertical
from .recommendation import RecommendationEngine
from . import validation
from .config import (
    STRATEGY_CONFIG, RECO_CONFIG, ENGINE_CONFIG, PROVIDER_CONFIG, VALIDATION_CONFIG,
)

_IV_FILE = "iv_history.json"


class Analyzer:
    def __init__(self, provider, reco: RecommendationEngine | None = None):
        self.provider = provider
        self.dm = DirectionalMomentum(STRATEGY_CONFIG)
        self.uf = UnusualFlow(STRATEGY_CONFIG)
        self.reco = reco or RecommendationEngine(RECO_CONFIG)
        # symbol -> {date_iso: iv}  (one IV per calendar day)
        self.iv_history: dict[str, dict[str, float]] = {}
        self._load_iv()
        self._market_cache = None

    # ----- market regime (cached per cycle) -------------------------- #
    def read_market(self):
        idx = PROVIDER_CONFIG["market_index_symbols"]
        spy_ta = self._ta_only(idx["spy"])
        qqq_ta = self._ta_only(idx["qqq"])
        vixq = self.provider.get_quote(idx["vix"])
        vix = vixq.get("last") if vixq else None
        self._market_cache = self.reco.read_market(spy_ta, qqq_ta, vix)
        return self._market_cache

    def _ta_only(self, symbol: str) -> dict | None:
        daily = self.provider.get_daily_bars(symbol, ENGINE_CONFIG["history_days"])
        if daily is None or len(daily) < 50:
            return None
        intraday = self.provider.get_intraday_bars(symbol, ENGINE_CONFIG["intraday_interval"])
        return ta_mod.compute_all(daily, STRATEGY_CONFIG, intraday)

    # ----- IV history (one reading per calendar day) ----------------- #
    def _record_iv(self, symbol: str, iv: float) -> list[float]:
        """Store today's IV (overwriting any earlier reading for today) and return
        the chronological list of daily IV values."""
        today = datetime.now().strftime("%Y-%m-%d")
        hist = self.iv_history.setdefault(symbol, {})
        hist[today] = iv
        # cap to ~history_days most recent days
        if len(hist) > ENGINE_CONFIG["history_days"]:
            for d in sorted(hist)[:-ENGINE_CONFIG["history_days"]]:
                del hist[d]
        return [hist[d] for d in sorted(hist)]

    def _iv_days(self, symbol: str) -> int:
        return len(self.iv_history.get(symbol, {}))

    # ----- full per-symbol analysis ---------------------------------- #
    def analyze(self, symbol: str, market=None) -> dict | None:
        daily = self.provider.get_daily_bars(symbol, ENGINE_CONFIG["history_days"])
        if daily is None or len(daily) < 50:
            return None
        intraday = self.provider.get_intraday_bars(symbol, ENGINE_CONFIG["intraday_interval"])
        ta = ta_mod.compute_all(daily, STRATEGY_CONFIG, intraday)

        # use the freshest underlying price we can for intrinsic checks/strikes
        q = self.provider.get_quote(symbol) or {}
        price = float(q.get("last") or ta["price"])
        ta["price"] = price

        chains_by_exp, merged, expirations = self.provider.get_chains(
            symbol, max(STRATEGY_CONFIG["dm_max_dte"], STRATEGY_CONFIG["uf_max_dte"])
        )

        # ---- SANITY FILTER: drop below-intrinsic / broken-quote contracts ----
        rejected = []
        if merged:
            merged, rej = validation.filter_chain(merged, price, VALIDATION_CONFIG)
            rejected = rej
            for exp in list(chains_by_exp):
                clean, _ = validation.filter_chain(chains_by_exp[exp], price, VALIDATION_CONFIG)
                if clean:
                    chains_by_exp[exp] = clean
                else:
                    del chains_by_exp[exp]
            expirations = [e for e in expirations if e in chains_by_exp]

        metrics = {"price": price, "rsi": ta["rsi"], "vwap": ta.get("vwap"),
                   "iv_history_days": self._iv_days(symbol), "rejected_contracts": len(rejected)}
        signals = []
        if merged:
            atm = om.atm_iv(merged, price)
            iv_values, iv_days = [], self._iv_days(symbol)
            if atm:
                iv_values = self._record_iv(symbol, atm)
                iv_days = self._iv_days(symbol)
            metrics["iv_history_days"] = iv_days

            # IV-rank only once enough DISTINCT DAYS exist; else unknown
            mature = iv_days >= RECO_CONFIG["min_iv_history_days"]
            metrics.update({
                "atm_iv": atm,
                "iv_rank": om.iv_rank(atm, iv_values) if mature else None,
                "iv_pct": om.iv_percentile(atm, iv_values) if mature else None,
                "iv_mature": mature,
                "skew": om.iv_skew(merged, price),
                "expected_move": om.expected_move(merged, price),
                **om.put_call_ratio(merged),
            })

            raw = []
            if STRATEGY_CONFIG["dm_enabled"]:
                s = self.dm.evaluate(symbol, ta, chains_by_exp, expirations,
                                     metrics.get("expected_move"), earnings_flag=False)
                if s:
                    raw.append(s)
            if STRATEGY_CONFIG["uf_enabled"]:
                raw.extend(self.uf.evaluate(symbol, ta, merged, atm, earnings_flag=False))
            if STRATEGY_CONFIG["spread_enabled"]:
                raw = [build_vertical(s, merged, STRATEGY_CONFIG) or s
                       if s.signal_type.value in ("call", "put") else s for s in raw]

            mk = market or self._market_cache
            for s in raw:
                s.confidence = self.reco.score_setup(ta, metrics, s, mk) / 100.0
                s.notes += f" | risk:{self.reco.risk_level(ta, metrics, s)}"
            signals = raw

        return {"ta": ta, "metrics": metrics, "signals": signals, "rejected": rejected}

    # ----- IV history persistence ------------------------------------ #
    def _load_iv(self):
        if os.path.exists(_IV_FILE):
            try:
                data = json.load(open(_IV_FILE))
                # migrate old format (list of values) -> drop; only keep dict form
                for k, v in data.items():
                    if isinstance(v, dict):
                        self.iv_history[k] = {str(d): float(iv) for d, iv in v.items()}
            except Exception:
                pass

    def save_iv(self):
        try:
            json.dump(self.iv_history, open(_IV_FILE, "w"))
        except Exception:
            pass
