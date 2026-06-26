# Architecture

## Layers

| Layer | Modules | Responsibility |
|---|---|---|
| **Data providers** | `providers/`, `data/tradier.py` | Normalize quotes, chains (+Greeks/IV), and bars into one interface. yfinance / Tradier / Robinhood are interchangeable via `DATA_PROVIDER`. |
| **Indicators** | `indicators/technicals.py`, `indicators/options_metrics.py` | Pure-pandas technicals; Black-Scholes Greeks/IV and chain analytics (IV rank, P/C, skew, expected move). |
| **Validation** | `validation.py` | Drops below-intrinsic / broken-quote contracts before strategies run. |
| **Strategies** | `strategies/` | Directional Momentum, Unusual Flow, optional verticals → `Signal` objects. |
| **Recommendation** | `recommendation/engine.py` | 0–100 confidence; HOLD/SELL/REDUCE/MONITOR; market regime read. |
| **Risk** | `risk/manager.py` | Sizing (fixed-fractional / Kelly), daily-loss halt, portfolio Greeks caps. |
| **Execution** | `execution/robinhood_router.py` | Builds Robinhood-MCP order **tickets**. Never auto-trades. |
| **Assistant** | `analysis.py`, `briefing/`, `alerts/`, `performance/`, `scheduler.py`, `assistant.py` | Briefing, real-time alerts, reports, scheduling. |
| **Delivery** | `notifications/`, `webapp/`, `storage/` | Telegram/SMS/email, FastAPI dashboard, SQLite+CSV logging. |

## Data flow (one scan)

```
provider.get_quote / get_chains / get_bars
        │
        ▼
validation.filter_chain      ← drops stale / below-intrinsic contracts
        │
        ▼
technicals + options_metrics ← indicators, IV rank (daily), expected move
        │
        ▼
strategies (DM / UF / spreads) → Signal
        │
        ▼
recommendation.score_setup    ← 0–100 confidence, risk rating
        │
        ▼
risk.manager                  ← size, Greeks/daily-loss checks
        │
        ▼
execution.robinhood_router    ← order ticket (human places it)
        │
        ├─► alerts.engine → notifications → Telegram
        └─► storage (SQLite + CSV) → performance + dashboard
```

## Key design choices

- **Human-in-the-loop** — the engine proposes; a person confirms and places every
  order. No path auto-executes.
- **Pluggable data** — swapping `DATA_PROVIDER` requires no other code changes.
- **Account state is external** — providers can't see broker balances, so portfolio
  data comes from `portfolio_sync.json` (refreshed from Robinhood via Claude).
- **Fail safe on bad data** — the validation guard and daily IV-history gating mean
  a stale quote can't become a tradeable alert.

## Configuration

Everything is in `config.py`: `DATA_PROVIDER`, `ENGINE_CONFIG` (watchlist, scan
interval), `STRATEGY_CONFIG`, `RISK_CONFIG`, `RECO_CONFIG`, `VALIDATION_CONFIG`,
`ALERT_CONFIG`, `SCHEDULE_CONFIG`, `NOTIFY_CONFIG`, `WEBAPP_CONFIG`. Secrets are
read from environment variables, never hardcoded.
