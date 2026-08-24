"""Pluggable trading strategies.

A strategy implements `domain.ports.strategy.IStrategy` and has **zero**
imports from `exchanges/`, `execution/`, or `ccxt` — see
`docs/coding-standards.md` and the Strategy Plugin Contract in
`docs/architecture.md`. Strategies are entirely testable with synthetic
`Bar` sequences and no event bus, exchange, or network access.

Adding a new strategy = a new file under `strategies/examples/` (or any
importable module) implementing `IStrategy`, plus a `module:ClassName`
config entry for `StrategyLoader` — no changes to this package's core
modules (`context.py`, `handler.py`, `loader.py`).
"""

from __future__ import annotations

from trading_platform.strategies.context import DefaultStrategyContext, NullPositionProvider
from trading_platform.strategies.handler import StrategyHandler
from trading_platform.strategies.loader import (
    describe_strategy,
    instantiate_strategy,
    load_strategy_class,
)

__all__ = [
    "DefaultStrategyContext",
    "NullPositionProvider",
    "StrategyHandler",
    "describe_strategy",
    "instantiate_strategy",
    "load_strategy_class",
]
