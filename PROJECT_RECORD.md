# Project Record — Robinhood Options Engine & Trading Assistant

A complete record of the system: an options day-trading **analysis + alerting**
engine that runs 24/7 on a VPS, pulls real-time data from Tradier, scores
opportunities, and pushes briefings and alerts to your phone via Telegram — while
**you stay the only one who places trades**.

> ⚠️ This system never trades on its own. Options can lose 100% of premium
> quickly. This is decision support, not financial advice; no warranty.

---

## 1. Architecture

Two layers, both in the `options_engine/` package:

1. **Core engine** — data, indicators, options math, strategies, risk sizing, and
   a Robinhood order-*ticket* generator.
2. **Assistant layer** — daily briefing, real-time alerts, recommendation/confidence
   engine, FastAPI dashboard, performance tracking, and a scheduler — to Telegram.

Execution stays on **Robinhood** (agentic account, read from `ROBINHOOD_ACCOUNT`);
market data from **Tradier**; notifications to **Telegram**.

```
VPS (24/7) ─ APScheduler service
   ├── 09:00 ET weekdays ─► Morning briefing ─► Telegram
   ├── every 60s (market hours) ─► Alert scan ─► Telegram (BUY/SELL/HOLD/RISK)
   ├── Fri 16:05 ─► Weekly report ─► Telegram
   └── 1st 09:10 ─► Monthly report ─► Telegram
Phone shows the proposed trade → you open Claude →
   review_option_order (preview) → you confirm → place_option_order → Robinhood
```

Data flow: **providers → indicators/options-metrics → strategies → recommendation
→ risk → order ticket**. See `README.md` for the full file map.

---

## 2. Strategies

- **Directional Momentum** — buys calls/puts on volume-confirmed EMA breakouts
  (EMA 9/21/50 aligned, RSI on the right side of 50, MACD confirming, volume ≥
  1.5× its 20-day average). Strike by target delta (~0.40); DTE scaled to expected
  move.
- **Unusual Options Flow** — volume ≫ open interest, large day's notional, and IV
  expansion as directional signals.
- **Optional vertical spreads** — defined-risk debit/credit verticals (off by
  default; requires options Level 3).

Filters before any signal: market-hours-only, min volume/OI, max bid-ask spread,
earnings avoidance, and the data-quality sanity guard.

---

## 3. Risk management

- Premium-at-risk per trade (default 1.5%), fixed-fractional or fractional-Kelly.
- Daily-loss auto-shutoff (default 5%).
- Portfolio net delta / theta / vega caps.
- Per-trade take-profit (+50%) / stop-loss (−30%) on premium.
- **Small-account reality:** one contract is often $100–$500; on a tiny account the
  sizer correctly returns zero contracts rather than break the risk limit.

---

## 4. Data-quality incident & fix

A BUY alert fired for **AMD 525C @ ~$5.33** while AMD traded near **$548** — a
contract priced **below its intrinsic value** (~$23), i.e. a stale/garbage quote,
with `IVrank 0` showing IV history hadn't built. It was **not** traded. Fixes:

1. **Sanity guard** (`validation.py`) — drops options priced below intrinsic,
   missing two-sided quotes, or crossed, *before* strategies see them.
2. **IV-rank fixed** (`analysis.py`) — records one IV reading per calendar day;
   IV-rank stays "unknown" until 20 distinct days accumulate, and BUY alerts are
   suppressed until then.
3. **Data verification** (`diagnostics.py`) — confirms the feed is fresh and sane
   and flags below-intrinsic/stale contracts.

---

## 5. Backtester

`backtest_options.py` tests Directional Momentum over 60d / 1y / 2y / 5y windows
(trades, win rate, net P&L, profit factor, avg win/loss, max drawdown, hold time).

**Modeling caveat:** free historical *option chains* don't exist, so it uses real
historical *underlying* prices and **models premiums with Black-Scholes** (trailing
realized vol as the IV input). It validates the signal/entry logic and realistic
convexity + theta — but not real spreads, IV skew, or event vol-crush. A strategy
sanity check, not a P&L promise. Unusual-Flow isn't backtestable (needs historical
options volume).

```bash
python3 -m options_engine.backtest_options --multi SOUN AAL SOFI CIFR HIMS --verbose
```

---

## 6. Deployment

- **Tradier** — funded Brokerage account → Production token (real-time + Greeks).
- **Telegram** — @BotFather `/newbot` → token; message bot → `getUpdates` → chat id.
- **Robinhood** — agentic account `<YOUR_AGENTIC_ACCOUNT>` (set via `ROBINHOOD_ACCOUNT`
  in `.env`), Options Level 2 (long calls/puts; spreads need Level 3).
- **VPS** — Ubuntu 24.04, 512MB **+ 2GB swap** (needed to install pandas/scipy).
  Installing deps: use `pip --ignore-installed typing_extensions` to dodge the
  Debian conflict. Scheduler fires 9am ET regardless of server timezone; survives
  SSH logout via `nohup` but **not** reboot (boot persistence is an open item).

Secrets live in `options_engine/.env` (gitignored). Portfolio state lives in
`portfolio_sync.json` (gitignored); refresh it from Robinhood via Claude or by hand.

---

## 7. Lessons learned

- A signal built on stale data is worse than no signal — guard below-intrinsic
  quotes and immature IV history.
- IV-rank must be built from one reading per **day**, not per scan.
- 512MB VPS needs **swap** to install scientific Python; pip's Debian
  `typing_extensions` conflict needs `--ignore-installed`.
- Run commands on the right machine (`C:\…>` = PC, `root@ubuntu…` = VPS); module
  commands run from the folder that *contains* `options_engine/`.
- Never commit secrets or account numbers — read them from env vars.
- Keep a human on every trade.

---

## 8. Future improvements

1. Run the backtest on the live watchlist and validate the edge before trading.
2. Boot persistence (systemd or `@reboot` cron) so the scheduler restarts on reboot.
3. Let IV history mature (~20 trading days) before trusting BUY alerts.
4. Fund the account so one contract is a sane fraction of it.
5. Confirm the `review_option_order` → `place_option_order` execution path.
6. Optional: Options Level 3 for defined-risk vertical spreads.
7. Add a `develop` branch and feature/bugfix branch workflow.

---

*Generated from the build session. Last updated: June 2026.*
