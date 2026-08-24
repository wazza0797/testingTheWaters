from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol, runtime_checkable

from trading_platform.domain.models.bar import Bar
from trading_platform.domain.models.position import Position
from trading_platform.domain.models.signal import Signal


class StrategyContext(Protocol):
    """Read-only view exposed to strategies: indicator helpers, current positions,
    and config params.

    Deliberately narrow and structural (a `Protocol`, not a concrete class) so
    this module never needs to import `pandas`/`indicators/` — the concrete
    implementation (`strategies/context.py::DefaultStrategyContext`) lives
    outside `domain/` and is free to depend on them. Strategies never receive
    a bar history directly (`IStrategy.on_bar` gets one `Bar` at a time); a
    strategy that needs indicator values accumulates its own bar buffer and
    passes it to `indicator(...)` each call.
    """

    symbol: str
    timeframe: str
    params: Mapping[str, Any]

    def indicator(self, name: str, bars: Sequence[Bar], **kwargs: Any) -> float:
        """Latest value of a named indicator (see `indicators.IndicatorRegistry`)
        computed over `bars`. Returns `NaN` if `bars` doesn't yet hold enough
        history for the requested indicator/period.
        """
        ...

    def position_for(self, symbol: str) -> Position | None:
        """Current position for `symbol`, or `None` if flat/untracked. Backed
        by `IPositionProvider` — see `domain/ports/portfolio.py`.
        """
        ...


@runtime_checkable
class IStrategy(Protocol):
    """A pluggable strategy. Must be testable with synthetic bars and zero
    imports from `exchanges/`, `execution/`, or `ccxt` (see coding standards).

    `@runtime_checkable`: `IStrategy` has methods only (no data attributes),
    so `isinstance(obj, IStrategy)` reliably checks that all three methods
    exist (not their exact signatures — Python can't check that at runtime).
    `strategies/loader.py::instantiate_strategy` uses this to fail fast with
    a clear `StrategyError` for a malformed dynamically-loaded strategy,
    instead of an `AttributeError` surfacing later mid-run inside
    `StrategyHandler`.
    """

    def on_start(self, ctx: StrategyContext) -> None: ...

    def on_bar(self, bar: Bar, ctx: StrategyContext) -> list[Signal]: ...

    def on_stop(self, ctx: StrategyContext) -> None: ...
