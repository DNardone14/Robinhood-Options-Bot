"""
Pluggable market-data + portfolio providers.

get_provider() returns the market-data provider named in config.DATA_PROVIDER
("yfinance" today; "tradier"/"robinhood" later) — all expose the same interface,
so the assistant, briefing, and alert engine never change when you swap sources.

PortfolioProvider reads broker account state (cash, positions) from a local sync
file, since account data isn't available from yfinance. Claude refreshes that file
from your Robinhood agentic account; a mock file lets everything run today.
"""

from .base import MarketDataProvider, PortfolioProvider, AccountSnapshot, PositionRow


def get_provider(name: str, config: dict | None = None) -> "MarketDataProvider":
    name = (name or "yfinance").lower()
    if name == "yfinance":
        from .yfinance_provider import YFinanceProvider
        return YFinanceProvider()
    if name == "tradier":
        from .tradier_provider import TradierProvider
        return TradierProvider(config or {})
    if name == "robinhood":
        from .robinhood_provider import RobinhoodProvider
        return RobinhoodProvider(config or {})
    raise ValueError(f"unknown DATA_PROVIDER: {name}")


__all__ = [
    "get_provider",
    "MarketDataProvider",
    "PortfolioProvider",
    "AccountSnapshot",
    "PositionRow",
]
