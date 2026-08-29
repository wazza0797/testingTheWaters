from __future__ import annotations

from trading_platform.domain.errors import ConfigurationError


def split_symbol(symbol: str) -> tuple[str, str]:
    """Split a unified `BASE/QUOTE` symbol into `(base, quote)`."""
    if "/" not in symbol:
        raise ConfigurationError(f"Expected unified symbol like 'BTC/USDT', got {symbol!r}")
    base, quote = symbol.split("/", 1)
    if not base or not quote:
        raise ConfigurationError(f"Invalid symbol {symbol!r}")
    return base, quote
