"""Domain-level exceptions.

Infrastructure code should catch third-party exceptions (ccxt, pyarrow, etc.)
at the adapter boundary and re-raise as one of these so the rest of the
application never depends on a third-party exception type.
"""

from __future__ import annotations


class TradingPlatformError(Exception):
    """Base class for all domain exceptions."""


class ValidationError(TradingPlatformError):
    """Raised when a domain model fails invariant checks."""


class OrderValidationError(TradingPlatformError):
    """Raised when an order fails exchange precision/size rules."""


class ExchangeAdapterError(TradingPlatformError):
    """Raised when an exchange adapter call fails (network, API, mapping)."""


class MarketDataError(TradingPlatformError):
    """Raised when market data cannot be read or written."""


class ConfigurationError(TradingPlatformError):
    """Raised when configuration is missing or invalid."""


class StrategyError(TradingPlatformError):
    """Raised when a strategy plugin fails to load or execute."""


class PortfolioError(TradingPlatformError):
    """Raised when a ledger/portfolio invariant is violated — e.g. a fill
    tries to sell more quantity than is currently held. Indicates a bug
    upstream (risk sizing, order queue, or fill simulator), not a user-facing
    configuration problem.
    """
