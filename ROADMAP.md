# Roadmap — Future Additions

Tracked work for the project. Develop each on its own `feature/*` branch off
`develop`, open a PR, let CI run the tests, then merge.

| # | Feature | Status | Branch |
|---|---|---|---|
| 1 | Auto-run tests before committing (CI + pre-commit hook) | 🛠️ in progress | `feature/ci-tests` |
| 2 | Richer Telegram alerts with charts/images | ⏳ planned | `feature/telegram-charts` |
| 3 | Robinhood position sync (live positions → portfolio_sync.json) | ⏳ planned | `feature/position-sync` |
| 4 | Backtester realism (slippage, commissions, IV premium) | ⏳ planned | `feature/backtest-realism` |
| 5 | Earnings calendar + chart integration | ⏳ planned | `feature/earnings` |

## Notes per item

1. **Auto-run tests** — GitHub Actions (`.github/workflows/tests.yml`) runs
   `pytest` on every push/PR; a local git hook (`scripts/git-hooks/pre-commit`)
   runs them before each commit. Activate the hook with
   `git config core.hooksPath scripts/git-hooks`.
2. **Telegram charts** — render a small price/indicator chart (matplotlib) and
   send via the bot's `send_photo()` so each alert includes a visual.
3. **Position sync** — a script that pulls live positions from Robinhood (via
   Claude/MCP or the API) and writes `portfolio_sync.json` so the briefing and
   dashboard show real balances.
4. **Backtester realism** — add per-contract commission + slippage, and an
   IV-over-realized-vol premium so modeled premiums better match real options.
5. **Earnings** — pull an earnings calendar (Tradier exposes one), flag or skip
   names with earnings inside the trade window, and surface dates in the briefing.
