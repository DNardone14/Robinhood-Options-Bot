"""
app.py — lightweight mobile dashboard (PDR Feature 4) on FastAPI.

Sections: Portfolio overview, Open positions (+ AI recommendation), Watchlist
(confidence), Market status, Recent alerts. Mobile-first single page that
auto-refreshes. Data comes from the bot's live state:
  * account  -> PortfolioProvider sync file
  * market + watchlist -> Analyzer (provider-backed; cached briefly)
  * alerts   -> Storage.recent_alerts()

Run standalone:
    python -m options_engine.webapp.app
or via the assistant (`--dashboard`). JSON is at /api/state for any other client.
"""

from __future__ import annotations

import time

from ..config import (
    DATA_PROVIDER, PROVIDER_CONFIG, RECO_CONFIG, RISK_CONFIG,
    ENGINE_CONFIG, WEBAPP_CONFIG, STORAGE_CONFIG,
)
from ..providers import get_provider, PortfolioProvider
from ..recommendation import RecommendationEngine, confidence_band
from ..analysis import Analyzer
from ..storage import Storage

try:
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse, JSONResponse
except ImportError:  # pragma: no cover
    FastAPI = None


# --------------------------------------------------------------------------- #
#  state assembly (cached so a page refresh doesn't hammer the data provider)
# --------------------------------------------------------------------------- #
class _StateCache:
    def __init__(self, ttl: int = 25):
        self.ttl = ttl
        self._at = 0.0
        self._data: dict | None = None
        self.provider = get_provider(DATA_PROVIDER, None)
        self.analyzer = Analyzer(self.provider, RecommendationEngine(RECO_CONFIG))
        self.pp = PortfolioProvider(PROVIDER_CONFIG["portfolio_sync_file"])
        self.storage = Storage(STORAGE_CONFIG)

    def get(self) -> dict:
        if self._data and (time.time() - self._at) < self.ttl:
            return self._data
        self._data = self._build()
        self._at = time.time()
        return self._data

    def _build(self) -> dict:
        market = self.analyzer.read_market()
        acct = self.pp.get_snapshot()
        positions = []
        for p in acct.positions:
            if p.kind != "option":
                continue
            try:
                b = self.analyzer.analyze(p.symbol, market)
                ta = b["ta"] if b else None
            except Exception:
                ta = None
            rec = self.analyzer.reco.assess_position(
                p, ta, market, RISK_CONFIG["take_profit_pct"], RISK_CONFIG["stop_loss_pct"])
            positions.append({
                "symbol": p.symbol, "contract": f"{p.strike:g}{p.option_type[0].upper()} {p.expiration}",
                "qty": p.quantity, "entry": p.entry_price, "current": p.current_price,
                "gain_pct": round(p.gain_pct, 1), "dte": p.dte,
                "delta": p.delta, "theta": p.theta,
                "recommendation": rec["recommendation"], "confidence": rec["confidence"],
                "reason": rec["reason"],
            })
        watch = []
        for sym in ENGINE_CONFIG["watchlist"]:
            try:
                b = self.analyzer.analyze(sym, market)
            except Exception:
                b = None
            if not b or not b["signals"]:
                continue
            best = max(b["signals"], key=lambda s: s.confidence)
            conf = best.confidence * 100
            if conf < RECO_CONFIG["moderate"]:
                continue
            lg = best.legs[0]
            watch.append({
                "symbol": sym, "confidence": round(conf, 0),
                "band": confidence_band(conf), "direction": best.direction,
                "contract": f"{lg.expiration} {lg.strike:g}{lg.option_type[0].upper()}",
                "premium": round(lg.entry_premium, 2),
            })
        watch.sort(key=lambda w: w["confidence"], reverse=True)
        self.analyzer.save_iv()
        return {
            "market": {
                "classification": market.classification, "sentiment": market.sentiment,
                "vix": market.vix, "risk": market.risk_level,
                "spy": market.spy_note, "qqq": market.qqq_note,
            },
            "account": {
                "total_value": acct.total_value, "cash": acct.cash,
                "buying_power": acct.buying_power, "day_pl": acct.day_pl,
                "week_pl": acct.week_pl, "month_pl": acct.month_pl,
                "open_positions": acct.open_positions, "open_options": acct.open_options,
                "updated_at": acct.updated_at,
            },
            "positions": positions,
            "watchlist": watch,
            "alerts": self.storage.recent_alerts(15),
        }


# --------------------------------------------------------------------------- #
#  HTML
# --------------------------------------------------------------------------- #
def _render_html(state: dict) -> str:
    m, a = state["market"], state["account"]
    refresh = WEBAPP_CONFIG.get("refresh_seconds", 30)

    def pl(v):
        cls = "pos" if v > 0 else ("neg" if v < 0 else "")
        sign = "+" if v > 0 else ""
        return f'<span class="{cls}">{sign}${v:,.2f}</span>'

    pos_rows = ""
    for p in state["positions"]:
        rc = {"SELL": "rec-sell", "REDUCE POSITION": "rec-reduce",
              "MONITOR CLOSELY": "rec-monitor", "HOLD": "rec-hold"}.get(p["recommendation"], "")
        g = p["gain_pct"]
        gcls = "pos" if g >= 0 else "neg"
        pos_rows += f"""
        <div class="card">
          <div class="row"><b>{p['symbol']}</b> <span class="muted">{p['contract']} ×{p['qty']}</span>
            <span class="{rc} pill">{p['recommendation']}</span></div>
          <div class="row"><span>${p['entry']:.2f} → ${p['current']:.2f}
            <span class="{gcls}">({'+' if g>=0 else ''}{g:.0f}%)</span></span>
            <span class="muted">{p['dte']} DTE · Δ{p['delta']:+.2f} Θ{p['theta']:+.2f}</span></div>
          <div class="muted small">conf {p['confidence']:.0f} — {p['reason']}</div>
        </div>"""
    if not state["positions"]:
        pos_rows = '<div class="muted">No open option positions.</div>'

    watch_rows = ""
    for w in state["watchlist"]:
        watch_rows += f"""
        <div class="card">
          <div class="row"><b>{w['symbol']}</b>
            <span class="pill conf">{w['confidence']:.0f} {w['band']}</span></div>
          <div class="row"><span class="muted">{w['contract']} · {w['direction']}</span>
            <span>~${w['premium']:.2f}</span></div>
        </div>"""
    if not state["watchlist"]:
        watch_rows = '<div class="muted">No opportunities above moderate confidence.</div>'

    alert_rows = ""
    for al in state["alerts"]:
        kc = {"buy": "rec-hold", "sell": "rec-sell", "risk": "rec-monitor",
              "hold": "rec-hold"}.get(al["kind"], "")
        alert_rows += f"""<div class="card small"><span class="pill {kc}">{al['kind'].upper()}</span>
          <b>{al['subject']}</b> <span class="muted">{al['ts'][5:16]}</span></div>"""
    if not state["alerts"]:
        alert_rows = '<div class="muted">No alerts yet.</div>'

    mcls = "pos" if m["classification"] == "Bullish" else ("neg" if m["classification"] == "Bearish" else "")
    vix = f"{m['vix']:.1f}" if m["vix"] is not None else "n/a"

    return f"""<!doctype html><html><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="{refresh}">
<title>{WEBAPP_CONFIG['title']}</title>
<style>
  :root {{ color-scheme: dark; }}
  body {{ font-family: -apple-system, system-ui, sans-serif; margin:0; background:#0e1117; color:#e6e6e6; }}
  header {{ padding:14px 16px; background:#161b22; position:sticky; top:0; border-bottom:1px solid #222; }}
  header h1 {{ font-size:17px; margin:0; }}
  .wrap {{ padding:12px 14px 40px; max-width:680px; margin:0 auto; }}
  h2 {{ font-size:13px; text-transform:uppercase; letter-spacing:.5px; color:#8b95a5; margin:18px 0 8px; }}
  .card {{ background:#161b22; border:1px solid #222; border-radius:12px; padding:10px 12px; margin-bottom:8px; }}
  .row {{ display:flex; justify-content:space-between; align-items:center; gap:8px; margin:2px 0; }}
  .muted {{ color:#8b95a5; }} .small {{ font-size:12px; }}
  .pos {{ color:#3fb950; }} .neg {{ color:#f85149; }}
  .grid {{ display:grid; grid-template-columns:1fr 1fr; gap:8px; }}
  .stat {{ background:#161b22; border:1px solid #222; border-radius:12px; padding:10px 12px; }}
  .stat .k {{ color:#8b95a5; font-size:12px; }} .stat .v {{ font-size:18px; font-weight:600; }}
  .pill {{ font-size:11px; padding:2px 8px; border-radius:999px; background:#21262d; }}
  .conf {{ background:#1f6feb33; color:#58a6ff; }}
  .rec-sell {{ background:#f8514922; color:#f85149; }}
  .rec-reduce {{ background:#d2992222; color:#e3a008; }}
  .rec-monitor {{ background:#bb800922; color:#e3a008; }}
  .rec-hold {{ background:#3fb95022; color:#3fb950; }}
</style></head><body>
<header><h1>📈 {WEBAPP_CONFIG['title']} <span class="muted small">· {DATA_PROVIDER}</span></h1></header>
<div class="wrap">

  <h2>Portfolio</h2>
  <div class="grid">
    <div class="stat"><div class="k">Total Value</div><div class="v">${a['total_value']:,.0f}</div></div>
    <div class="stat"><div class="k">Cash / BP</div><div class="v">${a['cash']:,.0f}</div></div>
    <div class="stat"><div class="k">Day P/L</div><div class="v">{pl(a['day_pl'])}</div></div>
    <div class="stat"><div class="k">Month P/L</div><div class="v">{pl(a['month_pl'])}</div></div>
  </div>
  <div class="muted small" style="margin-top:6px">Open: {a['open_positions']} ({a['open_options']} options) · synced {a['updated_at']}</div>

  <h2>Market Status</h2>
  <div class="card">
    <div class="row"><b class="{mcls}">{m['classification']}</b>
      <span class="muted">sentiment {m['sentiment']:.0f}/100 · VIX {vix} · risk {m['risk']}</span></div>
    <div class="muted small">SPY: {m['spy']}</div>
    <div class="muted small">QQQ: {m['qqq']}</div>
  </div>

  <h2>Open Positions</h2>
  {pos_rows}

  <h2>Watchlist</h2>
  {watch_rows}

  <h2>Recent Alerts</h2>
  {alert_rows}

</div></body></html>"""


# --------------------------------------------------------------------------- #
def create_app():
    if FastAPI is None:
        raise ImportError("fastapi + uvicorn are required for the dashboard")
    app = FastAPI(title=WEBAPP_CONFIG["title"])
    cache = _StateCache(ttl=WEBAPP_CONFIG.get("refresh_seconds", 30) - 5)

    @app.get("/", response_class=HTMLResponse)
    def index():
        return _render_html(cache.get())

    @app.get("/api/state")
    def api_state():
        return JSONResponse(cache.get())

    @app.get("/healthz")
    def health():
        return {"ok": True}

    return app


def main():
    import uvicorn
    uvicorn.run(create_app(), host=WEBAPP_CONFIG["host"], port=WEBAPP_CONFIG["port"])


if __name__ == "__main__":
    main()
