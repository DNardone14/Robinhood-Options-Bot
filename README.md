# Robinhood Options Engine & Trading Assistant

An options day-trading **analysis and alerting** system. It scans a watchlist
with real-time market data, scores opportunities with technical + options
analytics, and pushes a daily briefing and real-time alerts to your phone via
Telegram — while **you remain the only one who places trades**.

> ⚠️ **This system does not trade automatically.** It generates signals, alerts,
> and order *tickets*; every trade is reviewed and placed by a human. Options
> carry a high risk of loss (including 100% of premium). This is decision support,
> not financial advice, and it ships with no warranty. See [`LICENSE`](LICENSE).

---

## Features

- **Real-time data** via Tradier (quotes, full option chains, Greeks, IV), with a
  free yfinance fallback and a pluggable provider interface.
- **Indicators**: EMA 9/21/50, RSI, VWAP, Bollinger Bands, ATR, MACD, volume
  profile; **options metrics**: IV rank/percentile, put/call ratio, vol/OI,
  IV skew, ATM-straddle expected move.
- **Strategies**: Directional Momentum (volume-confirmed EMA breakouts) and
  Unusual Options Flow; optional defined-risk vertical spreads.
- **Recommendation engine**: 0–100 confidence scoring and per-position
  HOLD / SELL / REDUCE / MONITOR calls.
- **Risk management**: premium-at-risk sizing (fixed-fractional or Kelly), daily
  loss auto-shutoff, portfolio Greeks limits.
- **Data-quality guard**: rejects options priced below intrinsic value and other
  stale/garbage quotes before they can become a signal.
- **Notifications**: Telegram (primary), Twilio SMS, and email — with a dry-run
  console mode when no credentials are set.
- **Daily briefing + real-time alerts** (BUY / SELL / HOLD / RISK).
- **Mobile dashboard** (FastAPI), **performance tracking** (win rate, P&L,
  per-ticker, weekly/monthly), and an **APScheduler** service.
- **Backtester** across 60d / 1y / 2y / 5y (Black-Scholes-modeled).

---

## Installation

Requires Python 3.10+.

```bash
git clone https://github.com/<username>/<repo>.git
cd <repo>
pip install -r options_engine/requirements.txt
cp options_engine/.env.example options_engine/.env   # then fill in your keys
```

Fill `options_engine/.env` with your credentials (this file is gitignored):

```
ROBINHOOD_ACCOUNT=...        # your agentic account number
TRADIER_TOKEN=...            # Tradier PRODUCTION token (real-time data + Greeks)
TELEGRAM_BOT_TOKEN=...       # @BotFather -> /newbot
TELEGRAM_CHAT_ID=...         # from https://api.telegram.org/bot<token>/getUpdates
```

Account/portfolio data isn't available from market-data providers, so the
briefing/dashboard read `portfolio_sync.json` (also gitignored). Start from the
template:

```bash
cp options_engine/portfolio_sync.example.json portfolio_sync.json
```

---

## Usage

Run from the directory that **contains** `options_engine/`:

```bash
python -m options_engine.assistant --status      # show provider + channels
python -m options_engine.assistant --briefing    # build + send the morning briefing
python -m options_engine.assistant --alerts      # one alert scan
python -m options_engine.assistant --schedule    # full service (briefing + alerts + reports)
python -m options_engine.assistant --dashboard   # FastAPI dashboard at :8080
python -m options_engine.diagnostics             # verify the live data feed
python -m options_engine.backtest_options --multi SOUN AAL SOFI CIFR HIMS --verbose
python -m options_engine.main                     # core engine rich TUI
```

---

## Deployment (24/7 on a VPS)

Tested on an Ubuntu 24.04 DigitalOcean droplet (512MB + 2GB swap).

```bash
# from your machine:
scp -r options_engine root@DROPLET_IP:/root/optbot/
scp portfolio_sync.json root@DROPLET_IP:/root/optbot/

# on the droplet:
cd /root/optbot
fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab
python3 -m pip install --ignore-installed typing_extensions --break-system-packages
python3 -m pip install -r options_engine/requirements.txt --break-system-packages
nano options_engine/.env        # add ROBINHOOD_ACCOUNT, TRADIER_TOKEN, TELEGRAM_*
bash options_engine/start_assistant.sh
```

The scheduler fires the 9:00 AM ET briefing on weekdays, scans for alerts every
60s during market hours, and sends weekly/monthly performance reports. See
[`docs/`](docs/) and [`PROJECT_RECORD.md`](PROJECT_RECORD.md) for full details.

---

## Architecture

```
options_engine/
├── config.py          settings (provider, watchlist, risk, alerts, schedule)
├── assistant.py       orchestrator: briefing / alerts / reports
├── scheduler.py       APScheduler service (production entry point)
├── analysis.py        provider-backed per-symbol analysis (+ IV history, sanity filter)
├── validation.py      data-quality guard (below-intrinsic / bad-quote rejection)
├── diagnostics.py     live data-provider verification
├── backtest_options.py  Black-Scholes-modeled backtester
├── data/              Tradier client, market calendar
├── providers/         yfinance / tradier / robinhood (pluggable)
├── indicators/        technicals + options metrics (Black-Scholes)
├── strategies/        directional momentum, unusual flow, spreads
├── recommendation/    0–100 confidence + position recommendations
├── risk/              sizing, daily-loss halt, Greeks caps
├── execution/         Robinhood-MCP order-ticket builder (no auto-trade)
├── portfolio/         positions, mark-to-market, exits, Greeks rollup
├── notifications/     telegram / sms / email + dispatcher
├── briefing/ alerts/ performance/   the assistant features
├── webapp/            FastAPI mobile dashboard
└── storage/           SQLite + CSV logging
```

Data flows: **providers → indicators/options-metrics → strategies → recommendation
→ risk → order ticket**, with notifications and the dashboard reading the results.
Execution is human-in-the-loop: the bot proposes; you confirm and place via
Claude's Robinhood connector.

---

## Disclaimer

This project **never executes trades automatically**. It is an analysis and
notification tool. Nothing here is financial advice. Options trading can result
in the rapid and total loss of capital. You are solely responsible for any order
you choose to place. Provided "as is" with no warranty — see [`LICENSE`](LICENSE).
