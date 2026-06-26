# Options Day-Trading Strategy Engine

A standalone Python engine that scans underlyings, builds options trade signals
from technical + options-flow analysis, sizes them under hard risk limits, and
hands you ready-to-execute order tickets for your **Robinhood agentic account**
(`644037566`) via Claude's MCP connector. It mirrors the human-in-the-loop flow
of your existing equity tradebot — **it never places an order by itself.**

> ⚠️ **Risk notice.** Options day trading carries a high risk of rapid, total
> loss of the premium paid. This software is a research/automation tool, not
> financial advice, and ships with no warranty. Paper-trade and review the logged
> signals for several sessions before risking real money. You are responsible for
> every order you confirm in Claude.

---

## What it does

- **Data (Tradier):** live quotes, full option chains with Greeks/IV, daily +
  intraday OHLCV bars. Falls back to yfinance for bars and to a Black-Scholes
  calculation for Greeks when the feed omits them.
- **Indicators:** EMA 9/21/50, RSI(14, Wilder), VWAP, Bollinger Bands, ATR,
  MACD, and a volume profile (POC / value area).
- **Options metrics:** IV rank & percentile, put/call ratio, volume-to-OI,
  IV skew, and the ATM-straddle expected move.
- **Strategies:** Directional Momentum (volume-confirmed EMA breakouts, strike by
  delta, DTE by expected move) and Unusual Options Flow (vol/OI spikes, large
  notional, IV expansion). Optional vertical debit/credit **spread mode** caps
  risk and trims vega.
- **Filters:** market-hours-only, min volume/OI, max bid-ask spread, and an
  earnings filter (skip to dodge IV crush, or flag for IV-crush plays).
- **Risk:** premium-at-risk per trade (default 1.5%), fixed-fractional or
  fractional-Kelly sizing, daily-loss auto-shutoff, and portfolio net
  delta/theta/vega caps.
- **Execution:** writes a JSON + plain-English order ticket per approved signal
  and surfaces it on the dashboard; you execute through Claude
  (`review_option_order` → confirm → `place_option_order`).
- **Output:** a live `rich` terminal dashboard, plus every signal and trade
  logged to SQLite **and** CSV for backtest review.

---

## Install

```bash
cd options_engine
pip3 install -r requirements.txt --break-system-packages   # Ubuntu 24.04 note from your tradebot
cp .env.example .env        # then edit .env and paste your Tradier token
```

Get a Tradier token at <https://developer.tradier.com> (a brokerage account gives
you a production market-data token with Greeks). Export it before running:

```bash
set -a; source .env; set +a
```

---

## Run

From the directory **containing** `options_engine/` (so `-m` resolves the package):

```bash
python3 -m options_engine.main                # live terminal dashboard + 60s loop
python3 -m options_engine.main --once         # one scan, print text summary, exit
python3 -m options_engine.main --symbol NVDA  # scan a single symbol once
python3 -m options_engine.main --no-dashboard # loop, plain stdout logging (for servers)
```

On your DigitalOcean box, run it detached like your equity bot:

```bash
bash options_engine/start.sh     # background, logs to options_engine/engine.log
bash options_engine/status.sh    # running? + last 20 log lines
bash options_engine/stop.sh
```

---

## The trade workflow

1. Engine detects a signal, sizes it, and writes `tickets/SYMBOL_<ts>.json`.
2. The dashboard's **Active Signals** panel shows contract, premium, target, stop,
   size, and confidence; the ticket's `claude_instruction` is a copy-paste line.
3. In Claude chat (agentic Robinhood MCP connected) say, e.g.:
   *"Review and place an options order — BUY 1 NVDA 2026-06-26 130 CALL at $2.45
   limit on account 644037566."*
4. Claude runs `review_option_order`, shows the fill preview, you confirm, then it
   calls `place_option_order`. Multi-leg spreads route the same way as one ticket.
5. Open positions are marked every scan; when a stop/target/expiry-risk triggers,
   the engine emits a **close intent** ticket the same way.

---

## Configuration

Everything lives in `config.py` (grouped: Tradier, engine/universe, strategy,
risk, execution, storage). Common knobs:

| Want to… | Setting | Group |
|---|---|---|
| Change tickers | `watchlist` | `ENGINE_CONFIG` |
| Scan frequency | `scan_interval` | `ENGINE_CONFIG` |
| Target strike delta / band | `dm_target_delta`, `dm_delta_band` | `STRATEGY_CONFIG` |
| DTE window | `dm_min_dte` / `dm_max_dte` | `STRATEGY_CONFIG` |
| Turn on spreads | `spread_enabled`, `spread_type`, `spread_width` | `STRATEGY_CONFIG` |
| Earnings behavior | `skip_earnings_within_days`, `earnings_iv_crush_mode` | `STRATEGY_CONFIG` |
| Account size / per-trade risk | `account_size`, `risk_per_trade_pct` | `RISK_CONFIG` |
| Sizing method | `sizing_method` (`fixed_fractional`/`kelly`) | `RISK_CONFIG` |
| Portfolio Greeks caps | `max_net_delta/theta/vega` | `RISK_CONFIG` |
| Daily loss auto-stop | `max_daily_loss_pct` | `RISK_CONFIG` |

### Small-account caveat
Options trade in 100-share contracts, so one ATM contract on a $100+ stock is
often $150–$500 of premium. With a small account and a strict 1–2% per-trade cap,
the sizer will correctly return **zero contracts** — it won't breach your risk
limit. Options: raise `account_size`, trade cheaper underlyings (e.g. SPY/QQQ
further OTM), or set `allow_min_one_contract: True` (permits a single contract
when its premium fits `max_position_pct`). Use the override deliberately.

---

## Layout

```
options_engine/
├── config.py                 # all settings
├── main.py                   # CLI entry: scan loop + dashboard
├── engine.py                 # orchestrator: data → indicators → strategy → risk → tickets
├── data/
│   ├── tradier.py            # Tradier market-data client
│   └── market_calendar.py    # NYSE hours + earnings dates
├── indicators/
│   ├── technicals.py         # EMA/RSI/VWAP/BBands/ATR/MACD/volume profile
│   └── options_metrics.py    # Black-Scholes Greeks/IV + chain analytics
├── strategies/
│   ├── base.py               # Signal/Leg types + delta-based selection
│   ├── directional_momentum.py
│   ├── unusual_flow.py
│   └── spreads.py            # vertical debit/credit builder
├── risk/manager.py           # sizing, daily-loss halt, Greeks caps
├── execution/robinhood_router.py   # builds Robinhood-MCP order tickets
├── portfolio/tracker.py      # positions, mark-to-market, exits, Greeks rollup
├── storage/db.py             # SQLite + CSV logging
└── dashboard/tui.py          # rich terminal dashboard
```

---

## Verification

A smoke test exercises the math and the full pipeline on mock data. Black-Scholes
matches textbook values (ATM call $10.4506, Δ 0.6368; put-call parity exact; IV
solver round-trips 0.20), indicators and chain analytics check out, and the
strategy → risk → ticket → portfolio → storage path runs end-to-end. To re-run
against live data, point it at a symbol with your Tradier token set:

```bash
python3 -m options_engine.main --symbol SPY
```

---

## Notes / limitations

- Tradier sandbox tokens return delayed data **without** Greeks — use a
  production token, or rely on the Black-Scholes fallback (less accurate).
- "Large-block sweep" detection is approximated from chain volume/OI and IV
  (no live time-and-sales tape), so treat unusual-flow signals as a screen, not
  proof of institutional intent.
- Position state is in-memory; restarting the loop clears open paper positions
  (IV-rank history is persisted to `iv_history.json`). Wire in broker-position
  sync if you want durable tracking.
- Robinhood's agentic options order field names are adapted by Claude at call
  time; the ticket's `mcp_call` block is a best-effort payload, not a hard schema.
```
