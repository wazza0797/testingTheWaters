from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from trading_platform.domain.models.bar import Bar
from trading_platform.domain.models.position import Position
from trading_platform.domain.ports.portfolio import IPositionProvider
from trading_platform.indicators import (
    IndicatorRegistry,
    build_default_registry,
    closes_from_bars,
    ohlc_from_bars,
)


class NullPositionProvider:
    """Stub `IPositionProvider`: every symbol is always reported flat (`None`).

    The default for `DefaultStrategyContext` until Milestone 5's
    `PortfolioHandler` tracks real positions from fills — construction sites
    just pass a different `IPositionProvider` once that lands, no strategy
    changes required.
    """

    def position_for(self, symbol: str) -> Position | None:
        return None


@dataclass(frozen=True, slots=True)
class DefaultStrategyContext:
    """Concrete `StrategyContext`: indicator helpers backed by
    `indicators.IndicatorRegistry`, positions backed by an injected
    `IPositionProvider` (flat/stubbed until M5), and strategy-specific
    config params sourced from `config/*.yaml`.
    """

    symbol: str
    timeframe: str
    params: Mapping[str, Any] = field(default_factory=dict)
    position_provider: IPositionProvider = field(default_factory=NullPositionProvider)
    registry: IndicatorRegistry = field(default_factory=build_default_registry)

    def indicator(self, name: str, bars: Sequence[Bar], **kwargs: Any) -> float:
        if not bars:
            return float("nan")
        closes = closes_from_bars(bars)
        if name == "atr":
            high, low, _close = ohlc_from_bars(bars)
            series = self.registry.compute(name, closes, high=high, low=low, **kwargs)
        else:
            series = self.registry.compute(name, closes, **kwargs)
        return float(series.iloc[-1])

    def position_for(self, symbol: str) -> Position | None:
        return self.position_provider.position_for(symbol)
