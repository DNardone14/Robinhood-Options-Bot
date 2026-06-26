"""
config.py — all settings for the options day-trading engine + assistant layer.

Edit this file to change behavior. Nothing here places a live order on its own:
execution is SIGNALS-ONLY and routes through your Robinhood agentic MCP with a
human confirmation step (see execution/robinhood_router.py).

Secrets (Tradier token) are read from environment variables so you never commit
them. On your DigitalOcean box add them to /root/tradebot/.env or export them:

    export TRADIER_TOKEN="xxxxxxxx"
    export TRADIER_ACCOUNT_ID="VA00000000"   # optional, only for account reads
"""

import os

# --------------------------------------------------------------------------- #
#  Data layer — Tradier
# --------------------------------------------------------------------------- #
TRADIER_CONFIG = {
    # Use the production endpoint for real market data + Greeks.
    # The sandbox endpoint (https://sandbox.tradier.com) returns delayed/synthetic
    # data and does NOT include Greeks on the chain.
    "base_url":   os.environ.get("TRADIER_BASE_URL", "https://api.tradier.com/v1"),
    "token":      os.environ.get("TRADIER_TOKEN", ""),
    "account_id": os.environ.get("TRADIER_ACCOUNT_ID", ""),
    "timeout":    10,          # seconds per HTTP request
    # If Tradier returns no Greeks (sandbox / illiquid), compute them locally
    # with Black-Scholes instead of dropping the contract.
    "compute_greeks_fallback": True,
    "risk_free_rate": 0.043,   # annualized, used by the BS fallback
}

# --------------------------------------------------------------------------- #
#  Universe + loop
# --------------------------------------------------------------------------- #
ENGINE_CONFIG = {
    # Lower-priced, optionable names with upside (cheaper underlying = cheaper
    # premiums for a small account). SOUN/RDW/AAL/SOFI are the most affordable;
    # IREN/LASR are pricier; ALOY/RDW/LASR have THIN options the liquidity filter
    # will often skip (that's protective, not a bug).
    "watchlist": ["SOUN", "RDW", "AAL", "SOFI", "CIFR", "HIMS", "IREN", "LASR", "ALOY"],
    "scan_interval": 60,        # seconds between scans
    "market_hours_only": True,  # skip scans outside 9:30–16:00 ET
    "timezone": "America/New_York",
    # How many days of daily bars to pull for indicator + IV-rank history.
    "history_days": 252,
    # Intraday bar interval for VWAP / intraday technicals: "1min","5min","15min"
    "intraday_interval": "5min",
}

# --------------------------------------------------------------------------- #
#  Strategy parameters
# --------------------------------------------------------------------------- #
STRATEGY_CONFIG = {
    # ---- technical indicator periods ----
    "ema_fast": 9,
    "ema_mid": 21,
    "ema_slow": 50,
    "rsi_period": 14,
    "bb_period": 20,
    "bb_std": 2.0,
    "atr_period": 14,
    "macd_fast": 12,
    "macd_slow": 26,
    "macd_signal": 9,

    # ---- Directional Momentum ----
    "dm_enabled": True,
    "dm_rsi_long_min": 50,      # RSI must be > this for a call breakout
    "dm_rsi_short_max": 50,     # RSI must be < this for a put breakout
    "dm_volume_mult": 1.5,      # current vol vs 20-bar avg to confirm breakout
    "dm_target_delta": 0.40,    # pick the contract closest to this |delta|
    "dm_delta_band": (0.30, 0.55),
    "dm_min_dte": 1,            # day-trading: allow 0–1 DTE up to ~2 weeks
    "dm_max_dte": 14,

    # ---- Unusual Options Flow ----
    "uf_enabled": True,
    "uf_vol_oi_ratio": 2.0,     # contract volume / open interest threshold
    "uf_min_premium": 25_000,   # $ notional of the day's volume (vol*price*100)
    "uf_iv_expansion": 0.05,    # IV up >5 pts vs underlying's recent IV baseline
    "uf_min_dte": 0,
    "uf_max_dte": 30,

    # ---- Optional vertical spread mode (defined risk) ----
    "spread_enabled": False,    # turn on to convert directional signals into verticals
    "spread_type": "debit",     # "debit" or "credit"
    "spread_width": 5.0,        # dollars between long/short strikes

    # ---- liquidity / sanity filters ----
    "min_option_volume": 100,
    "min_open_interest": 250,
    "max_bid_ask_spread_pct": 0.10,   # (ask-bid)/mid must be <= 10%
    "skip_earnings_within_days": 2,   # avoid IV crush; set <0 to flag-only
    "earnings_iv_crush_mode": False,  # if True, FLAG earnings names instead of skipping
}

# --------------------------------------------------------------------------- #
#  Risk management
# --------------------------------------------------------------------------- #
RISK_CONFIG = {
    # NOTE on small accounts: options trade in 100-share contracts, so a single
    # ATM contract on a $100+ stock often costs $150–$500 of premium. With a tiny
    # account and a strict 1–2% per-trade cap, the sizer will correctly return
    # ZERO contracts (you can't honor the risk limit). Either raise account_size,
    # trade cheaper underlyings (SPY/QQQ far-OTM), or set allow_min_one_contract.
    "account_size": 10_000.0,       # total options buying power you allocate
    "risk_per_trade_pct": 1.5,      # % of account risked as premium per trade
    "allow_min_one_contract": False,  # if True, allow 1 contract when its premium
                                      # fits max_position_pct even if it exceeds the
                                      # per-trade % cap (use with care on small accts)
    "max_position_pct": 20.0,       # single position premium <= % of account
    "max_daily_loss_pct": 5.0,      # halt new entries if down this % on the day
    "max_open_positions": 5,
    "sizing_method": "fixed_fractional",  # "fixed_fractional" or "kelly"
    "kelly_fraction": 0.5,          # use half-Kelly when sizing_method == "kelly"
    "kelly_win_rate": 0.55,         # historical win rate estimate for Kelly
    "kelly_win_loss_ratio": 1.6,    # avg win / avg loss for Kelly

    # default per-trade exit math (premium-based)
    "take_profit_pct": 50.0,        # close at +50% on the option premium
    "stop_loss_pct": 30.0,          # close at -30% on the option premium

    # portfolio-level Greeks limits (per 1 share of underlying)
    "max_net_delta": 150.0,         # sum of (delta * contracts * 100) across book
    "max_net_theta": -75.0,         # most negative daily theta you'll tolerate ($)
    "max_net_vega": 300.0,          # net vega exposure cap
}

# --------------------------------------------------------------------------- #
#  Execution — Robinhood agentic MCP (signals only, human-in-loop)
# --------------------------------------------------------------------------- #
EXECUTION_CONFIG = {
    "mode": "signal_only",          # "signal_only" emits tickets; never auto-trades
    # Read from env so your account number is never committed to the repo.
    # Set ROBINHOOD_ACCOUNT in your .env (kept out of git via .gitignore).
    "robinhood_account": os.environ.get("ROBINHOOD_ACCOUNT", ""),
    "default_order_type": "limit",
    "limit_buffer_pct": 0.02,       # bid/ask buffer when proposing a limit price
    # Where to drop human-readable order tickets you paste into Claude chat.
    "ticket_dir": "tickets",
}

# --------------------------------------------------------------------------- #
#  Storage / logging
# --------------------------------------------------------------------------- #
STORAGE_CONFIG = {
    "db_path": "engine.db",
    "signals_csv": "signals_log.csv",
    "trades_csv": "trades_log.csv",
}

# --------------------------------------------------------------------------- #
#  Assistant / notification enhancement (PDR)
# --------------------------------------------------------------------------- #

# Which market-data provider powers quotes/chains/bars. "yfinance" works today
# with no account; swap to "tradier" once your token is live, or "robinhood"
# when the MCP path is wired. Account/portfolio data always comes from the
# portfolio sync file (broker accounts aren't reachable from yfinance).
DATA_PROVIDER = "tradier"           # "yfinance" | "tradier" | "robinhood"

PROVIDER_CONFIG = {
    # path the bot reads for live account state (cash, buying power, positions).
    # Claude (or you) refresh this from Robinhood; a mock file lets the briefing
    # run today. See portfolio_sync.example.json.
    "portfolio_sync_file": "portfolio_sync.json",
    "market_index_symbols": {"spy": "SPY", "qqq": "QQQ", "vix": "VIX"},
}

# Notification channels. Secrets come from env vars so nothing is committed.
# Telegram is primary (PDR). If a channel's creds are missing it runs in
# dry-run mode (prints to console / log) so everything is testable now.
NOTIFY_CONFIG = {
    "priority": ["telegram", "sms", "email"],   # delivery order; first success wins
    "telegram": {
        "bot_token": os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        "chat_id":   os.environ.get("TELEGRAM_CHAT_ID", ""),
    },
    "sms": {  # Twilio — reuse your equity bot's account
        "account_sid": os.environ.get("TWILIO_ACCOUNT_SID", ""),
        "auth_token":  os.environ.get("TWILIO_AUTH_TOKEN", ""),
        "from_number": os.environ.get("TWILIO_FROM", ""),
        "to_number":   os.environ.get("ALERT_PHONE", ""),
    },
    "email": {  # needs an SMTP relay (DigitalOcean blocks 25/465/587 directly)
        "smtp_host": os.environ.get("SMTP_HOST", "smtp.sendgrid.net"),
        "smtp_port": int(os.environ.get("SMTP_PORT", "587")),
        "username":  os.environ.get("SMTP_USER", ""),
        "password":  os.environ.get("SMTP_PASS", ""),
        "from_addr": os.environ.get("EMAIL_FROM", ""),
        "to_addr":   os.environ.get("EMAIL_TO", ""),
    },
}

# Recommendation / confidence thresholds (0–100 scale, PDR bands).
RECO_CONFIG = {
    "very_high": 90, "high": 75, "moderate": 60,   # band floors
    "buy_alert_min_confidence": 70,    # don't fire BUY alerts below this
    "reduce_gain_pct": 35.0,           # take-some level on a winner
    "monitor_dte": 2,                  # flag MONITOR CLOSELY within N DTE
    "theta_burn_pct_of_premium": 3.0,  # daily theta > this % of premium = risk
    # IV-rank needs a history of DAILY IV readings to be meaningful. Until this
    # many distinct trading days have accumulated, IV-rank is treated as unknown
    # and (optionally) BUY alerts are suppressed so you don't trade on day-one noise.
    "min_iv_history_days": 20,
    "require_iv_history_for_alerts": True,
}

# Data-quality guards applied to every option chain before strategies see it.
# The below-intrinsic rule is what would have blocked the bad AMD 525C @ $5.33.
VALIDATION_CONFIG = {
    "reject_below_intrinsic": True,
    "intrinsic_tolerance": 0.02,       # tolerate mid >= intrinsic*(1-tol) for rounding
    "require_two_sided_quote": True,   # need real bid AND ask
}

# Alert engine behavior.
ALERT_CONFIG = {
    "scan_interval": 60,               # seconds between intraday alert scans
    "dedupe_per_day": True,            # one alert per (symbol,kind) per day
    "trend_reversal_rsi": 70,          # exit hint when long call RSI > this then rolls
}

# Daily schedule (ET). APScheduler cron fields.
SCHEDULE_CONFIG = {
    "briefing_hour": 9, "briefing_minute": 0,      # 9:00 AM ET pre-market briefing
    "weekly_report_day": "fri", "weekly_report_hour": 16,
    "monthly_report_day": 1, "monthly_report_hour": 9,
    "timezone": "America/New_York",
}

# FastAPI dashboard.
WEBAPP_CONFIG = {
    "host": "0.0.0.0",
    "port": int(os.environ.get("DASHBOARD_PORT", "8080")),
    "title": "Options Assistant",
    "refresh_seconds": 30,             # browser auto-refresh
}
