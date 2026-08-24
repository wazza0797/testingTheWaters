"""Generic behavioral checks any `IStrategy` implementation should satisfy,
independent of its trading logic — reused across every strategy's own test
file (see `examples/test_sma_crossover.py`) instead of each one reinventing
these checks (or forgetting them). Not prefixed `test_`, so pytest never
collects this file as a test module itself; call `assert_strategy_conforms`
from a real test function.

This does **not** replace testing a strategy's actual signal logic — it only
catches the class of bug that has nothing to do with *which* strategy it is:
crashing lifecycle hooks, non-deterministic output, and signals attributed
to the wrong symbol.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from trading_platform.domain.models.bar import Bar
from trading_platform.domain.models.signal import Signal
from trading_platform.domain.ports.strategy import IStrategy, StrategyContext


def assert_strategy_conforms(
    make_strategy: Callable[[], IStrategy],
    ctx: StrategyContext,
    bars: Sequence[Bar],
) -> None:
    """Run every generic conformance check against fresh strategy instances
    built by `make_strategy` (called once per check, so state from one check
    never leaks into another) over `bars` (should include enough history to
    exercise the strategy's normal signal-producing path, not just the
    empty/insufficient-history case).
    """
    _assert_lifecycle_hooks_do_not_raise_without_any_bars(make_strategy, ctx)
    _assert_handles_a_single_bar_without_raising(make_strategy, ctx, bars)
    _assert_every_signal_matches_its_triggering_bars_symbol(make_strategy, ctx, bars)
    _assert_deterministic(make_strategy, ctx, bars)


def _assert_lifecycle_hooks_do_not_raise_without_any_bars(
    make_strategy: Callable[[], IStrategy], ctx: StrategyContext
) -> None:
    strategy = make_strategy()
    strategy.on_start(ctx)
    strategy.on_stop(ctx)


def _assert_handles_a_single_bar_without_raising(
    make_strategy: Callable[[], IStrategy], ctx: StrategyContext, bars: Sequence[Bar]
) -> None:
    if not bars:
        return
    strategy = make_strategy()
    strategy.on_start(ctx)
    signals = strategy.on_bar(bars[0], ctx)
    assert isinstance(signals, list)


def _assert_every_signal_matches_its_triggering_bars_symbol(
    make_strategy: Callable[[], IStrategy], ctx: StrategyContext, bars: Sequence[Bar]
) -> None:
    strategy = make_strategy()
    strategy.on_start(ctx)
    for bar in bars:
        for signal in strategy.on_bar(bar, ctx):
            assert isinstance(signal, Signal)
            assert signal.symbol == bar.symbol, (
                f"strategy emitted a signal for {signal.symbol!r} while processing "
                f"a bar for {bar.symbol!r}"
            )


def _assert_deterministic(
    make_strategy: Callable[[], IStrategy], ctx: StrategyContext, bars: Sequence[Bar]
) -> None:
    def _run() -> list[tuple[object, ...]]:
        strategy = make_strategy()
        strategy.on_start(ctx)
        recorded: list[tuple[object, ...]] = []
        for bar in bars:
            recorded.extend(
                (signal.signal_type, signal.timestamp, signal.symbol)
                for signal in strategy.on_bar(bar, ctx)
            )
        return recorded

    assert _run() == _run()
