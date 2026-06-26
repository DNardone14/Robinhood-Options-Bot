# Options Trading Assistant — Notification & Decision-Support Layer

This is the PDR enhancement on top of the core engine: a human-in-the-loop system
that analyzes the market, scores opportunities, and pushes **briefings and
real-time alerts to your phone** — while every trade stays a manual decision you
confirm through Claude's Robinhood MCP. Nothing here auto-trades.

## What you get

| PDR Feature | Where | Notes |
|---|---|---|
| Daily 9am briefing | `briefing/generator.py` | Portfolio, positions+Greeks+reco, market, watchlist, events |
| Real-time alerts | `alerts/engine.py` | BUY / SELL / HOLD-extension / RISK, with the PDR formats |
| Human-in-the-loop | by design | Alerts only; you place orders via Claude → Robinhood |
| Mobile dashboard | `webapp/app.py` | FastAPI, mobile-first, auto-refresh |
| Recommendation engine | `recommendation/engine.py` | 0–100 confidence bands; HOLD/SELL/REDUCE/MONITOR |
| Performance tracking | `performance/tracker.py` | Win rate, avg gain/loss, per-ticker, weekly+monthly |
| Notifications | `notifications/` | Telegram (primary) → SMS → email, with dry-run fallback |
| Scheduling | `scheduler.py` | APScheduler: briefing, alert loop, weekly/monthly reports |
| Data providers | `providers/` | yfinance now; Tradier / Robinhood drop-in later |

## Run it today (no accounts needed)

Everything works on free **yfinance** data and, with no notification credentials,
prints alerts to the console (dry-run) so you can see exactly what would be sent.

```bash
cd options_engine
pip3 install -r requirements.txt --break-system-packages
cp portfolio_sync.example.json portfolio_sync.json    # mock account for the briefing

python3 -m options_engine.assistant --status      # show provider + channels
python3 -m options_engine.assistant --briefing    # build + "send" the morning briefing
python3 -m options_engine.assistant --alerts      # one alert scan
python3 -m options_engine.assistant --dashboard   # http://localhost:8080/
```

## Turn on Telegram (1 minute)

1. In Telegram, message **@BotFather** → `/newbot` → copy the **bot token**.
2. Message your new bot once, then open
   `https://api.telegram.org/bot<token>/getUpdates` and copy your numeric **chat id**.
3. Put both in `.env`:
   ```
   TELEGRAM_BOT_TOKEN=123456:ABC...
   TELEGRAM_CHAT_ID=987654321
   ```
4. `set -a; source .env; set +a` then re-run `--briefing`. It now hits your phone.

SMS (Twilio) and email (SMTP relay) are configured the same way in `.env`; the
dispatcher tries Telegram → SMS → email in order and stops at the first success.

## Production (your DigitalOcean box)

```bash
bash options_engine/start_assistant.sh    # scheduler: briefing + alerts + reports
bash options_engine/start_dashboard.sh    # mobile dashboard on :8080
```

The scheduler fires the briefing at 9:00 AM ET on weekdays, scans for alerts every
60s during market hours, and sends weekly (Fri) + monthly (1st) performance reports.

## Account / portfolio data

yfinance can't see your Robinhood balances/positions, so the briefing, dashboard,
and position alerts read `portfolio_sync.json`. Keep it current by asking Claude to
"refresh my Robinhood positions into portfolio_sync.json" (it pulls
`get_option_positions` / `get_accounts` and writes the file), or edit it by hand.
The schema is in `portfolio_sync.example.json`.

## Switching data source later

When Tradier opens (or you wire the Robinhood cache), change one line in
`config.py`:

```python
DATA_PROVIDER = "tradier"     # or "robinhood"
```

No other code changes — the provider interface is identical.

## Configuration

New config blocks in `config.py`: `DATA_PROVIDER`, `PROVIDER_CONFIG`,
`NOTIFY_CONFIG`, `RECO_CONFIG`, `ALERT_CONFIG`, `SCHEDULE_CONFIG`, `WEBAPP_CONFIG`.
Confidence bands, alert thresholds, schedule times, and the dashboard port all
live there.
```
